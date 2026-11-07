"""Scansione ricorsiva dei file audio in una cartella."""

from __future__ import annotations

from pathlib import Path

from .metadata import AUDIO_EXTS

# cartelle di sistema da ignorare
_SKIP_DIRS = {"$recycle.bin", "system volume information", "recovered", "found.000"}
_PLAYLIST_EXTS = {".m3u", ".m3u8"}


def scan_audio(root: Path) -> list[Path]:
    """Tutti i file audio (per estensione) sotto `root`, ordinati per percorso."""
    files: list[Path] = []
    for dirpath, dirnames, filenames in _walk(root):
        for name in filenames:
            p = dirpath / name
            if p.suffix.lower() in AUDIO_EXTS:
                files.append(p)
    return sorted(files, key=lambda p: (str(p.parent).lower(), p.name.lower()))


def scan_playlists(root: Path) -> list[Path]:
    """Tutte le playlist .m3u/.m3u8 sotto `root`."""
    playlists: list[Path] = []
    for dirpath, dirnames, filenames in _walk(root):
        for name in filenames:
            p = dirpath / name
            if p.suffix.lower() in _PLAYLIST_EXTS:
                playlists.append(p)
    return sorted(playlists)


def _walk(root: Path):
    root = Path(root)
    if not root.is_dir():
        return
    for dirpath, dirnames, filenames in root.walk():
        # salta cartelle di sistema e nascoste
        dirnames[:] = [
            d for d in dirnames
            if not d.startswith(".") and d.lower() not in _SKIP_DIRS
        ]
        yield Path(dirpath), dirnames, [f for f in filenames if not f.startswith(".")]
