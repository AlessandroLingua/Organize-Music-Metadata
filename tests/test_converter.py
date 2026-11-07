"""Test della conversione audio: piano dei file di destinazione (senza FFmpeg)
e conversioni reali in tutti i formati (solo se FFmpeg e' installato)."""

import subprocess
import threading
from pathlib import Path

import pytest

from organizer import converter as cv
from organizer import metadata as md
from organizer.metadata import AudioInfo

FFMPEG = cv.find_ffmpeg()
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="FFmpeg non installato")

TAGS = {"title": "Keep Ya Head Up", "artist": "2Pac", "album": "Greatest Hits",
        "albumartist": "2Pac", "track": 3, "year": 1998, "genre": "Hip-Hop"}
# PNG 1x1 valido, usato come copertina
COVER = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010806000000"
    "1f15c4890000000d49444154789c6360f8cfc0f01f0005000201e2216bc80000000049454e44ae426082")


# --- piano dei file di destinazione ------------------------------------------------

def touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x")
    return path


def test_plan_mirrors_subfolders_in_output_dir(tmp_path):
    root = tmp_path / "Musica"
    a = touch(root / "2Pac - Greatest Hits" / "01 - Keep Ya Head Up.flac")
    b = touch(root / "Sfuso.wav")
    out = tmp_path / "Musica (MP3)"
    jobs = cv.plan_jobs([a, b], root, cv.FORMATS["mp3"], out)
    assert [j.target for j in jobs] == [out / "2Pac - Greatest Hits" / "01 - Keep Ya Head Up.mp3",
                                        out / "Sfuso.mp3"]
    assert all(j.skip_reason is None for j in jobs)


def test_plan_beside_originals_never_overwrites(tmp_path):
    root = tmp_path / "Musica"
    flac = touch(root / "brano.flac")
    mp3 = touch(root / "brano.mp3")      # gia MP3: e' lui stesso la destinazione
    other = touch(root / "altro.flac")
    touch(root / "altro.mp3")            # file esistente non selezionato
    jobs = {j.source.name: j for j in cv.plan_jobs([flac, mp3, other], root, cv.FORMATS["mp3"], None)}
    assert jobs["brano.mp3"].skip_reason.startswith("è già un file MP3")
    assert jobs["brano.flac"].skip_reason == "esiste già nella destinazione"
    assert jobs["altro.flac"].skip_reason == "esiste già nella destinazione"


def test_plan_same_name_different_formats_get_suffix(tmp_path):
    root = tmp_path / "Musica"
    a, b = touch(root / "brano.flac"), touch(root / "brano.wav")
    jobs = cv.plan_jobs([a, b], root, cv.FORMATS["mp3"], tmp_path / "out")
    assert [j.target.name for j in jobs] == ["brano.mp3", "brano (2).mp3"]


def test_plan_existing_target_skipped_unless_overwrite(tmp_path):
    root = tmp_path / "Musica"
    src = touch(root / "brano.flac")
    out = tmp_path / "out"
    touch(out / "brano.mp3")
    assert cv.plan_jobs([src], root, cv.FORMATS["mp3"], out)[0].skip_reason
    assert cv.plan_jobs([src], root, cv.FORMATS["mp3"], out, overwrite=True)[0].skip_reason is None
    # accanto agli originali la sostituzione non e' mai permessa
    touch(root / "brano.mp3")
    assert cv.plan_jobs([src], root, cv.FORMATS["mp3"], None, overwrite=True)[0].skip_reason


def test_output_dir_validation(tmp_path):
    root = tmp_path / "Musica"
    root.mkdir()
    assert cv.output_dir_error("", root)
    assert cv.output_dir_error("relativa", root)
    assert cv.output_dir_error(root, root)
    assert cv.output_dir_error(root / "Convertiti", root)
    assert cv.output_dir_error(tmp_path / "Musica (MP3)", root) is None
    assert cv.default_output_dir(root, cv.FORMATS["aac"]) == tmp_path / "Musica (AAC)"


# --- scelte di codifica ----------------------------------------------------------------

def test_can_copy_only_same_codec_and_not_better_quality():
    mp3 = cv.FORMATS["mp3"]
    assert cv.can_copy(AudioInfo(codec="mp3", bitrate=192), mp3, mp3.quality("320"))
    assert not cv.can_copy(AudioInfo(codec="mp3", bitrate=320), mp3, mp3.quality("128"))
    assert not cv.can_copy(AudioInfo(codec="aac", bitrate=128), mp3, mp3.quality("320"))
    flac = cv.FORMATS["flac"]
    hires = AudioInfo(codec="flac", lossless=True, bits=24, sample_rate=96000)
    cd = AudioInfo(codec="flac", lossless=True, bits=16, sample_rate=44100)
    assert cv.can_copy(hires, flac, flac.quality("original"))
    assert not cv.can_copy(hires, flac, flac.quality("cd"))
    assert cv.can_copy(cd, flac, flac.quality("cd"))
    # ALAC e AAC hanno la stessa estensione ma codec diversi
    assert not cv.can_copy(AudioInfo(codec="aac", bitrate=256), cv.FORMATS["alac"],
                           cv.FORMATS["alac"].quality("original"))


def test_encoder_args_lossless_depth_and_rate():
    flac = cv.FORMATS["flac"]
    hires = AudioInfo(codec="flac", lossless=True, bits=24, sample_rate=96000)
    lossy = AudioInfo(codec="mp3", bitrate=320, sample_rate=44100)
    assert "-ar" not in cv.encoder_args(flac, flac.quality("original"), hires)
    assert "24" in cv.encoder_args(flac, flac.quality("original"), hires)
    cd = cv.encoder_args(flac, flac.quality("cd"), hires)
    assert cd[cd.index("-sample_fmt") + 1] == "s16" and cd[cd.index("-ar") + 1] == "44100"
    hd = cv.encoder_args(flac, flac.quality("hd"), AudioInfo(codec="flac", lossless=True, bits=24,
                                                               sample_rate=88200))
    assert hd[hd.index("-ar") + 1] == "44100"
    # da un MP3 non ha senso andare oltre i 16 bit
    assert cv.encoder_args(cv.FORMATS["wav"], cv.FORMATS["wav"].quality("original"), lossy)[1] == "pcm_s16le"


def test_encoder_args_lossy_caps_sample_rate_except_opus():
    hires = AudioInfo(codec="flac", lossless=True, bits=24, sample_rate=96000)
    aac = cv.encoder_args(cv.FORMATS["aac"], cv.FORMATS["aac"].quality("256"), hires)
    assert aac[aac.index("-ar") + 1] == "48000" and aac[aac.index("-b:a") + 1] == "256k"
    assert "-ar" not in cv.encoder_args(cv.FORMATS["opus"], cv.FORMATS["opus"].quality("160"), hires)


def test_format_size():
    assert cv.format_size(512) == "512 byte"
    assert cv.format_size(3 * 1024 * 1024) == "3,0 MB"


# --- conversioni reali --------------------------------------------------------------------

def make_source(path: Path, *, rate=96000, flac_bits=24, tags=TAGS, cover=True) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    codec = ["-c:a", "flac", "-sample_fmt", "s32", "-bits_per_raw_sample", str(flac_bits)] \
        if path.suffix == ".flac" else ["-c:a", "libmp3lame", "-b:a", "320k"]
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", f"sine=frequency=440:duration=1:sample_rate={rate}", "-ac", "2", *codec, str(path)],
                   check=True)
    md.write_tags(path, tags)
    if cover:
        md.embed_cover(path, COVER, "image/png")
    return path


@needs_ffmpeg
@pytest.mark.parametrize("key", list(cv.FORMATS))
def test_convert_every_format_keeps_tags_and_cover(tmp_path, key):
    fmt = cv.FORMATS[key]
    if fmt not in cv.supported_formats(FFMPEG):
        pytest.skip(f"encoder {fmt.encoder} non disponibile")
    root = tmp_path / "Musica"
    src = make_source(root / "Album" / "03 - Keep Ya Head Up.flac")
    out = tmp_path / "out"
    jobs = cv.plan_jobs([src], root, fmt, out)
    result = cv.convert(jobs, fmt, fmt.quality(None), ffmpeg=FFMPEG, output_dir=out,
                        copy_compatible=False)  # anche FLAC -> FLAC va ricodificato

    target = out / "Album" / f"03 - Keep Ya Head Up{fmt.ext}"
    assert result.errors == [] and result.converted == 1, result.errors
    assert target.is_file()
    assert [p.name for p in target.parent.iterdir()] == [target.name]  # nessun file temporaneo
    assert md.read_tags(target) == TAGS
    assert md.read_cover(target) == (COVER, "image/png")
    info = md.audio_info(target)
    assert info.codec == fmt.codec
    if fmt.lossless:
        assert info.bits == 24 and info.sample_rate == 96000   # "come l'originale"
    else:
        assert info.sample_rate == 48000                        # ricampionato da 96 kHz
    assert src.is_file() and md.read_tags(src) == TAGS          # originale intatto


@needs_ffmpeg
def test_convert_lossless_to_cd_quality(tmp_path):
    root = tmp_path / "Musica"
    src = make_source(root / "hires.flac")
    fmt = cv.FORMATS["flac"]
    jobs = cv.plan_jobs([src], root, fmt, tmp_path / "out")
    cv.convert(jobs, fmt, fmt.quality("cd"), ffmpeg=FFMPEG, output_dir=tmp_path / "out")
    info = md.audio_info(tmp_path / "out" / "hires.flac")
    assert (info.bits, info.sample_rate) == (16, 44100)


@needs_ffmpeg
def test_convert_copies_files_already_in_format_and_folder_cover(tmp_path):
    root = tmp_path / "Musica"
    src = make_source(root / "Album" / "brano.mp3", rate=44100, cover=False)
    (root / "Album" / "cover.jpg").write_bytes(b"\xff\xd8jpeg")
    flac = make_source(root / "Album" / "altro.flac", rate=44100, flac_bits=16, cover=False)
    fmt = cv.FORMATS["mp3"]
    out = tmp_path / "out"
    jobs = cv.plan_jobs([src, flac], root, fmt, out)
    result = cv.convert(jobs, fmt, fmt.quality("320"), ffmpeg=FFMPEG, output_dir=out)
    assert (result.copied, result.converted, result.errors) == (1, 1, [])
    assert (out / "Album" / "brano.mp3").read_bytes() == src.read_bytes()
    assert (out / "Album" / "cover.jpg").is_file()
    # senza copertina incorporata viene usata quella della cartella
    assert md.read_cover(out / "Album" / "altro.mp3") == (b"\xff\xd8jpeg", "image/jpeg")


@needs_ffmpeg
def test_convert_reports_broken_files_and_continues(tmp_path):
    root = tmp_path / "Musica"
    broken = root / "rotto.flac"
    broken.parent.mkdir(parents=True)
    broken.write_bytes(b"non e' audio")
    good = make_source(root / "buono.flac", cover=False)
    fmt = cv.FORMATS["mp3"]
    out = tmp_path / "out"
    result = cv.convert(cv.plan_jobs([broken, good], root, fmt, out), fmt, fmt.quality(None),
                        ffmpeg=FFMPEG, output_dir=out)
    assert result.converted == 1 and len(result.errors) == 1 and "rotto.flac" in result.errors[0]
    assert sorted(p.name for p in out.iterdir()) == ["buono.mp3"]


@needs_ffmpeg
def test_convert_cancelled_leaves_no_partial_files(tmp_path):
    root = tmp_path / "Musica"
    sources = [make_source(root / f"{n}.flac", cover=False) for n in range(3)]
    fmt = cv.FORMATS["mp3"]
    out = tmp_path / "out"
    cancel = threading.Event()
    cancel.set()
    result = cv.convert(cv.plan_jobs(sources, root, fmt, out), fmt, fmt.quality(None),
                        ffmpeg=FFMPEG, output_dir=out, cancel_event=cancel)
    assert result.cancelled and result.created == 0
    assert not out.exists() or not any(out.rglob("*"))


# --- copertine OGG/Opus (anche per l'organizzazione) ------------------------------------------

@needs_ffmpeg
@pytest.mark.parametrize("ext", [".ogg", ".opus"])
def test_ogg_cover_roundtrip(tmp_path, ext):
    path = tmp_path / f"brano{ext}"
    codec = "libopus" if ext == ".opus" else "libvorbis"
    subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-f", "lavfi",
                    "-i", "sine=duration=1", "-c:a", codec, str(path)], check=True)
    assert not md.has_embedded_cover(path)
    md.embed_cover(path, COVER, "image/png")
    assert md.has_embedded_cover(path)
    assert md.read_cover(path) == (COVER, "image/png")
    md.remove_embedded_cover(path)
    assert not md.has_embedded_cover(path)
