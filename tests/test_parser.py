"""Unit test del parser dei nomi file e cartelle, con i pattern reali della libreria."""

from organizer.filename_parser import build_filename, parse_filename, parse_folder_name, sanitize_component


def test_pac_flac_pattern():
    r = parse_filename("01. - Keep Ya Head Up.flac")
    assert r.track == 1
    assert r.title == "Keep Ya Head Up"
    assert r.artist is None


def test_compilation_dup_suffix():
    r = parse_filename("02 - Gene Vincent - Be- Bop - A - Lula(1).mp3",
                       album_hint="Greatest Hits Collection 50s")
    assert r.track == 2
    assert r.artist == "Gene Vincent"
    assert r.title == "Be- Bop - A - Lula"


def test_compilation_simple():
    r = parse_filename("03 - Eddy Cochran - Cmon Everybody(1).mp3")
    assert r.track == 3
    assert r.artist == "Eddy Cochran"
    assert r.title == "Cmon Everybody"


def test_live_kept():
    r = parse_filename("04 - Elvis Presley - Thats All Right Mama (Live)(1).mp3")
    assert r.track == 4
    assert r.artist == "Elvis Presley"
    assert r.title == "Thats All Right Mama (Live)"


def test_two_digit_track():
    r = parse_filename("19 - Dean Martin - You Belong To Me(1).mp3")
    assert r.track == 19
    assert r.artist == "Dean Martin"
    assert r.title == "You Belong To Me"


def test_artist_title():
    r = parse_filename("Some Artist - Some Title.mp3")
    assert r.artist == "Some Artist"
    assert r.title == "Some Title"


def test_title_only():
    r = parse_filename("Just A Title.mp3")
    assert r.artist is None
    assert r.title == "Just A Title"


def test_album_in_name_without_hint():
    r = parse_filename("03 - Artist - Album - Title.mp3")
    assert r.track == 3
    assert r.artist == "Artist"
    assert r.album == "Album"
    assert r.title == "Title"


def test_junk_official_video():
    r = parse_filename("Song Name (Official Video).mp3")
    assert r.title == "Song Name"


def test_junk_hq_bracket():
    r = parse_filename("Song Name [HQ].mp3")
    assert r.title == "Song Name"


def test_parenthesized_track():
    r = parse_filename("(05) Strange Song.flac")
    assert r.track == 5
    assert r.title == "Strange Song"


def test_year_not_track():
    # un anno in 4 cifre non e' un numero di traccia
    r = parse_filename("1979 - Smashing Song.mp3")
    assert r.track is None


def test_folder_artist_album_disc():
    h = parse_folder_name("2Pac - Greatest Hits [CD1]")
    assert h.artist == "2Pac"
    assert h.album == "Greatest Hits"
    assert h.disc == 1


def test_folder_compilation():
    h = parse_folder_name("Greatest Hits Collection 50s")
    assert h.artist is None
    assert h.album == "Greatest Hits Collection 50s"


def test_folder_with_year():
    h = parse_folder_name("Pink Floyd - The Dark Side Of The Moon (1973)")
    assert h.artist == "Pink Floyd"
    assert h.album == "The Dark Side Of The Moon"
    assert h.year == 1973


def test_build_filename_default_format():
    name = build_filename(3, "2Pac", "Greatest Hits", "Keep Ya Head Up", ".flac")
    assert name == "03 - 2Pac - Greatest Hits - Keep Ya Head Up.flac"


def test_build_filename_pads_to_max():
    name = build_filename(103, "A", "B", "T", ".mp3", width=3)
    assert name == "103 - A - B - T.mp3"


def test_build_filename_without_track():
    name = build_filename(None, "A", "B", "T", ".mp3")
    assert name == "A - B - T.mp3"


def test_sanitize_windows_chars():
    assert sanitize_component('AC/DC: Back In Black?*') == "AC DC Back In Black"
    assert sanitize_component("Nome..") == "Nome"
