"""Applicazione del piano: scrive i tag, rinomina/sposta i file, gestisce le
collisioni, regenera le playlist e tiene un diario per annullare tutto.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import metadata as md
from .planner import Plan

JOURNAL_DIR = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "OrganizeMusicMetadata"
JOURNAL_PATH = JOURNAL_DIR / "undo.json"
# dopo un annullamento riuscito il diario viene archiviato con questo nome
UNDONE_NAME = "undo-annullato.json"


@dataclass
class ApplyResult:
    """Esito di `apply`, mostrato all'utente a fine operazione."""
    journal: Path
    files_changed: int = 0
    folders_renamed: int = 0
    covers_saved: int = 0
    errors: list[str] = field(default_factory=list)
    cancelled: bool = False


@dataclass
class UndoResult:
    restored: int = 0
    folders_restored: int = 0
    errors: list[str] = field(default_factory=list)


def _same_path(a: Path, b: Path) -> bool:
    """Stesso percorso, ignorando maiuscole/minuscole come fa Windows."""
    return os.path.normcase(str(a)) == os.path.normcase(str(b))


def _cancelled(cancel_event) -> bool:
    return cancel_event is not None and cancel_event.is_set()


def apply(plan: Plan, *, progress=None, cancel_event=None) -> ApplyResult:
    """Applica tutte le modifiche del piano (esclusi i file esclusi dall'utente).

    Il diario per l'annullamento viene salvato anche se l'operazione viene
    interrotta o fallisce a meta, cosi quanto gia fatto resta annullabile.
    """
    journal_path = JOURNAL_PATH
    result = ApplyResult(journal=journal_path)
    items = [it for it in plan.items if it.will_change]
    folder_renames = plan.active_folder_renames
    cover_jobs = {folder: url for folder, url in plan.cover_jobs.items()
                  if any(it.cover_needed and it.will_change and it.path.parent == folder for it in plan.items)}
    total = len(cover_jobs) + len(items) + len(folder_renames)
    step = 0

    def report(message: str) -> None:
        if progress:
            progress(step, total, message)

    journal_entries: list[dict] = []
    folder_journal: list[dict] = []
    cover_created: set[Path] = set()

    try:
        # 1) copertine: una per cartella album
        cover_bytes: dict[Path, bytes] = {}
        client = None
        for folder, url in cover_jobs.items():
            if _cancelled(cancel_event):
                break
            step += 1
            report(f"Copertina: {folder.name}")
            existing = md.find_cover_file(folder)
            if existing:
                data = existing.read_bytes()
            else:
                if client is None:
                    from .lookup import LookupClient
                    client = LookupClient()
                data = client.fetch_cover(url)
                if data:
                    ext = ".png" if url.lower().endswith(".png") else ".jpg"
                    target = folder / f"cover{ext}"
                    try:
                        target.write_bytes(data)
                        cover_created.add(target)
                        result.covers_saved += 1
                    except OSError as exc:
                        result.errors.append(f"{folder.name}: copertina non salvata ({exc})")
                        data = None
                else:
                    result.errors.append(f"{folder.name}: copertina non scaricata")
            if data:
                cover_bytes[folder] = data

        # 2) per ogni file: scrivi tag, poi rinomina/sposta
        for item in items:
            if _cancelled(cancel_event):
                break
            step += 1
            report(f"Modifica: {item.path.name}")

            entry: dict = {
                "original_path": str(item.path),
                "new_path": str(item.path),
                "original_tags": {k: v for k, v in item.original.items() if v is not None},
                "had_cover": md.has_embedded_cover(item.path),
            }
            journal_entries.append(entry)  # subito: anche un errore a meta resta annullabile
            errors: list[str] = []

            if item.tag_changes or item.tags_to_clear:
                try:
                    if item.tags_to_clear:
                        md.clear_tags(item.path, item.tags_to_clear)
                    if item.tag_changes:
                        md.write_tags(item.path, item.proposed)
                except Exception as exc:
                    errors.append(f"tag non scritti ({exc})")

            # incorpora la copertina scaricata se il file non ne aveva una
            folder_cover = cover_bytes.get(item.path.parent)
            if item.cover_needed and folder_cover and not entry["had_cover"]:
                try:
                    md.embed_cover(item.path, folder_cover)
                    entry["cover_added"] = True
                except Exception:
                    pass  # formato non supportato (ogg/wma): la copertina resta solo come file

            # rinomina / sposta
            if item.renames:
                new_dir = item.new_dir or item.path.parent
                target = _collision_safe(new_dir, item.new_name, item.path)
                # confronto testuale: su Windows Path("a") == Path("A"), ma una
                # rinomina che cambia solo maiuscole/minuscole va eseguita
                if str(target) != str(item.path):
                    try:
                        new_dir.mkdir(parents=True, exist_ok=True)
                        os.replace(item.path, target)
                        entry["new_path"] = str(target)
                    except OSError as exc:
                        errors.append(f"non rinominato ({exc.strerror or exc})")

            if errors:
                entry["error"] = "; ".join(errors)
                result.errors.append(f"{item.path.name}: {entry['error']}")
            else:
                result.files_changed += 1

        # 3) rigenera le playlist nelle cartelle modificate
        _regenerate_playlists(plan.root, journal_entries)

        # 4) elimina le cartelle d'origine rimaste vuote dopo gli spostamenti
        moved = [e for e in journal_entries if not _same_path(Path(e["original_path"]).parent,
                                                              Path(e["new_path"]).parent)]
        if moved and not _cancelled(cancel_event):
            _prune_empty_dirs({Path(e["original_path"]).parent for e in moved}, stop_at=plan.root)

        # 5) rinomina le cartelle album (solo se richiesto, senza riorganizzazione)
        for rename in folder_renames:
            if _cancelled(cancel_event):
                break
            step += 1
            report(f"Cartella: {rename.folder.name}")
            target = _collision_safe(rename.folder.parent, rename.new_name, rename.folder)
            try:
                os.rename(rename.folder, target)
                folder_journal.append({"original": str(rename.folder), "new": str(target)})
                result.folders_renamed += 1
            except OSError as exc:
                result.errors.append(f"Cartella {rename.folder.name}: non rinominata ({exc.strerror or exc})")
    finally:
        result.cancelled = _cancelled(cancel_event)
        # 6) salva il diario (annullamento), qualunque cosa sia successa
        if journal_entries or folder_journal or cover_created:
            JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
            journal = {
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
                "root": str(plan.root),
                "cover_created": [str(p) for p in cover_created],
                "entries": journal_entries,
                "folder_renames": folder_journal,
            }
            tmp = journal_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(journal, ensure_ascii=False, indent=1), encoding="utf-8")
            os.replace(tmp, journal_path)
    return result


def journal_info(journal_path: Path | None = None) -> dict | None:
    """Riepilogo dell'ultima operazione annullabile (None se non c'e')."""
    path = Path(journal_path or JOURNAL_PATH)
    try:
        journal = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return {
        "timestamp": journal.get("timestamp", ""),
        "root": journal.get("root", ""),
        "files": len(journal.get("entries", [])),
        "folders": len(journal.get("folder_renames", [])),
    }


def undo(journal_path: Path | None = None, *, progress=None) -> UndoResult:
    """Annulla l'ultima operazione: ripristina tag, percorsi e copertine.

    Se tutto va a buon fine il diario viene archiviato (non si puo annullare
    due volte la stessa operazione); in caso di errori resta, per riprovare.
    """
    journal_path = Path(journal_path or JOURNAL_PATH)
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    entries = journal.get("entries", [])
    result = UndoResult()

    # prima riporta le cartelle ai nomi originali (i percorsi dei file nel
    # diario si riferiscono alle cartelle con il nome precedente)
    for rename in reversed(journal.get("folder_renames", [])):
        if rename.get("error"):
            continue
        new = Path(rename["new"])
        original = Path(rename["original"])
        if not new.is_dir():
            continue
        if original.exists() and not _same_path(new, original):
            result.errors.append(f"Cartella {original.name}: esiste già, non ripristinata")
            continue
        try:
            os.rename(new, original)
            result.folders_restored += 1
        except OSError as exc:
            result.errors.append(f"Cartella {new.name}: {exc.strerror or exc}")

    for i, entry in enumerate(reversed(entries), start=1):
        if progress:
            progress(i, len(entries), f"Ripristino: {Path(entry['new_path']).name}")
        current = Path(entry["new_path"])
        original = Path(entry["original_path"])
        if not current.is_file() and original.is_file():
            current = original  # la rinomina non era avvenuta: i tag vanno comunque ripristinati
        if not current.is_file():
            result.errors.append(f"{original.name}: file non trovato")
            continue

        # cancella i tag che prima erano vuoti, poi ripristina i valori originali
        original_tags = entry.get("original_tags", {})
        current_tags = md.read_tags(current)
        to_clear = [k for k in md.TAG_KEYS if not original_tags.get(k) and current_tags.get(k)]
        try:
            if to_clear:
                md.clear_tags(current, to_clear)
            md.write_tags(current, dict(original_tags))
        except Exception as exc:
            result.errors.append(f"{current.name}: tag non ripristinati ({exc})")
        if entry.get("cover_added"):
            md.remove_embedded_cover(current)

        if str(current) != str(original):
            if original.exists() and not _same_path(current, original):
                result.errors.append(f"{original.name}: esiste già un file con il nome originale")
                continue
            try:
                original.parent.mkdir(parents=True, exist_ok=True)
                os.replace(current, original)
            except OSError as exc:
                result.errors.append(f"{current.name}: non ripristinato ({exc.strerror or exc})")
                continue
        result.restored += 1

    # elimina copertine scaricate e cartelle di destinazione rimaste vuote
    for name in journal.get("cover_created", []):
        try:
            Path(name).unlink(missing_ok=True)
        except OSError:
            pass
    if entries:
        root = Path(journal["root"]) if journal.get("root") else None
        _prune_empty_dirs({Path(e["new_path"]).parent for e in entries}, stop_at=root)

    if not result.errors:
        try:
            os.replace(journal_path, journal_path.with_name(UNDONE_NAME))
        except OSError:
            pass
    return result


def _collision_safe(new_dir: Path, new_name: str, source: Path | None = None) -> Path:
    """Se il nome di destinazione esiste gia (ed e' un altro elemento), aggiunge ' (2)'.

    `source` e' l'elemento che si sta rinominando: se la destinazione e' lui
    stesso (anche con maiuscole diverse) non c'e' collisione.
    """
    target = new_dir / new_name
    if (source is not None and _same_path(target, source)) or not target.exists():
        return target
    stem, ext = Path(new_name).stem, Path(new_name).suffix
    if source is not None and source.is_dir():
        stem, ext = new_name, ""
    for n in range(2, 100):
        candidate = new_dir / f"{stem} ({n}){ext}"
        if (source is not None and _same_path(candidate, source)) or not candidate.exists():
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
        try:
            playlists = [p for p in folder.iterdir() if p.suffix.lower() in (".m3u", ".m3u8")]
        except OSError:
            continue
        for playlist in playlists:
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
            if stop_at is not None and _same_path(current, stop_at):
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
