"""Test di regressione per i problemi trovati durante la revisione:
correzioni manuali, esclusioni, rinomine solo maiuscole, annullamento dopo
la riorganizzazione, analisi senza connessione, nomi doppi, pattern non validi.
"""

import os

import pytest
import requests

import organizer.applier as applier
from organizer import metadata as md
from organizer.applier import apply, journal_info, undo
from organizer.planner import analyze, pattern_error, replan

from test_pipeline import create_library, make_mp3


@pytest.fixture()
def library(tmp_path, monkeypatch):
    root = create_library(tmp_path)
    monkeypatch.setattr(applier, "JOURNAL_DIR", tmp_path)
    monkeypatch.setattr(applier, "JOURNAL_PATH", tmp_path / "undo.json")
    return root


def _replan(plan, **kw):
    opts = dict(filename_pattern=plan.filename_pattern, folder_pattern=plan.folder_pattern,
                rename_folders=plan.rename_folders, reorganize=plan.reorganize)
    opts.update(kw)
    return replan(plan, **opts)


def _item(plan, needle):
    return next(it for it in plan.items if needle in it.path.name)


# --- correzioni manuali -----------------------------------------------------------

def test_manual_edit_changes_name_and_tags(library):
    plan = analyze(library, online=False)
    item = _item(plan, "Keep")
    item.manual = {"title": "Keep Ya Head Up (Remix)"}
    _replan(plan)
    assert item.components["title"] == "Keep Ya Head Up (Remix)"
    assert item.sources["title"] == "manuale"
    assert item.proposed["title"] == "Keep Ya Head Up (Remix)"
    assert item.new_name == "01 - 2Pac - Greatest Hits - Keep Ya Head Up (Remix).flac"


def test_manual_edit_can_be_reverted(library):
    plan = analyze(library, online=False)
    item = _item(plan, "Keep")
    item.manual = {"title": "Altro"}
    _replan(plan)
    item.manual = {}
    _replan(plan)
    assert item.components["title"] == "Keep Ya Head Up"
    assert item.sources["title"] == "nome file"


def test_manual_artist_updates_default_albumartist(library):
    plan = analyze(library, online=False)
    item = _item(plan, "In The Flesh")  # albumartist dal tag: resta quello
    item.manual = {"artist": "Roger Waters"}
    _replan(plan)
    assert item.proposed["artist"] == "Roger Waters"
    assert item.proposed["albumartist"] == "Pink Floyd"


def test_manual_edit_applied_to_file(library):
    plan = analyze(library, online=False)
    item = _item(plan, "Keep")
    item.manual = {"year": 1993, "genre": "Hip-Hop"}
    _replan(plan)
    apply(plan)
    tags = md.read_tags(library / "2Pac - Greatest Hits [CD1]" / "01 - 2Pac - Greatest Hits - Keep Ya Head Up.flac")
    assert tags["year"] == 1993
    assert tags["genre"] == "Hip-Hop"


def test_manual_clear_removes_tag_and_undo_restores_it(library):
    plan = analyze(library, online=False)
    item = _item(plan, "In The Flesh")
    assert item.original["year"] == 1979
    item.manual = {"year": None}
    _replan(plan)
    assert item.tags_to_clear == ["year"]
    assert item.needs_change
    journal = apply(plan).journal
    assert md.read_tags(item.path)["year"] is None
    undo(journal)
    assert md.read_tags(item.path)["year"] == 1979


# --- esclusioni -------------------------------------------------------------------

def test_excluded_item_is_not_touched(library):
    plan = analyze(library, online=False)
    item = _item(plan, "Keep")
    item.excluded = True
    assert item.needs_change and not item.will_change
    result = apply(plan)
    assert (library / "2Pac - Greatest Hits [CD1]" / "01. - Keep Ya Head Up.flac").is_file()
    assert md.read_tags(item.path)["title"] is None
    assert result.files_changed == 6


def test_fully_excluded_folder_is_not_renamed(library):
    plan = analyze(library, rename_folders=True, online=False)
    for it in plan.items:
        if "2Pac" in str(it.path.parent):
            it.excluded = True
    _replan(plan)
    assert all("2Pac" not in fr.folder.name for fr in plan.folder_renames)


# --- maiuscole/minuscole (Windows non le distingue) -------------------------------

def test_case_only_rename_is_applied(library):
    album = library / "2Pac - Greatest Hits [CD1]"
    (album / "01. - Keep Ya Head Up.flac").rename(album / "01 - 2pac - greatest hits - keep ya head up.flac")
    plan = analyze(library, filename_pattern="{nn} - {titolo}", online=False)
    item = _item(plan, "keep ya head up")
    item.manual = {"title": "Keep Ya Head Up"}
    _replan(plan)
    assert item.new_name == "01 - Keep Ya Head Up.flac"
    apply(plan)
    assert "01 - Keep Ya Head Up.flac" in os.listdir(album)


def test_case_only_folder_rename(library):
    old = library / "Pink Floyd - The Wall [CD1]"
    lower = library / "pink floyd - the wall"
    old.rename(lower)
    plan = analyze(library, rename_folders=True, folder_pattern="{artista} - {album}", online=False)
    mapping = {fr.folder.name: fr.new_name for fr in plan.folder_renames}
    assert mapping["pink floyd - the wall"] == "Pink Floyd - The Wall"
    journal = apply(plan).journal
    assert "Pink Floyd - The Wall" in os.listdir(library)  # niente " (2)"
    undo(journal)
    assert "pink floyd - the wall" in os.listdir(library)


# --- annullamento ------------------------------------------------------------------

def test_undo_after_reorganize_removes_empty_folders(library):
    before = sorted(p.relative_to(library) for p in library.rglob("*"))
    plan = analyze(library, reorganize=True, online=False)
    undo(apply(plan).journal)
    after = sorted(p.relative_to(library) for p in library.rglob("*"))
    assert after == before


def test_undo_archives_journal(library):
    plan = analyze(library, online=False)
    journal = apply(plan).journal
    info = journal_info(journal)
    assert info["files"] == 7
    result = undo(journal)
    assert result.errors == []
    assert not journal.exists()
    assert (journal.parent / applier.UNDONE_NAME).exists()
    assert journal_info(journal) is None


def test_undo_restores_tags_when_rename_failed(library, monkeypatch):
    real_replace = os.replace

    def failing_replace(src, dst):
        if str(src).lower().endswith((".flac", ".mp3", ".m4a")):
            raise PermissionError(13, "File in uso")
        return real_replace(src, dst)

    monkeypatch.setattr(applier.os, "replace", failing_replace)
    plan = analyze(library, online=False)
    result = apply(plan)
    assert result.errors  # gli errori di rinomina vengono riportati
    original = library / "2Pac - Greatest Hits [CD1]" / "01. - Keep Ya Head Up.flac"
    assert md.read_tags(original)["title"] == "Keep Ya Head Up"  # tag scritti, nome no

    monkeypatch.setattr(applier.os, "replace", real_replace)
    undo(result.journal)
    assert md.read_tags(original)["title"] is None


def test_apply_reports_cancel(library):
    import threading
    event = threading.Event()
    event.set()
    plan = analyze(library, online=False)
    result = apply(plan, cancel_event=event)
    assert result.cancelled
    assert result.files_changed == 0


# --- rete assente ------------------------------------------------------------------

def test_analysis_without_network_does_not_crash(library, monkeypatch):
    calls = []

    def no_network(self, *args, **kwargs):
        calls.append(args)
        raise requests.ConnectionError("rete assente")

    monkeypatch.setattr(requests.Session, "get", no_network)
    for i in range(6):
        make_mp3(library / f"Brano sconosciuto {i}.mp3")
    plan = analyze(library, online=True, download_covers=True)
    assert len(plan.items) == 14
    assert plan.notices and "non disponibile" in plan.notices[0]
    assert len(calls) <= 3  # dopo pochi errori smette di provare


# --- nomi doppi ----------------------------------------------------------------------

def test_duplicate_target_names_are_flagged(library):
    album = library / "2Pac - Greatest Hits [CD1]"
    make_mp3(album / "04 - Intro.mp3")
    make_mp3(album / "05 - Intro.mp3")
    plan = analyze(library, filename_pattern="{titolo}", online=False)
    intros = [it for it in plan.items if "Intro" in it.path.name]
    assert all(any("già usato" in w for w in it.warnings) for it in intros)
    apply(plan)
    names = sorted(p.name for p in album.iterdir() if p.name.startswith("Intro"))
    assert names == ["Intro (2).mp3", "Intro.mp3"]


# --- validazione dei pattern ---------------------------------------------------------

@pytest.mark.parametrize("pattern,kind,ok", [
    ("{nn} - {titolo}", "file", True),
    ("{titolo}", "file", True),
    ("{nn} - {artista}", "file", False),       # manca il titolo
    ("{nn} - {title}", "file", False),          # campo sconosciuto
    ("{nn} - {titolo", "file", False),          # graffa non chiusa
    ("{artista}\\{titolo}", "file", False),     # separatore di cartella
    ("{titolo}?", "file", False),               # carattere vietato
    ("", "file", False),
    ("{artista} - {album} ({disco})", "folder", True),
    ("{artista}", "folder", False),             # manca l'album
])
def test_pattern_validation(pattern, kind, ok):
    assert (pattern_error(pattern, kind=kind) is None) is ok


# --- tag nei WAV ------------------------------------------------------------------------

def make_wav(path):
    import wave
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(44100)
        w.writeframes(b"\x00\x00" * 2 * 4410)


def test_wav_without_tags_stays_valid_after_tagging(tmp_path):
    """I tag di un WAV vanno nel blocco RIFF: scritti in testa al file lo rovinavano."""
    import wave
    from mutagen.wave import WAVE

    path = tmp_path / "brano.wav"
    make_wav(path)
    md.write_tags(path, {"title": "Titolo", "artist": "Artista", "track": 2})
    md.embed_cover(path, b"\xff\xd8jpeg")
    assert md.read_tags(path)["title"] == "Titolo"
    assert md.has_embedded_cover(path)
    md.clear_tags(path, ["artist"])
    md.remove_embedded_cover(path)
    assert md.read_tags(path)["artist"] is None and md.read_tags(path)["title"] == "Titolo"
    assert not md.has_embedded_cover(path)
    WAVE(path)  # ancora un RIFF valido
    with wave.open(str(path), "rb") as w:
        assert w.getnframes() == 4410
