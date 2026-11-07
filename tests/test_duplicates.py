"""Test della ricerca dei duplicati e della scelta della copia da tenere."""

from pathlib import Path

import pytest

from organizer import duplicates as dup
from organizer.metadata import AudioInfo
from organizer.planner import PlanItem

FLAC16 = AudioInfo(codec="flac", lossless=True, bits=16, sample_rate=44100, length=200)
FLAC24 = AudioInfo(codec="flac", lossless=True, bits=24, sample_rate=96000, length=200)
WAV16 = AudioInfo(codec="pcm", lossless=True, bits=16, sample_rate=44100, length=200)
MP3_320 = AudioInfo(codec="mp3", bitrate=320, sample_rate=44100, length=200.5)
MP3_192 = AudioInfo(codec="mp3", bitrate=192, sample_rate=44100, length=201)
OPUS_160 = AudioInfo(codec="opus", bitrate=160, sample_rate=48000, length=200)


def item(path, audio, title="Keep Ya Head Up", artist="2Pac", album="Greatest Hits", size=None, tags=None):
    p = Path(path)
    if size is not None:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x" * size)
    it = PlanItem(path=p, original=dict(tags or {}), folder_hint=None, parsed=None, audio=audio)
    it.components = {"title": title, "artist": artist, "album": album}
    return it


def test_normalize():
    assert dup.normalize("Beyoncé – Halo (feat. Jay-Z)") == "beyonce halo"
    assert dup.normalize("Simon & Garfunkel") == "simon and garfunkel"
    assert dup.normalize("Left Behind") == "left behind"        # «ft» dentro una parola resta
    assert dup.normalize("Song (Live)") == "song live"           # le versioni restano diverse


def test_groups_same_song_in_different_formats(tmp_path):
    a = item(tmp_path / "A" / "01 - Keep Ya Head Up.flac", FLAC16)
    b = item(tmp_path / "A" / "01 - Keep Ya Head Up.mp3", MP3_320)
    c = item(tmp_path / "B" / "keep ya head up (feat. Dave Hollister).mp3", MP3_192,
             title="Keep Ya Head Up (feat. Dave Hollister)")
    other = item(tmp_path / "A" / "02 - Temptations.flac", FLAC16, title="Temptations")
    groups = dup.find_duplicates([a, b, c, other])
    assert len(groups) == 1
    assert {it.path for it in groups[0].items} == {a.path, b.path, c.path}
    assert groups[0].keep == {a.path}                              # lossless batte MP3 320


def test_versions_durations_and_albums_stay_separate(tmp_path):
    base = item(tmp_path / "a.mp3", MP3_320)
    live = item(tmp_path / "b.mp3", MP3_320, title="Keep Ya Head Up (Live)")
    edit = item(tmp_path / "c.mp3", AudioInfo(codec="mp3", bitrate=320, length=180))
    other_album = item(tmp_path / "d.mp3", MP3_192, album="Greatest Hits Collection")
    assert dup.find_duplicates([base, live, edit, other_album]) == []
    groups = dup.find_duplicates([base, live, edit, other_album], across_albums=True)
    assert [{it.path.name for it in g.items} for g in groups] == [{"a.mp3", "d.mp3"}]


def test_missing_album_matches_any_album(tmp_path):
    a = item(tmp_path / "a.flac", FLAC16)
    b = item(tmp_path / "b.mp3", MP3_320, album=None)
    assert len(dup.find_duplicates([a, b])) == 1


def test_without_artist_only_same_folder(tmp_path):
    a = item(tmp_path / "X" / "Intro.mp3", MP3_320, title="Intro", artist=None, album=None)
    b = item(tmp_path / "Y" / "Intro.mp3", MP3_320, title="Intro", artist=None, album=None)
    c = item(tmp_path / "X" / "Intro(1).mp3", MP3_320, title="Intro", artist=None, album=None)
    groups = dup.find_duplicates([a, b, c])
    assert len(groups) == 1 and {it.path for it in groups[0].items} == {a.path, c.path}
    assert groups[0].keep == {a.path}                              # «(1)» e' la copia


@pytest.mark.parametrize("audios,best", [
    ((FLAC16, FLAC24), 1),            # piu bit e frequenza
    ((WAV16, FLAC16), 1),             # stessa qualita: FLAC (tag, spazio)
    ((MP3_320, OPUS_160), 0),         # Opus 160 vale circa un MP3 256: vince l'MP3 320
    ((MP3_320, FLAC16), 1),           # lossless batte sempre i formati compressi
])
def test_quality_ranking(tmp_path, audios, best):
    items = [item(tmp_path / f"{i}.x", a) for i, a in enumerate(audios)]
    assert dup.choose_keep(dup.DuplicateGroup(items), dup.RULE_BEST) is items[best]


def test_opus_beats_mp3_at_similar_bitrate(tmp_path):
    items = [item(tmp_path / "a.mp3", MP3_192), item(tmp_path / "b.opus", OPUS_160)]
    assert dup.choose_keep(dup.DuplicateGroup(items), dup.RULE_BEST).path.name == "b.opus"


def test_rules_smallest_and_preferred_format(tmp_path):
    flac = item(tmp_path / "a.flac", FLAC16, size=3000)
    mp3 = item(tmp_path / "a.mp3", MP3_320, size=1000)
    opus = item(tmp_path / "a.opus", OPUS_160, size=500)
    groups = dup.find_duplicates([flac, mp3, opus])
    group = groups[0]
    assert dup.choose_keep(group, dup.RULE_SMALLEST) is opus
    assert dup.choose_keep(group, "codec:mp3") is mp3
    assert dup.choose_keep(group, "codec:wma") is flac              # formato assente: la migliore
    labels = dict(dup.rule_choices(groups))
    assert "codec:mp3" in labels and "codec:wma" not in labels
    dup.apply_rule(group, "codec:mp3")
    assert {it.path for it in group.to_remove} == {flac.path, opus.path}


def test_remove_files_uses_bin_and_reports_errors(tmp_path):
    a, b = tmp_path / "a.mp3", tmp_path / "b.mp3"
    a.write_bytes(b"x" * 10)
    b.write_bytes(b"y" * 20)
    binned = []

    def trash(path):
        if path.name == "b.mp3":
            raise OSError("bloccato")
        binned.append(path)
        path.unlink()

    result = dup.remove_files([a, b], trash=trash)
    assert (result.removed, result.freed_bytes) == (1, 10)
    assert binned == [a] and b.exists() and "b.mp3" in result.errors[0]
