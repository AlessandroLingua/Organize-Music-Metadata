"""Lettura e scrittura dei tag audio via mutagen, per tutti i formati principali.

Campi normalizzati usati in tutta l'app:
  title, artist, album, albumartist, track (int|None), year (int|None), genre
"""

from __future__ import annotations

from pathlib import Path

from mutagen.asf import ASF
from mutagen.flac import FLAC, Picture
from mutagen.id3 import ID3, ID3NoHeaderError, APIC, TALB, TCON, TDRC, TIT2, TPE1, TPE2, TRCK
from mutagen.mp4 import MP4, MP4Cover
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis
from mutagen.wave import WAVE

TAG_KEYS = ("title", "artist", "album", "albumartist", "track", "year", "genre")

AUDIO_EXTS = {".mp3", ".flac", ".m4a", ".ogg", ".opus", ".wma", ".wav"}
# formati in cui possiamo incorporare la copertina
COVER_FORMATS = {".mp3", ".flac", ".m4a", ".wav"}


def _to_int(value) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(str(value).split("/")[0].strip())
    except (ValueError, TypeError):
        return None


def _to_year(value) -> int | None:
    if value in (None, ""):
        return None
    s = str(value)
    for token in s.replace("/", "-").split("-"):
        if len(token) == 4 and token.isdigit():
            return int(token)
    return None


def _first(value) -> str | None:
    """Primo elemento di un valore mutagen (lista/tupla/stringa)."""
    if value in (None, ""):
        return None
    if isinstance(value, (list, tuple)):
        if not value:
            return None
        value = value[0]
    s = str(value).strip()
    return s or None


# --- lettura -----------------------------------------------------------------

def read_tags(path: Path) -> dict:
    """Legge i tag di un file audio; restituisce un dict con i campi normalizzati.

    Non solleva eccezioni: un file illeggibile restituisce tag vuoti.
    """
    ext = path.suffix.lower()
    tags: dict = {k: None for k in TAG_KEYS}
    try:
        if ext == ".mp3":
            try:
                id3 = ID3(path)
            except ID3NoHeaderError:
                return tags
            tags["title"] = _first(id3.get("TIT2"))
            tags["artist"] = _first(id3.get("TPE1"))
            tags["album"] = _first(id3.get("TALB"))
            tags["albumartist"] = _first(id3.get("TPE2"))
            tags["track"] = _to_int(_first(id3.get("TRCK")))
            tags["year"] = _to_year(_first(id3.get("TDRC")) or _first(id3.get("TYER")))
            tags["genre"] = _first(id3.get("TCON"))
        elif ext == ".flac":
            f = FLAC(path)
            t = f.tags or {}
            tags["title"] = _first(t.get("title"))
            tags["artist"] = _first(t.get("artist"))
            tags["album"] = _first(t.get("album"))
            tags["albumartist"] = _first(t.get("albumartist"))
            tags["track"] = _to_int(_first(t.get("tracknumber")))
            tags["year"] = _to_year(_first(t.get("date")) or _first(t.get("originaldate")))
            tags["genre"] = _first(t.get("genre"))
        elif ext in (".ogg", ".opus"):
            f = OggOpus(path) if ext == ".opus" else OggVorbis(path)
            t = f.tags or {}
            tags["title"] = _first(t.get("title"))
            tags["artist"] = _first(t.get("artist"))
            tags["album"] = _first(t.get("album"))
            tags["albumartist"] = _first(t.get("albumartist"))
            tags["track"] = _to_int(_first(t.get("tracknumber")))
            tags["year"] = _to_year(_first(t.get("date")))
            tags["genre"] = _first(t.get("genre"))
        elif ext == ".m4a":
            f = MP4(path)
            t = f.tags or {}
            tags["title"] = _first(t.get("\xa9nam"))
            tags["artist"] = _first(t.get("\xa9ART"))
            tags["album"] = _first(t.get("\xa9alb"))
            tags["albumartist"] = _first(t.get("aART"))
            if t.get("trkn"):
                tags["track"] = t["trkn"][0][0]
            tags["year"] = _to_year(_first(t.get("\xa9day")))
            tags["genre"] = _first(t.get("\xa9gen"))
        elif ext == ".wma":
            f = ASF(path)
            tags["title"] = _first(f.get("Title"))
            tags["artist"] = _first(f.get("Author"))
            tags["album"] = _first(f.get("WM/AlbumTitle"))
            tags["albumartist"] = _first(f.get("WM/AlbumArtist"))
            tags["track"] = _to_int(_first(f.get("WM/TrackNumber")))
            tags["year"] = _to_year(_first(f.get("WM/Year")))
            tags["genre"] = _first(f.get("WM/Genre"))
        elif ext == ".wav":
            f = WAVE(path)
            id3 = f.tags if isinstance(f.tags, ID3) else None
            if id3:
                tags["title"] = _first(id3.get("TIT2"))
                tags["artist"] = _first(id3.get("TPE1"))
                tags["album"] = _first(id3.get("TALB"))
                tags["albumartist"] = _first(id3.get("TPE2"))
                tags["track"] = _to_int(_first(id3.get("TRCK")))
                tags["year"] = _to_year(_first(id3.get("TDRC")))
                tags["genre"] = _first(id3.get("TCON"))
    except Exception:
        pass  # file corrotto o non supportato: tag vuoti
    return tags


def has_embedded_cover(path: Path) -> bool:
    """True se il file contiene una copertina incorporata."""
    ext = path.suffix.lower()
    try:
        if ext in (".mp3", ".wav"):
            id3 = _load_id3(path)
            return bool(id3) and any(k.startswith("APIC") for k in id3.keys())
        if ext == ".flac":
            return len(FLAC(path).pictures) > 0
        if ext == ".m4a":
            return bool(MP4(path).tags.get("covr"))
    except Exception:
        pass
    return False


def _load_id3(path: Path) -> ID3:
    try:
        return ID3(path)
    except ID3NoHeaderError:
        return ID3()


# --- scrittura ---------------------------------------------------------------

def write_tags(path: Path, values: dict) -> None:
    """Scrive i tag (solo i campi non-None presenti in `values`). Solleva su errore."""
    ext = path.suffix.lower()

    if ext == ".mp3":
        id3 = _load_id3(path)
        _set_id3(id3, values)
        id3.save(path, v2_version=3)
    elif ext == ".wav":
        f = WAVE(path)
        if not isinstance(f.tags, ID3):
            f.tags = ID3()
        _set_id3(f.tags, values)
        f.save()
    elif ext == ".flac":
        f = FLAC(path)
        if f.tags is None:
            f.add_tags()
        _set_vorbis(f.tags, values)
        f.save()
    elif ext in (".ogg", ".opus"):
        f = OggOpus(path) if ext == ".opus" else OggVorbis(path)
        if f.tags is None:
            f.add_tags()
        _set_vorbis(f.tags, values)
        f.save()
    elif ext == ".m4a":
        f = MP4(path)
        t = f.tags
        if t is None:
            f.add_tags()
            t = f.tags
        _set_mp4(t, values)
        f.save()
    elif ext == ".wma":
        f = ASF(path)
        _set_asf(f, values)
        f.save()
    else:
        raise ValueError(f"Formato non supportato: {ext}")


def _set_id3(id3: ID3, values: dict) -> None:
    def text(value):
        return str(value)

    if values.get("title"):
        id3.setall("TIT2", [TIT2(encoding=3, text=text(values["title"]))])
    if values.get("artist"):
        id3.setall("TPE1", [TPE1(encoding=3, text=text(values["artist"]))])
    if values.get("album"):
        id3.setall("TALB", [TALB(encoding=3, text=text(values["album"]))])
    if values.get("albumartist"):
        id3.setall("TPE2", [TPE2(encoding=3, text=text(values["albumartist"]))])
    if values.get("track"):
        id3.setall("TRCK", [TRCK(encoding=3, text=str(values["track"]))])
    if values.get("year"):
        id3.setall("TDRC", [TDRC(encoding=3, text=str(values["year"]))])
    if values.get("genre"):
        id3.setall("TCON", [TCON(encoding=3, text=text(values["genre"]))])


def _set_vorbis(t, values: dict) -> None:
    if values.get("title"):
        t["title"] = [str(values["title"])]
    if values.get("artist"):
        t["artist"] = [str(values["artist"])]
    if values.get("album"):
        t["album"] = [str(values["album"])]
    if values.get("albumartist"):
        t["albumartist"] = [str(values["albumartist"])]
    if values.get("track"):
        t["tracknumber"] = [str(values["track"])]
    if values.get("year"):
        t["date"] = [str(values["year"])]
    if values.get("genre"):
        t["genre"] = [str(values["genre"])]


def _set_mp4(t, values: dict) -> None:
    if values.get("title"):
        t["\xa9nam"] = [str(values["title"])]
    if values.get("artist"):
        t["\xa9ART"] = [str(values["artist"])]
    if values.get("album"):
        t["\xa9alb"] = [str(values["album"])]
    if values.get("albumartist"):
        t["aART"] = [str(values["albumartist"])]
    if values.get("track"):
        t["trkn"] = [(int(values["track"]), 0)]
    if values.get("year"):
        t["\xa9day"] = [str(values["year"])]
    if values.get("genre"):
        t["\xa9gen"] = [str(values["genre"])]


def _set_asf(f: ASF, values: dict) -> None:
    from mutagen.asf import ASFUnicodeAttribute

    mapping = {
        "title": "Title",
        "artist": "Author",
        "album": "WM/AlbumTitle",
        "albumartist": "WM/AlbumArtist",
        "track": "WM/TrackNumber",
        "year": "WM/Year",
        "genre": "WM/Genre",
    }
    for key, asf_key in mapping.items():
        if values.get(key):
            f[asf_key] = [ASFUnicodeAttribute(str(values[key]))]


# --- copertine ---------------------------------------------------------------

# mapping chiave normalizzata -> identificatori per formato, per cancellare i tag
_CLEAR_MAP = {
    "mp3": {"title": "TIT2", "artist": "TPE1", "album": "TALB", "albumartist": "TPE2",
            "track": "TRCK", "year": "TDRC", "genre": "TCON"},
    "vorbis": {"title": "title", "artist": "artist", "album": "album", "albumartist": "albumartist",
               "track": "tracknumber", "year": "date", "genre": "genre"},
    "m4a": {"title": "\xa9nam", "artist": "\xa9ART", "album": "\xa9alb", "albumartist": "aART",
            "track": "trkn", "year": "\xa9day", "genre": "\xa9gen"},
    "wma": {"title": "Title", "artist": "Author", "album": "WM/AlbumTitle", "albumartist": "WM/AlbumArtist",
            "track": "WM/TrackNumber", "year": "WM/Year", "genre": "WM/Genre"},
}


def clear_tags(path: Path, keys) -> None:
    """Cancella i tag indicati (chiavi normalizzate di TAG_KEYS)."""
    ext = path.suffix.lower()
    if ext in (".mp3", ".wav"):
        id3 = _load_id3(path)
        for key in keys:
            frame = _CLEAR_MAP["mp3"].get(key)
            if frame:
                id3.delall(frame)
        id3.save(path, v2_version=3)
    elif ext == ".flac":
        f = FLAC(path)
        for key in keys:
            name = _CLEAR_MAP["vorbis"].get(key)
            if name and name in (f.tags or {}):
                del f.tags[name]
        f.save()
    elif ext in (".ogg", ".opus"):
        f = OggOpus(path) if ext == ".opus" else OggVorbis(path)
        for key in keys:
            name = _CLEAR_MAP["vorbis"].get(key)
            if name and name in (f.tags or {}):
                del f.tags[name]
        f.save()
    elif ext == ".m4a":
        f = MP4(path)
        for key in keys:
            name = _CLEAR_MAP["m4a"].get(key)
            if name and f.tags and name in f.tags:
                del f.tags[name]
        f.save()
    elif ext == ".wma":
        f = ASF(path)
        for key in keys:
            name = _CLEAR_MAP["wma"].get(key)
            if name and name in f:
                del f[name]
        f.save()


def embed_cover(path: Path, data: bytes, mime: str = "image/jpeg") -> None:
    """Incorpora una copertina nel file (sostituisce quelle esistenti)."""
    ext = path.suffix.lower()
    if ext in (".mp3", ".wav"):
        id3 = _load_id3(path)
        id3.delall("APIC")
        id3.add(APIC(encoding=3, mime=mime, type=3, desc="", data=data))
        id3.save(path, v2_version=3)
    elif ext == ".flac":
        f = FLAC(path)
        f.clear_pictures()
        pic = Picture()
        pic.type = 3
        pic.mime = mime
        pic.desc = ""
        pic.data = data
        f.add_picture(pic)
        f.save()
    elif ext == ".m4a":
        f = MP4(path)
        fmt = MP4Cover.FORMAT_PNG if mime == "image/png" else MP4Cover.FORMAT_JPEG
        f.tags["covr"] = [MP4Cover(data, imageformat=fmt)]
        f.save()
    else:
        raise ValueError(f"Incorporamento copertina non supportato per {ext}")


def remove_embedded_cover(path: Path) -> None:
    """Rimuove la copertina incorporata (usato dall'annullamento modifiche)."""
    ext = path.suffix.lower()
    try:
        if ext in (".mp3", ".wav"):
            id3 = _load_id3(path)
            id3.delall("APIC")
            id3.save(path, v2_version=3)
        elif ext == ".flac":
            f = FLAC(path)
            if f.pictures:
                f.clear_pictures()
                f.save()
        elif ext == ".m4a":
            f = MP4(path)
            if f.tags and f.tags.get("covr"):
                del f.tags["covr"]
                f.save()
    except Exception:
        pass


def find_cover_file(folder: Path) -> Path | None:
    """Cerca un'immagine di copertina gia presente nella cartella dell'album."""
    names = ("cover.jpg", "cover.png", "folder.jpg", "folder.png", "front.jpg", "front.png", "album.jpg", "art.jpg")
    for name in names:
        p = folder / name
        if p.is_file():
            return p
    for p in folder.iterdir():
        if p.suffix.lower() in (".jpg", ".jpeg", ".png") and any(
            k in p.stem.lower() for k in ("cover", "folder", "front", "art")
        ):
            return p
    return None
