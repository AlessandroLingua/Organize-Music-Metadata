"""Conversione dei file audio in altri formati e qualita, tramite FFmpeg.

Gli originali non vengono mai modificati: i file convertiti vanno in una
cartella separata (con le stesse sottocartelle) oppure accanto agli originali.
Tag e copertina vengono copiati nel nuovo file.

Ogni file viene scritto prima con un nome temporaneo nascosto e rinominato
solo a conversione riuscita: un'interruzione non lascia mai file a meta.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from . import metadata as md


# ==============================================================================
# formati e qualita
# ==============================================================================

@dataclass(frozen=True)
class Quality:
    key: str
    label: str
    args: tuple[str, ...] = ()   # opzioni dell'encoder (formati compressi)
    kbps: int | None = None      # bitrate nominale (formati compressi)
    bits: int | None = None      # profondita massima (lossless; None = come l'originale)
    rate: int | None = None      # frequenza massima (lossless; None = come l'originale)


@dataclass(frozen=True)
class OutputFormat:
    key: str
    label: str          # nome nel menu
    short: str          # nome breve (cartella predefinita, messaggi)
    ext: str
    codec: str          # codec come lo riporta md.audio_info
    encoder: str        # encoder FFmpeg
    muxer: str          # contenitore FFmpeg
    lossless: bool
    description: str
    qualities: tuple[Quality, ...]
    default_quality: str

    def quality(self, key: str | None) -> Quality:
        for q in self.qualities:
            if q.key == key:
                return q
        return next(q for q in self.qualities if q.key == self.default_quality)


def _cbr(kbps: int, label: str) -> Quality:
    return Quality(str(kbps), f"{kbps} kbps · {label}", ("-b:a", f"{kbps}k"), kbps=kbps)


LOSSLESS_QUALITIES = (
    Quality("original", "Come l'originale"),
    Quality("cd", "16 bit · 44,1 kHz (qualità CD)", bits=16, rate=44100),
    Quality("hd", "24 bit · fino a 48 kHz", bits=24, rate=48000),
)

FORMATS: dict[str, OutputFormat] = {f.key: f for f in (
    OutputFormat(
        "mp3", "MP3", "MP3", ".mp3", "mp3", "libmp3lame", "mp3", False,
        "Il più compatibile: telefoni, autoradio, qualsiasi lettore.",
        (_cbr(320, "massima"), _cbr(256, "alta"), _cbr(192, "buona"), _cbr(128, "file piccoli"),
         Quality("v0", "VBR V0 · circa 245 kbps (variabile)", ("-q:a", "0"), kbps=245),
         Quality("v2", "VBR V2 · circa 190 kbps (variabile)", ("-q:a", "2"), kbps=190)),
        "320"),
    OutputFormat(
        "aac", "AAC (.m4a)", "AAC", ".m4a", "aac", "aac", "ipod", False,
        "Ottimo per iPhone, iTunes e Apple Music; più efficiente dell'MP3.",
        (_cbr(320, "massima"), _cbr(256, "alta"), _cbr(192, "buona"), _cbr(128, "file piccoli")),
        "256"),
    OutputFormat(
        "opus", "Opus", "Opus", ".opus", "opus", "libopus", "opus", False,
        "La qualità migliore a parità di dimensione (lettori e telefoni recenti).",
        (_cbr(256, "massima"), _cbr(192, "alta"), _cbr(160, "trasparente"), _cbr(128, "buona"),
         _cbr(96, "file piccoli")),
        "160"),
    OutputFormat(
        "ogg", "OGG Vorbis", "OGG", ".ogg", "vorbis", "libvorbis", "ogg", False,
        "Formato libero, molto diffuso nei giochi e su Android.",
        (Quality("q10", "Q10 · circa 500 kbps (massima)", ("-q:a", "10"), kbps=500),
         Quality("q8", "Q8 · circa 256 kbps (alta)", ("-q:a", "8"), kbps=256),
         Quality("q6", "Q6 · circa 192 kbps (buona)", ("-q:a", "6"), kbps=192),
         Quality("q4", "Q4 · circa 128 kbps (file piccoli)", ("-q:a", "4"), kbps=128)),
        "q6"),
    OutputFormat(
        "flac", "FLAC", "FLAC", ".flac", "flac", "flac", "flac", True,
        "Senza perdita di qualità; circa la metà dello spazio del WAV.",
        LOSSLESS_QUALITIES, "original"),
    OutputFormat(
        "alac", "ALAC (.m4a)", "ALAC", ".m4a", "alac", "alac", "ipod", True,
        "Senza perdita di qualità, per iPhone, iTunes e Apple Music.",
        LOSSLESS_QUALITIES, "original"),
    OutputFormat(
        "wav", "WAV", "WAV", ".wav", "pcm", "pcm_s16le", "wav", True,
        "Non compresso: massima compatibilità ma file molto grandi.",
        LOSSLESS_QUALITIES, "original"),
)}
DEFAULT_FORMAT = "mp3"


def _capped_rate(source: int | None, cap: int | None) -> int | None:
    """Frequenza a cui ricampionare (None = lasciare invariata).

    Le frequenze multiple di 44,1 kHz (88,2, 176,4…) scendono a 44,1 kHz,
    le altre al limite richiesto: il ricampionamento resta il piu semplice.
    """
    if not source or not cap or source <= cap:
        return None
    return 44100 if source % 44100 == 0 else cap


def _target_bits(info: md.AudioInfo, quality: Quality) -> int:
    """Profondita del file lossless di destinazione: 16 o 24 bit.

    Da una sorgente compressa (MP3…) non c'e' nulla da guadagnare oltre i 16 bit.
    """
    natural = 24 if info.lossless and (info.bits or 16) > 16 else 16
    return min(natural, quality.bits) if quality.bits else natural


def encoder_args(fmt: OutputFormat, quality: Quality, info: md.AudioInfo) -> list[str]:
    """Opzioni FFmpeg per codificare il flusso audio."""
    if fmt.lossless:
        bits = _target_bits(info, quality)
        rate = _capped_rate(info.sample_rate, quality.rate)
        if fmt.key == "flac":
            args = ["-c:a", "flac", "-compression_level", "8"]
            args += ["-sample_fmt", "s16"] if bits == 16 else ["-sample_fmt", "s32", "-bits_per_raw_sample", "24"]
        elif fmt.key == "alac":
            args = ["-c:a", "alac", "-sample_fmt", "s16p" if bits == 16 else "s32p"]
        else:  # wav
            args = ["-c:a", "pcm_s16le" if bits == 16 else "pcm_s24le"]
    else:
        args = ["-c:a", fmt.encoder, *quality.args]
        # Opus lavora sempre a 48 kHz (lo gestisce FFmpeg); gli altri non oltre i 48 kHz
        rate = None if fmt.key == "opus" else _capped_rate(info.sample_rate, 48000)
    if rate:
        args += ["-ar", str(rate)]
    return args


def can_copy(info: md.AudioInfo, fmt: OutputFormat, quality: Quality) -> bool:
    """True se il file e' gia nel formato scelto con qualita uguale o inferiore:
    copiarlo cosi com'e' evita una ricodifica inutile (e, per i formati
    compressi, un'ulteriore perdita di qualita)."""
    if info.codec != fmt.codec:
        return False
    if fmt.lossless:
        return (_target_bits(info, quality) >= (info.bits or 16)
                and _capped_rate(info.sample_rate, quality.rate) is None)
    return bool(info.bitrate and quality.kbps and info.bitrate <= quality.kbps * 1.05)


# ==============================================================================
# FFmpeg
# ==============================================================================

FFMPEG_DOWNLOAD_URL = "https://ffmpeg.org/download.html"
_EXE = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"
# su Windows FFmpeg non deve aprire una finestra di terminale
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def find_ffmpeg(app_dir: Path | None = None) -> str | None:
    """Percorso di ffmpeg: accanto all'app, nel PATH o nelle installazioni comuni."""
    candidates: list[Path] = []
    if app_dir is not None:
        candidates += [app_dir / _EXE, app_dir / "ffmpeg" / _EXE, app_dir / "ffmpeg" / "bin" / _EXE]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    found = shutil.which("ffmpeg")
    if found:
        return found

    candidates = []
    local = os.environ.get("LOCALAPPDATA")
    if local:
        winget = Path(local) / "Microsoft" / "WinGet"
        candidates.append(winget / "Links" / _EXE)
        try:
            candidates += sorted((winget / "Packages").glob(f"*FFmpeg*/*/bin/{_EXE}"), reverse=True)
        except OSError:
            pass
    for var in ("ProgramFiles", "ProgramFiles(x86)"):
        if os.environ.get(var):
            candidates.append(Path(os.environ[var]) / "ffmpeg" / "bin" / _EXE)
    candidates += [
        Path("C:/ffmpeg/bin") / _EXE,
        Path(os.environ.get("ProgramData", "C:/ProgramData")) / "chocolatey" / "bin" / _EXE,
        Path.home() / "scoop" / "shims" / _EXE,
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


@lru_cache(maxsize=4)
def available_encoders(ffmpeg: str) -> frozenset[str] | None:
    """Encoder audio disponibili in questa versione di FFmpeg (None = sconosciuti)."""
    try:
        out = subprocess.run([ffmpeg, "-hide_banner", "-encoders"], capture_output=True, text=True,
                             timeout=15, creationflags=_NO_WINDOW, stdin=subprocess.DEVNULL).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    names = set()
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 2 and len(parts[0]) == 6 and parts[0].startswith("A"):
            names.add(parts[1])
    return frozenset(names) or None


def supported_formats(ffmpeg: str) -> list[OutputFormat]:
    """Formati di uscita che questa versione di FFmpeg sa produrre."""
    encoders = available_encoders(ffmpeg)
    if encoders is None:
        return list(FORMATS.values())
    return [f for f in FORMATS.values() if f.encoder in encoders]


# ==============================================================================
# piano della conversione
# ==============================================================================

@dataclass
class ConvertJob:
    source: Path
    target: Path
    skip_reason: str | None = None   # se valorizzato il file non viene convertito


@dataclass
class ConvertResult:
    output_dir: Path | None          # None = accanto agli originali
    converted: int = 0
    copied: int = 0
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    cancelled: bool = False
    source_bytes: int = 0            # dimensione degli originali elaborati
    output_bytes: int = 0            # dimensione dei file creati

    @property
    def created(self) -> int:
        return self.converted + self.copied


def _key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def _is_within(path: Path, folder: Path) -> bool:
    try:
        return os.path.commonpath([_key(path), _key(folder)]) == _key(folder)
    except ValueError:  # unita diverse
        return False


def default_output_dir(root: Path, fmt: OutputFormat) -> Path:
    """Cartella proposta: accanto a quella della musica, es. «Musica (MP3)»."""
    root = Path(root)
    if root.name:
        return root.parent / f"{root.name} ({fmt.short})"
    return Path.home() / "Music" / f"Musica convertita ({fmt.short})"


def output_dir_error(output_dir: str | Path | None, root: Path) -> str | None:
    """Messaggio d'errore se la cartella di destinazione non e' utilizzabile."""
    text = str(output_dir or "").strip().strip('"')
    if not text:
        return "Scegli la cartella di destinazione."
    path = Path(text)
    if not path.is_absolute():
        return "Indica il percorso completo della cartella (es. D:\\Musica MP3)."
    if _is_within(path, root):
        return ("La destinazione deve essere fuori dalla cartella della musica, "
                "altrimenti i file convertiti si mescolano agli originali.")
    if path.exists() and not path.is_dir():
        return "Esiste già un file con questo nome."
    return None


def _target_folder(source: Path, root: Path, output_dir: Path | None) -> Path:
    if output_dir is None:
        return source.parent
    try:
        rel = source.parent.relative_to(root)
    except ValueError:
        rel = Path(source.parent.name)
    return output_dir / rel


def plan_jobs(sources: list[Path], root: Path, fmt: OutputFormat, output_dir: Path | None, *,
              overwrite: bool = False) -> list[ConvertJob]:
    """Decide il file di destinazione di ogni originale.

    Non sovrascrive mai un originale ne un file gia presente accanto agli
    originali; nella cartella separata sostituisce i file esistenti solo se
    `overwrite` e' attivo (altrimenti li salta, cosi una conversione
    interrotta si puo riprendere).
    """
    jobs: list[ConvertJob] = []
    sources_keys = {_key(s) for s in sources}
    reserved: set[str] = set()
    for source in sources:
        folder = _target_folder(source, root, output_dir)
        target = folder / (source.stem + fmt.ext)
        key = _key(target)
        if key == _key(source):
            jobs.append(ConvertJob(source, target, f"è già un file {fmt.ext.lstrip('.').upper()}"))
            continue
        if key in reserved:
            # due originali con lo stesso nome e formati diversi (brano.flac, brano.wav)
            n = 2
            while True:
                target = folder / f"{source.stem} ({n}){fmt.ext}"
                key = _key(target)
                if key not in reserved and key not in sources_keys and not target.exists():
                    break
                n += 1
        if key in sources_keys or (target.exists() and not (overwrite and output_dir is not None)):
            reserved.add(key)
            jobs.append(ConvertJob(source, target, "esiste già nella destinazione"))
            continue
        reserved.add(key)
        jobs.append(ConvertJob(source, target))
    return jobs


# ==============================================================================
# esecuzione
# ==============================================================================

class _Cancelled(Exception):
    pass


def default_workers() -> int:
    """Conversioni in parallelo: una per core, lasciandone uno libero."""
    return max(1, min(8, (os.cpu_count() or 2) - 1))


def _temp_path(target: Path) -> Path:
    # il punto iniziale nasconde il file alla scansione della libreria;
    # l'estensione resta quella vera perche' mutagen la usa per scrivere i tag
    return target.with_name(f".{target.stem}.parziale{target.suffix}")


def _run_ffmpeg(cmd: list[str], cancel_event) -> None:
    proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE, creationflags=_NO_WINDOW)
    while True:
        try:
            _, err = proc.communicate(timeout=0.25)
            break
        except subprocess.TimeoutExpired:
            if cancel_event is not None and cancel_event.is_set():
                proc.kill()
                proc.communicate()
                raise _Cancelled()
    if proc.returncode != 0:
        lines = [ln.strip() for ln in err.decode("utf-8", "replace").splitlines() if ln.strip()]
        raise RuntimeError(lines[-1] if lines else f"FFmpeg ha restituito l'errore {proc.returncode}")


class _CoverCache:
    """Copertina della cartella (cover.jpg…) letta una sola volta per cartella."""

    def __init__(self):
        self._lock = threading.Lock()
        self._data: dict[Path, tuple[bytes, str] | None] = {}

    def get(self, folder: Path) -> tuple[bytes, str] | None:
        with self._lock:
            if folder not in self._data:
                cover = None
                try:
                    found = md.find_cover_file(folder)
                    if found is not None:
                        mime = "image/png" if found.suffix.lower() == ".png" else "image/jpeg"
                        cover = (found.read_bytes(), mime)
                except OSError:
                    pass
                self._data[folder] = cover
            return self._data[folder]


def _copy_tags_and_cover(source: Path, target: Path, covers: _CoverCache) -> str | None:
    """Riporta tag e copertina dell'originale sul file convertito.

    FFmpeg copia gia buona parte dei tag; qui si garantiscono quelli principali
    (con i nomi giusti per ogni formato) e la copertina, che FFmpeg non sa
    trasferire tra tutti i formati. Restituisce un avviso, se qualcosa non va.
    """
    problems = []
    tags = {k: v for k, v in md.read_tags(source).items() if v not in (None, "")}
    if tags:
        try:
            md.write_tags(target, tags)
        except Exception as exc:
            problems.append(f"tag non copiati ({exc})")
    if target.suffix.lower() in md.COVER_FORMATS:
        cover = md.read_cover(source) or covers.get(source.parent)
        if cover:
            try:
                md.embed_cover(target, *cover)
            except Exception as exc:
                problems.append(f"copertina non copiata ({exc})")
    return "; ".join(problems) or None


def convert(jobs: list[ConvertJob], fmt: OutputFormat, quality: Quality, *, ffmpeg: str,
            output_dir: Path | None, copy_compatible: bool = True, progress=None,
            cancel_event=None, workers: int | None = None) -> ConvertResult:
    """Converte i file del piano, in parallelo. Gli originali non vengono toccati."""
    result = ConvertResult(output_dir=output_dir)
    result.skipped = [f"{job.source.name}: {job.skip_reason}" for job in jobs if job.skip_reason]
    todo = [job for job in jobs if not job.skip_reason]
    covers = _CoverCache()
    done_folders: dict[Path, Path] = {}
    lock = threading.Lock()

    def cancelled() -> bool:
        return cancel_event is not None and cancel_event.is_set()

    def run(job: ConvertJob) -> tuple[str, str | None]:
        if cancelled():
            raise _Cancelled()
        if not job.source.is_file():
            raise RuntimeError("file non trovato")
        info = md.audio_info(job.source)
        job.target.parent.mkdir(parents=True, exist_ok=True)
        temp = _temp_path(job.target)
        try:
            if copy_compatible and can_copy(info, fmt, quality):
                shutil.copy2(job.source, temp)
                outcome, warning = "copied", None
            else:
                cmd = [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
                       "-i", str(job.source), "-map", "0:a:0", "-map_metadata", "0",
                       *encoder_args(fmt, quality, info)]
                if fmt.key == "mp3":
                    cmd += ["-id3v2_version", "3"]
                cmd += ["-f", fmt.muxer, str(temp)]
                _run_ffmpeg(cmd, cancel_event)
                outcome = "converted"
                warning = _copy_tags_and_cover(job.source, temp, covers)
            os.replace(temp, job.target)
        finally:
            try:
                temp.unlink(missing_ok=True)
            except OSError:
                pass
        with lock:
            done_folders.setdefault(job.source.parent, job.target.parent)
        return outcome, warning

    total = len(todo)
    done = 0
    executor = ThreadPoolExecutor(max_workers=workers or default_workers())
    try:
        futures = {executor.submit(run, job): job for job in todo}
        for future in as_completed(futures):
            job = futures[future]
            done += 1
            try:
                outcome, warning = future.result()
            except _Cancelled:
                continue
            except Exception as exc:
                result.errors.append(f"{job.source.name}: {exc}")
            else:
                if outcome == "copied":
                    result.copied += 1
                else:
                    result.converted += 1
                if warning:
                    result.errors.append(f"{job.target.name}: convertito, ma {warning}")
                try:
                    result.source_bytes += job.source.stat().st_size
                    result.output_bytes += job.target.stat().st_size
                except OSError:
                    pass
            if progress and not cancelled():
                progress(done, total, f"Conversione: {job.source.name}")
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
        result.cancelled = cancelled()

    # nella cartella separata porta anche le immagini di copertina (cover.jpg…)
    if output_dir is not None:
        for source_folder, target_folder in done_folders.items():
            try:
                cover = md.find_cover_file(source_folder)
                if cover is not None and not (target_folder / cover.name).exists():
                    shutil.copy2(cover, target_folder / cover.name)
            except OSError:
                pass
    return result


def format_size(n: int) -> str:
    """Dimensione leggibile, es. «1,2 GB»."""
    size = float(n)
    for unit in ("byte", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            text = f"{size:.0f}" if unit in ("byte", "KB") else f"{size:.1f}"
            return f"{text.replace('.', ',')} {unit}"
        size /= 1024
    return f"{n} byte"
