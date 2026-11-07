"""Ricerca dei brani doppi nella libreria e scelta di quale copia tenere.

Due file sono lo stesso brano se hanno lo stesso artista e lo stesso titolo
(ignorando maiuscole, accenti, punteggiatura e i «feat.»), una durata simile
e, salvo richiesta, lo stesso album. Le versioni diverse (Live, Remix…) hanno
un titolo diverso e restano separate.

Per ogni gruppo viene proposta automaticamente la copia da tenere secondo un
criterio (qualita migliore, file piu leggero o formato preferito); le altre
copie vanno nel Cestino solo quando l'utente lo conferma.
"""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from .converter import move_to_recycle_bin
from .metadata import TAG_KEYS, AudioInfo
from .planner import PlanItem

DURATION_TOLERANCE = 3.0   # secondi di differenza ammessi tra due copie

RULE_BEST = "best"
RULE_SMALLEST = "smallest"
# formati proponibili come preferiti, nell'ordine del menu
_CODEC_ORDER = ("flac", "alac", "pcm", "mp3", "aac", "opus", "vorbis", "wma")
# a parita di bitrate Opus, AAC e Vorbis suonano meglio dell'MP3
_EFFICIENCY = {"opus": 1.6, "aac": 1.3, "vorbis": 1.3, "mp3": 1.0, "wma": 0.9}
# tra due lossless equivalenti meglio un formato compresso e con tag completi
_LOSSLESS_RANK = {"flac": 3, "alac": 2, "pcm": 1}
_FEAT = re.compile(r"[(\[]?\s*\b(?:feat|ft|featuring)\b\.?[^)\]]*[)\]]?", re.IGNORECASE)
_COPY_MARKER = re.compile(r"(\(\d+\)|\s-\s(copia|copy)|\s(copia|copy))$", re.IGNORECASE)


def normalize(text: str | None) -> str:
    """Testo confrontabile: «Beyoncé – Halo (feat. X)» -> «beyonce halo»."""
    if not text:
        return ""
    t = unicodedata.normalize("NFKD", str(text))
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = _FEAT.sub(" ", t).casefold().replace("&", " and ")
    t = re.sub(r"[\W_]+", " ", t)
    return " ".join(t.split())


@dataclass
class DuplicateGroup:
    """Copie dello stesso brano; `keep` sono i file da tenere."""
    items: list[PlanItem]
    sizes: dict[Path, int] = field(default_factory=dict)
    keep: set[Path] = field(default_factory=set)

    @property
    def title(self) -> str:
        comp = self.items[0].components
        artist, title = comp.get("artist"), comp.get("title") or self.items[0].path.stem
        return f"{artist} — {title}" if artist else str(title)

    @property
    def to_remove(self) -> list[PlanItem]:
        return [it for it in self.items if it.path not in self.keep]

    def size(self, item: PlanItem) -> int:
        return self.sizes.get(item.path, 0)


# ==============================================================================
# ricerca
# ==============================================================================

def _same_duration(a: PlanItem, b: PlanItem, tolerance: float) -> bool:
    la, lb = a.audio.length, b.audio.length
    return not la or not lb or abs(la - lb) <= tolerance


def _same_album(a: PlanItem, b: PlanItem) -> bool:
    x, y = normalize(a.components.get("album")), normalize(b.components.get("album"))
    return not x or not y or x == y


def find_duplicates(items: list[PlanItem], *, across_albums: bool = False,
                    tolerance: float = DURATION_TOLERANCE, rule: str = RULE_BEST) -> list[DuplicateGroup]:
    """Gruppi di copie dello stesso brano, con la copia da tenere gia scelta."""
    buckets: dict[tuple, list[PlanItem]] = {}
    for item in items:
        title = normalize(item.components.get("title"))
        if not title:
            continue
        artist = normalize(item.components.get("artist"))
        # senza artista un titolo come «Intro» non basta: solo nella stessa cartella
        key = (artist, title) if artist else ("", title, os.path.normcase(str(item.path.parent)))
        buckets.setdefault(key, []).append(item)

    groups: list[DuplicateGroup] = []
    for bucket in buckets.values():
        if len(bucket) < 2:
            continue
        # componenti connesse: a-b e b-c simili mettono a, b e c nello stesso gruppo
        parent = list(range(len(bucket)))

        def find(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        for i in range(len(bucket)):
            for j in range(i + 1, len(bucket)):
                a, b = bucket[i], bucket[j]
                if _same_duration(a, b, tolerance) and (across_albums or _same_album(a, b)):
                    parent[find(i)] = find(j)
        clusters: dict[int, list[PlanItem]] = {}
        for i, item in enumerate(bucket):
            clusters.setdefault(find(i), []).append(item)
        for members in clusters.values():
            if len(members) > 1:
                members.sort(key=lambda it: str(it.path).casefold())
                groups.append(DuplicateGroup(members, sizes={it.path: _file_size(it.path) for it in members}))

    for group in groups:
        apply_rule(group, rule)
    groups.sort(key=lambda g: g.title.casefold())
    return groups


def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


# ==============================================================================
# quale copia tenere
# ==============================================================================

def quality_score(audio: AudioInfo) -> tuple:
    """Piu alto = qualita migliore. Lossless batte sempre i formati compressi."""
    if audio.lossless:
        return (2, audio.bits or 16, audio.sample_rate or 44100, _LOSSLESS_RANK.get(audio.codec or "", 0))
    if audio.codec:
        effective = round((audio.bitrate or 0) * _EFFICIENCY.get(audio.codec, 1.0))
        return (1, effective, audio.sample_rate or 0, 0)
    return (0, 0, 0, 0)


def _tiebreak(item: PlanItem) -> tuple:
    """A parita di qualita: tag piu completi, nome senza «(1)»/«copia», nome piu corto."""
    tags = sum(1 for k in TAG_KEYS if item.original.get(k) not in (None, ""))
    is_copy = bool(_COPY_MARKER.search(item.path.stem.strip()))
    return (tags, not is_copy, -len(item.path.name))


def rule_choices(groups: list[DuplicateGroup]) -> list[tuple[str, str]]:
    """Criteri proponibili: (chiave, testo del menu)."""
    choices = [(RULE_BEST, "La qualità migliore"), (RULE_SMALLEST, "Il file più leggero")]
    present = {it.audio.codec for g in groups for it in g.items if it.audio.codec}
    for codec in _CODEC_ORDER:
        if codec in present:
            name = AudioInfo(codec=codec).name
            choices.append((f"codec:{codec}", f"Il formato {name}, se c'è (altrimenti la qualità migliore)"))
    return choices


def choose_keep(group: DuplicateGroup, rule: str) -> PlanItem:
    """La copia da tenere secondo il criterio."""
    candidates = group.items
    if rule.startswith("codec:"):
        codec = rule.split(":", 1)[1]
        candidates = [it for it in group.items if it.audio.codec == codec] or group.items
    elif rule == RULE_SMALLEST:
        return min(candidates, key=lambda it: (group.size(it) or float("inf"),
                                               tuple(-x for x in _tiebreak(it))))
    return max(candidates, key=lambda it: (quality_score(it.audio), _tiebreak(it)))


def apply_rule(group: DuplicateGroup, rule: str) -> None:
    group.keep = {choose_keep(group, rule).path}


# ==============================================================================
# eliminazione (nel Cestino)
# ==============================================================================

@dataclass
class RemoveResult:
    removed: int = 0
    freed_bytes: int = 0
    errors: list[str] = field(default_factory=list)
    cancelled: bool = False


def remove_files(paths: list[Path], *, trash=None, progress=None, cancel_event=None) -> RemoveResult:
    """Sposta i file nel Cestino, uno alla volta (interrompibile)."""
    trash = trash or move_to_recycle_bin
    result = RemoveResult()
    for i, path in enumerate(paths, start=1):
        if cancel_event is not None and cancel_event.is_set():
            result.cancelled = True
            break
        if progress:
            progress(i, len(paths), f"Nel Cestino: {path.name}")
        size = _file_size(path)
        try:
            trash(path)
        except OSError as exc:
            result.errors.append(f"{path.name}: {exc}")
            continue
        result.removed += 1
        result.freed_bytes += size
    return result
