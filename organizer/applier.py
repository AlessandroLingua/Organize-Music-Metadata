"""Applicazione del piano: scrive i tag, rinomina/sposta i file, gestisce le
collisioni, regenera le playlist e tiene un diario per annullare tutto.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

from . import metadata as md
from .planner import Plan, PlanItem

JOURNAL_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "OrganizeMusicMetadata"
JOURNAL_PATH = JOURNAL_DIR / "undo.json"

_COVER_NAMES = ("cover.jpg", "cover.png", "folder.jpg", "folder.png", "front.jpg", "front.png")


def apply(plan: Plan, *, progress=None, cancel_event=None) -> Path:
    """Applica tutte le modifiche del piano e restituisce il percorso del diario."""
    items = [it for it in plan.items if it.needs_change]
    journal_entries: list[dict] = []
    done = 0
    total = len(items)

    # 1) copertine: una per cartella album
    cover_bytes: dict[Path, bytes] = {}
    cover_created: set[Path] = set()
    for folder, url in plan.cover_jobs.items():
        if cancel_event is not None and cancel_event.is_set():
            break
        existing = md.find_cover_file(folder)
        if existing:
            data = existing.read_bytes()
        else:
            from .lookup import LookupClient
            data = LookupClient().fetch_cover(url) if plan.items else None
            if data:
                ext = ".png" if url.lower().endswith(".png") else ".jpg"
                target = folder / f"cover{ext}"
                try:
                    target.write_bytes(data)
                    cover_created.add(target)
                except OSError:
                    data = None
        if data:
            cover_bytes[folder] = data

    # 2) per ogni file: scrivi tag, poi rinomina/sposta
    for item in items:
        if cancel_event is not None and cancel_event.is_set():
            break
        done += 1
        if progress:
            progress(done, total, f"Modifica: {item.path.name}")

        entry: dict = {
            "original_path": str(item.path),
            "new_path": str(item.new_path),
            "original_tags": {k: v for k, v in item.original.items() if v is not None},
            "had_cover": md.has_embedded_cover(item.path),
            "had_disc_number": "disc" not in {k for k in item.proposed},
        }
        try:
            md.write_tags(item.path, item.proposed)
        except Exception as exc:
            entry["error"] = f"tag: {exc}"

        # incorpora la copertina scaricata se il file non ne aveva una
        folder_cover = cover_bytes.get(item.path.parent)
        if item.cover_needed and folder_cover and not entry["had_cover"]:
            try:
                md.embed_cover(item.path, folder_cover)
                entry["cover_added"] = True
            except Exception:
                pass  # formato non supportato (ogg/wma): la copertina resta solo come file

        # rinomina / sposta
        new_dir = item.new_dir or item.path.parent
        target = _collision_safe(new_dir, item.new_name, item.path)
        entry["new_path"] = str(target)
        if target != item.path:
            try:
                new_dir.mkdir(parents=True, exist_ok=True)
                os.replace(item.path, target)
            except OSError as exc:
                entry["error"] = f"rinomina: {exc}"
        journal_entries.append(entry)

        if progress:
            progress(done, total, f"Modifica: {target.name}")

    # 3) rigenera le playlist nelle cartelle modificate
    _regenerate_playlists(plan.root, journal_entries)

    # 4) elimina le cartelle d'origine rimaste vuote dopo gli spostamenti
    moved = [e for e in journal_entries if e["original_path"] != e["new_path"]]
    if moved and not (cancel_event is not None and cancel_event.is_set()):
        _prune_empty_dirs({Path(e["original_path"]).parent for e in moved}, stop_at=plan.root)

    # 5) rinomina le cartelle album (solo se richiesto, senza riorganizzazione)
    folder_journal: list[dict] = []
    if plan.rename_folders and not plan.reorganize:
        for rename in plan.folder_renames:
            if cancel_event is not None and cancel_event.is_set():
                break
            if not rename.needs_change:
                continue
            if progress:
                progress(0, 0, f"Cartella: {rename.folder.name}")
            target = _collision_safe(rename.folder.parent, rename.new_name)
            try:
                os.rename(rename.folder, target)
                folder_journal.append({"original": str(rename.folder), "new": str(target)})
            except OSError as exc:
                folder_journal.append({"original": str(rename.folder), "new": str(target), "error": str(exc)})

    # 6) salva il diario (annullamento)
    JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    journal = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "root": str(plan.root),
        "cover_created": [str(p) for p in cover_created],
        "entries": journal_entries,
        "folder_renames": folder_journal,
    }
    JOURNAL_PATH.write_text(json.dumps(journal, ensure_ascii=False, indent=1), encoding="utf-8")
    return JOURNAL_PATH


def undo(journal_path: Path = JOURNAL_PATH, *, progress=None) -> int:
    """Annulla l'ultima operazione: ripristina tag, percorsi e copertine."""
    journal = json.loads(Path(journal_path).read_text(encoding="utf-8"))
    entries = journal.get("entries", [])

    # prima riporta le cartelle ai nomi originali (i percorsi dei file nel
    # diario si riferiscono alle cartelle con il nome precedente)
    for rename in reversed(journal.get("folder_renames", [])):
        if rename.get("error"):
            continue
        new = Path(rename["new"])
        original = Path(rename["original"])
        if new.is_dir() and not original.exists():
            try:
                os.rename(new, original)
            except OSError:
                pass

    restored = 0
    for i, entry in enumerate(reversed(entries), start=1):
        if progress:
            progress(i, len(entries), f"Ripristino: {Path(entry['new_path']).name}")
        current = Path(entry["new_path"])
        original = Path(entry["original_path"])
        if current.is_file():
            # cancella i tag che prima erano vuoti, poi ripristina i valori originali
            original_tags = entry.get("original_tags", {})
            current_tags = md.read_tags(current)
            to_clear = [k for k in md.TAG_KEYS
                        if not original_tags.get(k) and current_tags.get(k)]
            if to_clear:
                try:
                    md.clear_tags(current, to_clear)
                except Exception:
                    pass
            values = {k: v for k, v in original_tags.items()}
            try:
                md.write_tags(current, values)
            except Exception:
                pass
            if entry.get("cover_added"):
                md.remove_embedded_cover(current)
            try:
                original.parent.mkdir(parents=True, exist_ok=True)
                if current != original:
                    os.replace(current, original)
                restored += 1
            except OSError:
                pass
    # elimina copertine scaricate e cartelle d'origine rimaste vuote
    for name in journal.get("cover_created", []):
        try:
            Path(name).unlink(missing_ok=True)
        except OSError:
            pass
    if entries:
        drive_root = Path(entries[0]["original_path"]).parent.anchor or None
        stop_at = Path(journal["root"]) if journal.get("root") else (Path(drive_root) if drive_root else None)
        _prune_empty_dirs({Path(e["original_path"]).parent for e in entries}, stop_at=stop_at)
    return restored


def _collision_safe(new_dir: Path, new_name: str, source: Path | None = None) -> Path:
    """Se il nome di destinazione esiste gia (ed e' un altro elemento), aggiunge ' (2)'."""
    target = new_dir / new_name
    if (source is not None and target == source) or not target.exists():
        return target
    stem, ext = Path(new_name).stem, Path(new_name).suffix
    for n in range(2, 100):
        candidate = new_dir / f"{stem} ({n}){ext}"
        if candidate == source or not candidate.exists():
            return candidate
    return target


def _regenerate_playlists(root: Path, entries: list[dict]) -> None:
    """Aggiorna i percorsi dentro i file .m3u dopo i rinomini."""
    by_folder: dict[Path, dict[str, str]] = {}
    for entry in entries:
        old = Path(entry["original_path"])
        new = Path(entry["new_path"])
        if old.parent == new.parent and old.name != new.name:
            by_folder.setdefault(old.parent, {})[old.name] = new.name
    for folder, mapping in by_folder.items():
        for playlist in folder.iterdir():
            if playlist.suffix.lower() not in (".m3u", ".m3u8"):
                continue
            try:
                lines = playlist.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            changed = False
            out = []
            for line in lines:
                stripped = line.strip()
                if stripped and not stripped.startswith("#") and stripped in mapping:
                    out.append(mapping[stripped])
                    changed = True
                elif stripped and not stripped.startswith("#") and Path(stripped).name in mapping:
                    # percorsi relativi con sottocartelle
                    out.append(line.replace(Path(stripped).name, mapping[Path(stripped).name]))
                    changed = True
                else:
                    out.append(line)
            if changed:
                try:
                    playlist.write_text("\n".join(out) + "\n", encoding="utf-8")
                except OSError:
                    pass


def _prune_empty_dirs(folders: set[Path], stop_at: Path | None = None) -> None:
    """Elimina le cartelle rimaste vuote dopo uno spostamento (fino a `stop_at`)."""
    for folder in folders:
        current = folder
        while True:
            if stop_at is not None and current == stop_at:
                break
            try:
                if current.is_dir() and not any(current.iterdir()):
                    current.rmdir()
                else:
                    break
            except OSError:
                break
            parent = current.parent
            if parent == current:
                break
            current = parent
