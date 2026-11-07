"""Analisi della libreria: decide per ogni file i tag corretti, il nuovo nome
e (se richiesto) la nuova cartella, senza toccare nulla.

I nomi file e le cartelle seguono pattern personalizzabili con i campi
{nn} {artista} {album} {titolo} {anno} {disco}; i campi mancanti spariscono
insieme ai separatori superflui.

Il risultato (Plan) viene mostrato in anteprima nella GUI prima di applicare.
"""

from __future__ import annotations

import threading
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from . import metadata as md
from .filename_parser import (
    FolderHint,
    expand_pattern,
    parse_filename,
    parse_folder_name,
    similar,
)
from .lookup import LookupClient, LookupResult
from .scanner import scan_audio

VARIOUS_ARTISTS = "Various Artists"

# soglie di confidenza per gli abbinamenti online
CONFIDENCE_OK = 0.62       # sopra: abbinamento accettato
CONFIDENCE_LOW = 0.45      # sopra: accettato ma segnalato in giallo

DEFAULT_FILENAME_PATTERN = "{nn} - {artista} - {album} - {titolo}"
DEFAULT_FOLDER_PATTERN = "{artista} - {album} ({disco})"
FILENAME_PRESETS = [
    DEFAULT_FILENAME_PATTERN,
    "{nn} - {titolo}",
    "{nn} - {artista} - {titolo}",
    "{artista} - {titolo}",
    "{titolo}",
    "{nn} - {artista} - {titolo} ({anno})",
]
FOLDER_PRESETS = [
    DEFAULT_FOLDER_PATTERN,
    "{album} ({disco})",
    "{artista} - {album}",
    "{album}",
    "{album} ({anno})",
]


@dataclass
class PlanItem:
    """Tutto cio che serve per un file: stato attuale e modifiche proposte."""
    path: Path
    original: dict                      # tag letti dal file
    folder_hint: FolderHint
    parsed: object                      # ParsedName dal nome file

    # componenti risolti (tag -> nome file -> internet -> manuale)
    components: dict = field(default_factory=dict)
    sources: dict = field(default_factory=dict)   # campo -> "tag"/"nome file"/"internet"
    confidence: float | None = None
    warnings: list[str] = field(default_factory=list)

    proposed: dict = field(default_factory=dict)  # tag finali da scrivere
    new_name: str = ""
    new_dir: Path | None = None
    manual: dict = field(default_factory=dict)    # correzioni manuali utente
    cover_needed: bool = False

    @property
    def new_path(self) -> Path:
        return (self.new_dir or self.path.parent) / self.new_name

    @property
    def needs_change(self) -> bool:
        if self.new_name and self.new_name != self.path.name:
            return True
        if self.new_dir and self.new_dir != self.path.parent:
            return True
        return bool(tag_differences(self.original, self.proposed))


@dataclass
class FolderRename:
    """Rinomina proposta per una cartella album (i file dentro restano al loro posto)."""
    folder: Path
    new_name: str

    @property
    def new_path(self) -> Path:
        return self.folder.parent / self.new_name

    @property
    def needs_change(self) -> bool:
        return bool(self.new_name) and self.new_name != self.folder.name


@dataclass
class Plan:
    root: Path
    items: list[PlanItem] = field(default_factory=list)
    cover_jobs: dict[Path, str] = field(default_factory=dict)  # cartella album -> url copertina
    folder_renames: list[FolderRename] = field(default_factory=list)
    filename_pattern: str = DEFAULT_FILENAME_PATTERN
    folder_pattern: str = DEFAULT_FOLDER_PATTERN
    rename_folders: bool = False
    reorganize: bool = False


def tag_differences(current: dict, proposed: dict) -> dict:
    """Campi da scrivere: proposti diversi dallo stato attuale.

    Il confronto ignora maiuscole e spazi doppi, cosi "2pac" non genera
    una modifica inutile rispetto a "2Pac".
    """
    diff = {}
    for key, new in proposed.items():
        if new is None:
            continue
        cur = current.get(key)
        if key == "track":
            if cur != new:
                diff[key] = new
            continue
        cur_n = str(cur or "").strip().lower()
        new_n = str(new).strip().lower()
        if cur_n != new_n:
            diff[key] = new
    return diff


def analyze(
    root: Path,
    *,
    reorganize: bool = False,
    online: bool = True,
    download_covers: bool = False,
    filename_pattern: str = DEFAULT_FILENAME_PATTERN,
    folder_pattern: str = DEFAULT_FOLDER_PATTERN,
    rename_folders: bool = False,
    progress=None,
    cancel_event: threading.Event | None = None,
) -> Plan:
    """Scansiona la libreria e costruisce il piano delle modifiche.

    progress(done, total, message) viene chiamato per aggiornare la GUI.
    Se cancel_event viene impostato, l'analisi si interrompe sollevando CancelledError.
    """
    plan = Plan(
        root=root,
        filename_pattern=filename_pattern or DEFAULT_FILENAME_PATTERN,
        folder_pattern=folder_pattern or DEFAULT_FOLDER_PATTERN,
        rename_folders=rename_folders and not reorganize,
        reorganize=reorganize,
    )
    files = scan_audio(root)
    if progress:
        progress(0, len(files), f"Trovati {len(files)} file audio")

    client = LookupClient(enabled=online)
    folders: dict[Path, list[PlanItem]] = {}

    for i, path in enumerate(files, start=1):
        if cancel_event is not None and cancel_event.is_set():
            raise CancelledError()
        original = md.read_tags(path)
        hint = _hint_for(path, root)
        parsed = parse_filename(
            path.name,
            artist_hint=original.get("artist") or hint.artist,
            album_hint=hint.album,
        )
        item = PlanItem(path=path, original=original, folder_hint=hint, parsed=parsed)
        folders.setdefault(path.parent, []).append(item)
        plan.items.append(item)
        if progress:
            progress(i, len(files), f"Lettura tag: {path.name}")

    # analisi per cartella: risoluzione componenti + rilevamento compilation
    folders_done = 0
    for folder, items in folders.items():
        if cancel_event is not None and cancel_event.is_set():
            raise CancelledError()
        folders_done += 1
        if progress:
            progress(folders_done, len(folders), f"Analisi cartella: {folder.name}")

        hint = items[0].folder_hint
        for item in items:
            _resolve_components(item, client)

        is_various = _is_compilation(items, hint)
        width = _track_width(items)

        for item in items:
            if is_various:
                item.components["albumartist"] = VARIOUS_ARTISTS
                item.sources["albumartist"] = "analisi"
            _finalize_item(item, width=width, reorganize=reorganize, root=root,
                           pattern=plan.filename_pattern)

        # copertine mancanti: una sola ricerca per album
        if download_covers:
            _plan_covers(folder, items, client, plan)

    if plan.rename_folders:
        _compute_folder_renames(plan, folders, plan.folder_pattern)

    return plan


def replan(
    plan: Plan,
    *,
    filename_pattern: str,
    folder_pattern: str,
    rename_folders: bool,
    reorganize: bool,
) -> Plan:
    """Ricalcola nomi file/cartelle e rinomine cartella con nuovi pattern,
    senza riscansionare la libreria ne ripetere le ricerche online."""
    plan.filename_pattern = filename_pattern or DEFAULT_FILENAME_PATTERN
    plan.folder_pattern = folder_pattern or DEFAULT_FOLDER_PATTERN
    plan.rename_folders = rename_folders and not reorganize
    plan.reorganize = reorganize
    plan.folder_renames = []

    folders: dict[Path, list[PlanItem]] = {}
    for item in plan.items:
        folders.setdefault(item.path.parent, []).append(item)

    for folder, items in folders.items():
        width = _track_width(items)
        for item in items:
            _compute_targets(item, width=width, reorganize=reorganize,
                             root=plan.root, pattern=plan.filename_pattern)

    if plan.rename_folders:
        _compute_folder_renames(plan, folders, plan.folder_pattern)
    return plan


class CancelledError(Exception):
    """Analisi annullata dall'utente."""


# --- risoluzione dei componenti ----------------------------------------------

def _hint_for(path: Path, root: Path) -> FolderHint:
    """Indizi dalla cartella (nessun indizio se il file e' direttamente nella radice)."""
    if path.parent == root:
        return FolderHint()
    return parse_folder_name(path.parent.name)


def _resolve_components(item: PlanItem, client: LookupClient) -> None:
    """Combina tag esistenti, nome file e ricerca online nei componenti finali."""
    comp: dict = {}
    sources: dict = {}
    original = item.original
    parsed = item.parsed
    hint = item.folder_hint

    def take(key, value, source):
        if value and not comp.get(key):
            comp[key] = value
            sources[key] = source

    # 1) tag esistenti (fonte piu affidabile)
    for key in ("title", "artist", "album", "albumartist", "track", "year", "genre"):
        take(key, original.get(key), "tag")

    # 2) nome cartella (l'organizzazione fisica ha priorita' sul nome file)
    take("album", hint.album, "nome cartella")
    take("artist", hint.artist, "nome cartella")

    # 3) nome file
    take("title", parsed.title, "nome file")
    take("artist", parsed.artist, "nome file")
    take("album", parsed.album, "nome file")
    take("track", parsed.track, "nome file")

    if hint.artist and not comp.get("albumartist"):
        comp["albumartist"] = hint.artist
        sources["albumartist"] = "nome cartella"
    if hint.disc and not comp.get("disc"):
        comp["disc"] = hint.disc
    if hint.year and not comp.get("year"):
        comp["year"] = hint.year
        sources["year"] = "nome cartella"

    # 4) ricerca online SOLO per i campi ancora mancanti
    missing = [k for k in ("title", "artist") if not comp.get(k)]
    if missing and client.enabled:
        result = client.lookup(
            artist=comp.get("artist"),
            title=comp.get("title"),
            album=comp.get("album"),
        )
        _apply_lookup(item, result, comp, sources, fill_only=missing)
    item.components = comp
    item.sources = sources


def _apply_lookup(
    item: PlanItem,
    result: LookupResult | None,
    comp: dict,
    sources: dict,
    fill_only: list[str],
) -> None:
    """Inserisce i dati trovati online senza mai sovrascrivere campi gia presenti."""
    if not result:
        return
    if result.confidence < CONFIDENCE_LOW:
        return  # abbinamento troppo debole: meglio niente
    item.confidence = max(item.confidence or 0.0, result.confidence)
    item.sources["_lookup_source"] = result.source

    if "title" in fill_only and result.title:
        comp["title"] = result.title
        sources["title"] = "internet"
    if "artist" in fill_only and result.artist:
        comp["artist"] = result.artist
        sources["artist"] = "internet"
    # anno/album dal risultato online se davvero mancanti (anche quando il
    # titolo era gia noto: la ricerca e' avvenuta perche' manca l'artista)
    if not comp.get("year") and result.year:
        comp["year"] = result.year
        sources["year"] = "internet"
    if not comp.get("album") and result.album:
        comp["album"] = result.album
        sources["album"] = "internet"
    if not comp.get("genre") and result.genre:
        comp["genre"] = result.genre
        sources["genre"] = "internet"
    if not comp.get("track") and result.track:
        comp["track"] = result.track
        sources["track"] = "internet"


def _is_compilation(items: list[PlanItem], hint: FolderHint) -> bool:
    """True se la cartella contiene brani di piu artisti (>= 3) o e' marcata Various."""
    for it in items:
        aa = (it.original.get("albumartist") or "").strip()
        if aa and similar(aa, VARIOUS_ARTISTS) >= 0.8:
            return True
        aa_norm = aa.lower()
        if aa_norm in ("various", "varie artisti", "varios", "compilation", "raccolta"):
            return True
    artists = set()
    for it in items:
        a = it.components.get("artist")
        if a:
            artists.add(a.strip().lower())
    return len(artists) >= 3


def _track_width(items: list[PlanItem]) -> int:
    """Larghezza del numero traccia: quante cifre serve per il max della cartella."""
    tracks = [it.components.get("track") for it in items if it.components.get("track")]
    return max(2, len(str(max(tracks)))) if tracks else 2


def _finalize_item(item: PlanItem, *, width: int, reorganize: bool, root: Path, pattern: str) -> None:
    """Completa i componenti (albumartist, correzioni manuali) e i tag proposti,
    poi calcola nome file e cartella di destinazione."""
    comp = dict(item.components)
    sources = dict(item.sources)

    if not comp.get("albumartist") and comp.get("artist"):
        comp["albumartist"] = comp["artist"]
        sources.setdefault("albumartist", sources.get("artist", ""))

    # correzioni manuali dell'utente (valore None/vuoto = cancella il campo)
    for key, value in item.manual.items():
        if value in (None, ""):
            comp.pop(key, None)
            sources.pop(key, None)
        else:
            comp[key] = value
            sources[key] = "manuale"

    item.components = comp
    item.sources = sources

    # tag proposti: tutto cio che abbiamo risolto ("disc" e' solo informativo)
    proposed = {k: comp.get(k) for k in md.TAG_KEYS if comp.get(k) is not None}
    if comp.get("albumartist"):
        proposed["albumartist"] = comp["albumartist"]
    item.proposed = proposed

    _compute_targets(item, width=width, reorganize=reorganize, root=root, pattern=pattern)


def _compute_targets(item: PlanItem, *, width: int, reorganize: bool, root: Path, pattern: str) -> None:
    """Calcola nuovo nome file e (se riorganizzazione) nuova cartella, + avvisi."""
    item.warnings = []
    comp = item.components

    title = comp.get("title")
    if not title:
        item.warnings.append("Titolo non trovato: rinomina saltata")
        item.new_name = item.path.name
        item.new_dir = item.path.parent if reorganize else None
        return

    ext = item.path.suffix.lower()
    name = expand_pattern(
        pattern,
        track=comp.get("track"),
        artist=comp.get("artist"),
        album=comp.get("album"),
        title=title,
        year=comp.get("year"),
        disc=comp.get("disc"),
        width=width,
    )
    item.new_name = (name + ext) if name else item.path.name

    if reorganize:
        artist_dir = comp.get("albumartist") or comp.get("artist") or "Artisti sconosciuti"
        album_dir = comp.get("album") or "Album sconosciuto"
        if comp.get("disc"):
            album_dir = f"{album_dir} (CD{comp['disc']})"
        item.new_dir = root / sanitize(artist_dir) / sanitize(album_dir)
    else:
        item.new_dir = None

    if item.confidence is not None and item.confidence < CONFIDENCE_OK:
        item.warnings.append("Abbinamento online incerto: controlla i dati")
    try:
        if len(str(item.new_path)) > 240:
            item.warnings.append("Percorso molto lungo: possibile problema su Windows")
    except Exception:
        pass


def sanitize(s: str | None) -> str:
    from .filename_parser import sanitize_component
    return sanitize_component(s) or "Sconosciuto"


# --- rinomina delle cartelle ---------------------------------------------------

def _compute_folder_renames(plan: Plan, folders: dict[Path, list[PlanItem]], pattern: str) -> None:
    plan.folder_renames = []
    for folder, items in folders.items():
        rename = _compute_folder_rename(folder, items, pattern, plan.root)
        if rename is not None and rename.needs_change:
            plan.folder_renames.append(rename)


def _compute_folder_rename(folder: Path, items: list[PlanItem], pattern: str, root: Path) -> FolderRename | None:
    """Nome cartella intelligente dai metadati risolti dei file che contiene."""
    if folder == root:
        return None  # la radice scelta dall'utente non si tocca

    albums = Counter(it.components.get("album") for it in items if it.components.get("album"))
    if not albums:
        return None
    album = albums.most_common(1)[0][0]

    artists = Counter(
        it.components.get("albumartist") or it.components.get("artist")
        for it in items
        if it.components.get("albumartist") or it.components.get("artist")
    )
    artist = artists.most_common(1)[0][0] if artists else None

    disc = items[0].folder_hint.disc
    year = items[0].folder_hint.year
    if year is None:
        years = Counter(it.components["year"] for it in items if it.components.get("year"))
        year = years.most_common(1)[0][0] if years else None

    new_name = expand_pattern(
        pattern, artist=artist, album=album, year=year, disc=disc, max_len=80,
    )
    if not new_name or "Album sconosciuto" in new_name or "Sconosciuto" in new_name:
        return None
    # album multi-CD: senza {disco} nel pattern il disco va aggiunto comunque,
    # altrimenti CD1 e CD2 finirebbero nella stessa cartella omonima
    if disc and "{disco}" not in pattern:
        new_name = f"{new_name} (CD{disc})"
    return FolderRename(folder=folder, new_name=new_name)


# --- copertine -----------------------------------------------------------------

def _plan_covers(folder: Path, items: list[PlanItem], client: LookupClient, plan: Plan) -> None:
    """Se la cartella non ha ne copertina incorporata ne cover.jpg, cerca l'URL online."""
    if md.find_cover_file(folder):
        return
    if all(md.has_embedded_cover(it.path) for it in items):
        return
    sample = items[0]
    album = sample.components.get("album")
    artist = sample.components.get("albumartist") or sample.components.get("artist")
    if not album:
        return
    result = client.lookup(artist=artist, title=sample.components.get("title"), album=album)
    if result and result.cover_url and result.confidence >= CONFIDENCE_OK:
        plan.cover_jobs[folder] = result.cover_url
        for it in items:
            if not md.has_embedded_cover(it.path):
                it.cover_needed = True
