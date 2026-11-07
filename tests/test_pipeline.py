"""Test end-to-end della pipeline: scan -> piano -> applica -> annulla,
su file audio minimi generati al volo (senza rete).
"""

import struct

import pytest

from organizer import metadata as md
from organizer.applier import JOURNAL_PATH, apply, undo
from organizer.planner import DEFAULT_FOLDER_PATTERN, analyze, replan


# --- generazione fixture -------------------------------------------------------

def make_mp3(path):
    frame = bytes([0xFF, 0xFB, 0x90, 0x00]) + b"\x00" * 413  # 128 kbps 44.1 kHz silenzio
    path.write_bytes(frame * 4)


def make_flac(path):
    packed = (44100 << 44) | ((2 - 1) << 41) | ((16 - 1) << 36) | 0
    streaminfo = (
        struct.pack(">HH", 4096, 4096) + b"\x00" * 3 + b"\x00" * 3
        + packed.to_bytes(8, "big") + b"\x00" * 16
    )
    path.write_bytes(b"fLaC" + b"\x80" + struct.pack(">I", 34)[1:] + streaminfo)


def make_m4a(path):
    def box(name, payload=b""):
        return struct.pack(">I", 8 + len(payload)) + name + payload

    mvhd = b"\x00" * 4 + b"\x00" * 8 + struct.pack(">II", 1000, 1000) \
        + struct.pack(">I", 0x00010000) + b"\x01\x00" + b"\x00" * 10 + b"\x00" * 36 \
        + b"\x00" * 24 + struct.pack(">I", 2)
    path.write_bytes(box(b"ftyp", b"M4A \x00\x00\x02\x00isomiso2mp41") + box(b"moov", box(b"mvhd", mvhd)))


MAKERS = {".mp3": make_mp3, ".flac": make_flac, ".m4a": make_m4a}


def create_library(tmp_path: "object") -> "object":
    """Crea una mini-libreria con gli stessi pattern degli screenshot."""
    root = tmp_path / "Musica"
    album = root / "2Pac - Greatest Hits [CD1]"
    album.mkdir(parents=True)
    for name in (
        "01. - Keep Ya Head Up.flac",
        "02 - 2 Of Amerikaz Most Wanted.flac",
        "03. - Temptations.flac",
    ):
        MAKERS[".flac"](album / name)

    comp = root / "Greatest Hits Collection 50s"
    comp.mkdir()
    for name in (
        "02 - Gene Vincent - Be- Bop - A - Lula(1).mp3",
        "03 - Eddy Cochran - Cmon Everybody(1).mp3",
        "04 - Elvis Presley - Thats All Right Mama (Live)(1).mp3",
        "05 - The Everly Brothers - Bye Bye Love(1).mp3",
    ):
        MAKERS[".mp3"](comp / name)

    # album con tag gia corretti e nome gia giusto: non deve essere toccato
    ok_album = root / "Pink Floyd - The Wall [CD1]"
    ok_album.mkdir()
    make_m4a(ok_album / "01 - Pink Floyd - The Wall - In The Flesh.m4a")
    md.write_tags(ok_album / "01 - Pink Floyd - The Wall - In The Flesh.m4a", {
        "title": "In The Flesh", "artist": "Pink Floyd", "album": "The Wall",
        "albumartist": "Pink Floyd", "track": 1, "year": 1979,
    })
    return root


@pytest.fixture()
def library(tmp_path):
    root = create_library(tmp_path)
    # il diario va in LOCALAPPDATA: nei test punta a una cartella temporanea
    import organizer.applier as applier
    applier.JOURNAL_PATH = tmp_path / "undo.json"
    return root


# --- piano ---------------------------------------------------------------------

def test_plan_album_single_artist(library):
    plan = analyze(library, online=False)
    album_items = [it for it in plan.items if "2Pac" in str(it.path)]
    first = next(it for it in album_items if "Keep" in it.path.name)
    assert first.components["title"] == "Keep Ya Head Up"
    assert first.components["artist"] == "2Pac"          # dal nome cartella
    assert first.components["album"] == "Greatest Hits"  # dal nome cartella, senza [CD1]
    assert first.components["track"] == 1
    assert first.new_name == "01 - 2Pac - Greatest Hits - Keep Ya Head Up.flac"


def test_plan_compilation_artists_from_filename(library):
    plan = analyze(library, online=False)
    gene = next(it for it in plan.items if "Gene Vincent" in it.path.name)
    assert gene.components["artist"] == "Gene Vincent"
    assert gene.components["title"] == "Be- Bop - A - Lula"
    assert gene.components["album"] == "Greatest Hits Collection 50s"
    assert gene.components["albumartist"] == "Various Artists"
    assert gene.new_name == "02 - Gene Vincent - Greatest Hits Collection 50s - Be- Bop - A - Lula.mp3"


def test_plan_skips_already_correct(library):
    plan = analyze(library, online=False)
    ok_items = [it for it in plan.items if "In The Flesh" in it.path.name]
    assert len(ok_items) == 1
    assert not ok_items[0].needs_change


def test_plan_reorganize_paths(library):
    plan = analyze(library, reorganize=True, online=False)
    first = next(it for it in plan.items if "Keep" in it.path.name)
    assert first.new_dir == library / "2Pac" / "Greatest Hits (CD1)"
    gene = next(it for it in plan.items if "Gene Vincent" in it.path.name)
    assert gene.new_dir == library / "Various Artists" / "Greatest Hits Collection 50s"


# --- applica e annulla -----------------------------------------------------------

def test_apply_and_undo_roundtrip(library):
    plan = analyze(library, online=False)
    journal = apply(plan)
    assert journal.exists()

    keep = library / "2Pac - Greatest Hits [CD1]" / "01 - 2Pac - Greatest Hits - Keep Ya Head Up.flac"
    assert keep.is_file()
    tags = md.read_tags(keep)
    assert tags["title"] == "Keep Ya Head Up"
    assert tags["artist"] == "2Pac"
    assert tags["track"] == 1
    # il vecchio nome non esiste piu
    assert not (library / "2Pac - Greatest Hits [CD1]" / "01. - Keep Ya Head Up.flac").exists()

    # annullamento: tutto torna com'era
    restored = undo(journal)
    assert restored >= 6
    original = library / "2Pac - Greatest Hits [CD1]" / "01. - Keep Ya Head Up.flac"
    assert original.is_file()
    tags_back = md.read_tags(original)
    assert tags_back["title"] is None
    assert not keep.exists()


def test_apply_compilation_tags(library):
    plan = analyze(library, online=False)
    apply(plan)
    gene = library / "Greatest Hits Collection 50s" / (
        "02 - Gene Vincent - Greatest Hits Collection 50s - Be- Bop - A - Lula.mp3"
    )
    assert gene.is_file()
    tags = md.read_tags(gene)
    assert tags["artist"] == "Gene Vincent"
    assert tags["albumartist"] == "Various Artists"
    assert tags["album"] == "Greatest Hits Collection 50s"
    assert tags["track"] == 2


def test_apply_reorganize_moves_files(library):
    plan = analyze(library, reorganize=True, online=False)
    apply(plan)
    moved = library / "2Pac" / "Greatest Hits (CD1)" / "01 - 2Pac - Greatest Hits - Keep Ya Head Up.flac"
    assert moved.is_file()
    # la vecchia cartella, svuotata (nessun file non-audio), viene rimossa
    assert not (library / "2Pac - Greatest Hits [CD1]").exists()


def test_playlist_regenerated(library):
    album = library / "2Pac - Greatest Hits [CD1]"
    (album / "album.m3u").write_text("01. - Keep Ya Head Up.flac\n02 - 2 Of Amerikaz Most Wanted.flac\n", encoding="utf-8")
    plan = analyze(library, online=False)
    apply(plan)
    content = (album / "album.m3u").read_text(encoding="utf-8")
    assert "01 - 2Pac - Greatest Hits - Keep Ya Head Up.flac" in content
    assert "01. - Keep Ya Head Up.flac" not in content


def test_tag_only_change_no_rename(library):
    # file con nome gia nel formato giusto ma tag mancanti: solo tag vengono scritti
    album = library / "2Pac - Greatest Hits [CD1]"
    make_flac(album / "04 - 2Pac - Greatest Hits - Unconditional Love.flac")
    plan = analyze(library, online=False)
    item = next(it for it in plan.items if "Unconditional" in it.path.name)
    assert item.needs_change
    assert item.new_name == item.path.name  # nome invariato
    apply(plan)
    tags = md.read_tags(album / "04 - 2Pac - Greatest Hits - Unconditional Love.flac")
    assert tags["title"] == "Unconditional Love"
    assert tags["artist"] == "2Pac"


# --- pattern personalizzati e rinomina cartelle ---------------------------------

def test_pattern_titolo_only(library):
    plan = analyze(library, filename_pattern="{titolo}", online=False)
    first = next(it for it in plan.items if "Keep" in it.path.name)
    assert first.new_name == "Keep Ya Head Up.flac"


def test_pattern_nn_titolo(library):
    plan = analyze(library, filename_pattern="{nn} - {titolo}", online=False)
    gene = next(it for it in plan.items if "Gene Vincent" in it.path.name)
    assert gene.new_name == "02 - Be- Bop - A - Lula.mp3"


def test_replan_changes_names_without_rescan(library):
    plan = analyze(library, online=False)
    n = len(plan.items)
    replan(plan, filename_pattern="{nn} - {titolo}",
           folder_pattern=DEFAULT_FOLDER_PATTERN, rename_folders=False, reorganize=False)
    assert len(plan.items) == n
    gene = next(it for it in plan.items if "Gene Vincent" in it.path.name)
    assert gene.new_name == "02 - Be- Bop - A - Lula.mp3"
    # i tag proposti non cambiano con il pattern
    assert gene.proposed["artist"] == "Gene Vincent"


def test_folder_rename_plan(library):
    plan = analyze(library, rename_folders=True,
                   folder_pattern="{artista} - {album} ({disco})", online=False)
    mapping = {fr.folder.name: fr.new_name for fr in plan.folder_renames}
    assert mapping["2Pac - Greatest Hits [CD1]"] == "2Pac - Greatest Hits (CD1)"
    assert mapping["Pink Floyd - The Wall [CD1]"] == "Pink Floyd - The Wall (CD1)"
    # compilation: artista = Various Artists, nessun disco
    assert mapping["Greatest Hits Collection 50s"] == "Various Artists - Greatest Hits Collection 50s"


def test_folder_rename_pattern_album_only(library):
    plan = analyze(library, rename_folders=True, folder_pattern="{album}", online=False)
    mapping = {fr.folder.name: fr.new_name for fr in plan.folder_renames}
    assert mapping["2Pac - Greatest Hits [CD1]"] == "Greatest Hits (CD1)"
    # il nome cartella gia corretto non genera una rinomina
    assert "Greatest Hits Collection 50s" not in mapping


def test_reorganize_disables_folder_rename(library):
    plan = analyze(library, reorganize=True, rename_folders=True, online=False)
    assert plan.rename_folders is False
    assert plan.folder_renames == []


def test_apply_folder_rename_and_undo(library):
    album = library / "2Pac - Greatest Hits [CD1]"
    (album / "ripping.log").write_text("EAC log", encoding="utf-8")  # file non audio segue la cartella
    plan = analyze(library, rename_folders=True,
                   folder_pattern="{artista} - {album} ({disco})", online=False)
    journal = apply(plan)

    new_folder = library / "2Pac - Greatest Hits (CD1)"
    assert new_folder.is_dir()
    assert (new_folder / "ripping.log").is_file()   # il file non audio e' arrivato insieme
    assert (new_folder / "01 - 2Pac - Greatest Hits - Keep Ya Head Up.flac").is_file()
    assert not album.exists()

    undo(journal)
    assert album.is_dir()
    assert (album / "ripping.log").is_file()
    assert (album / "01. - Keep Ya Head Up.flac").is_file()
    assert not new_folder.exists()


def test_folder_rename_in_place_keeps_non_audio(library):
    # con rinomina cartelle attiva (senza riorganizzazione) i file NON cambiano cartella
    plan = analyze(library, rename_folders=True,
                   folder_pattern="{artista} - {album} ({disco})", online=False)
    first = next(it for it in plan.items if "Keep" in it.path.name)
    assert first.new_dir is None
    assert first.new_path.parent == library / "2Pac - Greatest Hits [CD1]"
