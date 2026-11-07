"""Estrae traccia / artista / album / titolo dal nome file e dal nome della cartella.

Gestisce i pattern reali delle librerie musicali, ad esempio:
  - "01. - Keep Ya Head Up.flac"                 -> traccia 1, titolo
  - "02 - Gene Vincent - Be- Bop - A - Lula(1).mp3" -> traccia 2, artista, titolo (senza "(1)")
  - "03 - Artista - Album - Titolo.mp3"          -> traccia, artista, album, titolo
  - "Artista - Titolo.mp3" / "Titolo.mp3"
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path


@dataclass
class ParsedName:
    """Risultato del parsing del nome file."""
    track: int | None = None
    artist: str | None = None
    album: str | None = None
    title: str | None = None


@dataclass
class FolderHint:
    """Indizi ricavati dal nome della cartella che contiene i file."""
    artist: str | None = None
    album: str | None = None
    disc: int | None = None
    year: int | None = None


# --- pulizia del testo -------------------------------------------------------

# Parole tra parentesi che sono rumore, non parte del titolo (case-insensitive).
_JUNK_PATTERNS = [
    re.compile(r"\s*\((?:official\s+)?(?:music\s+)?(?:audio|video|lyrics?|lyric\s+video|visualizer|hq|hd|4k|explicit)\s*\)", re.I),
    re.compile(r"\s*\[(?:official\s+)?(?:music\s+)?(?:audio|video|lyrics?|visualizer|hq|hd|4k|explicit|320\s*k?bps?|flac|itunes)\s*\]", re.I),
    re.compile(r"\s*\{\s*(?:official\s+)?(?:audio|video|lyrics?)\s*\}", re.I),
]

# Suffisso "(1)", "(2)"... tipico dei download duplicati, solo a fine nome.
_DUP_SUFFIX = re.compile(r"\s*\(\d{1,2}\)\s*$")

# Numeri di traccia in testa al nome, nei formati piu comuni.
_TRACK_RES = [
    re.compile(r"^\((\d{1,3})\)\s*[-–]?\s*(.+)$"),   # (01) Titolo
    re.compile(r"^(\d{1,3})\.\s*[-–]\s*(.+)$"),      # 01. - Titolo
    re.compile(r"^(\d{1,3})\s*[-–]\s*(.+)$"),        # 01 - Titolo
    re.compile(r"^(\d{1,3})\.\s*(.+)$"),             # 01. Titolo
    re.compile(r"^(\d{2,3})\s+(.+)$"),               # 01 Titolo (solo 2-3 cifre)
]

_CD_RE = re.compile(r"[\[(]\s*(?:cd|disc|disk|disco)\s*0*(\d+)\s*[\])]", re.I)
_CD_PLAIN_RE = re.compile(r"\s+\(?\s*(?:cd|disco)\s*0*(\d+)\s*\)?\s*$", re.I)
_YEAR_RE = re.compile(r"[\[(]\s*((?:19|20)\d{2})\s*[\])]")
_QUALITY_TOKEN_RE = re.compile(r"\s*[\[(]\s*(?:flac|mp3|320\s*k?bps?|itunes|web|cdrip|vinyl\s*rip)\s*[\])]", re.I)


def clean_text(text: str | None) -> str | None:
    """Normalizza spazi e caratteri strani, restituisce None se resta vuoto."""
    if text is None:
        return None
    s = text.replace("\u00a0", " ").replace("\u200b", "")
    s = re.sub(r"\s+", " ", s).strip()
    s = s.strip(" -_.")
    s = re.sub(r"\s+", " ", s).strip()
    return s or None


def strip_junk(stem: str) -> str:
    """Rimuove rumore come '(1)', '[HQ]', '(Official Video)' da un nome."""
    s = stem
    for _ in range(4):  # fino a stabilizzazione (es. "(Live)(1)")
        prev = s
        for rex in _JUNK_PATTERNS:
            s = rex.sub("", s)
        # suffisso duplicato solo se resta qualcosa di significativo
        stripped = _DUP_SUFFIX.sub("", s)
        if len(stripped.strip()) >= 3:
            s = stripped
        s = clean_text(s) or s
        if s == prev:
            break
    return s


def norm_text(s: str | None) -> str:
    """Testo normalizzato per confronti: minuscolo, senza accenti/punteggiatura."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().replace("_", " ")
    s = re.sub(r"[^\w\s]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def similar(a: str | None, b: str | None) -> float:
    """Somiglianza 0..1 tra due stringhe (ignora maiuscole, accenti, punteggiatura)."""
    na, nb = norm_text(a), norm_text(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()


def sanitize_component(s: str | None, max_len: int = 100) -> str:
    """Rende una stringa utilizzabile come nome file/cartella su Windows."""
    s = s or ""
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", s)
    s = re.sub(r"\s+", " ", s).strip().strip(".").strip()
    if len(s) > max_len:
        s = s[:max_len].rstrip()
    return s.strip()


def parse_filename(
    filename: str,
    artist_hint: str | None = None,
    album_hint: str | None = None,
) -> ParsedName:
    """Estrae i componenti dal nome di un file (senza percorso).

    artist_hint / album_hint (dal tag o dalla cartella) servono a disambiguare
    i nomi con molti trattini, es. "Be- Bop - A - Lula".
    """
    stem = Path(filename).stem
    s = strip_junk(stem)
    # trattini usati come separatore (en/em dash) -> trattino semplice
    s = re.sub(r"\s*[–—―]\s*", " - ", s)
    s = re.sub(r"\s*-{2,}\s*", " - ", s)
    s = clean_text(s) or ""

    track: int | None = None
    for rex in _TRACK_RES:
        m = rex.match(s)
        if m:
            track = int(m.group(1))
            s = m.group(2)
            break

    segments = [c for c in (clean_text(seg) for seg in s.split(" - ")) if c]

    artist = album = title = None
    if album_hint:
        # la cartella definisce gia l'album: tutto quello dopo l'artista e' il titolo
        if len(segments) >= 2:
            artist, title = segments[0], " - ".join(segments[1:])
        elif len(segments) == 1:
            title = segments[0]
    else:
        if len(segments) >= 3:
            artist, album, title = segments[0], segments[1], " - ".join(segments[2:])
        elif len(segments) == 2:
            artist, title = segments
        elif len(segments) == 1:
            title = segments[0]

    # se il primo segmento non assomiglia all'artista noto, probabilmente era parte del titolo
    if artist and artist_hint and similar(artist, artist_hint) < 0.75 and similar(title, artist_hint) > similar(artist, artist_hint):
        title = f"{artist} - {title}"
        artist = None

    return ParsedName(track=track, artist=artist, album=album, title=title)


def parse_folder_name(folder: str) -> FolderHint:
    """Estrae indizi dal nome cartella, es. "2Pac - Greatest Hits [CD1]"."""
    s = folder
    disc = year = None

    m = _CD_RE.search(s)
    if m:
        disc = int(m.group(1))
        s = _CD_RE.sub("", s, count=1)
    else:
        m = _CD_PLAIN_RE.search(s)
        if m:
            disc = int(m.group(1))
            s = _CD_PLAIN_RE.sub("", s, count=1)

    m = _YEAR_RE.search(s)
    if m:
        year = int(m.group(1))
        s = _YEAR_RE.sub("", s, count=1)

    s = _QUALITY_TOKEN_RE.sub("", s)
    s = clean_text(s) or ""

    artist, album = None, s or None
    parts = [p for p in (clean_text(x) for x in s.split(" - ")) if p]
    if len(parts) == 2:
        artist, album = parts

    return FolderHint(artist=artist, album=album, disc=disc, year=year)


def build_filename(
    track: int | None,
    artist: str | None,
    album: str | None,
    title: str | None,
    extension: str,
    width: int = 2,
) -> str:
    """Costruisce il nuovo nome file nel formato "NN - Artista - Album - Titolo.ext"."""
    parts: list[str] = []
    if track:
        parts.append(f"{track:0{width}d}")
    for comp in (artist, album, title):
        c = sanitize_component(comp)
        if c:
            parts.append(c)
    if not parts:
        return ""
    return " - ".join(parts) + extension
