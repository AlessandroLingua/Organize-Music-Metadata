"""Test dei pattern personalizzabili per nomi file e cartelle."""

import pytest

from organizer.filename_parser import expand_pattern
from organizer.planner import DEFAULT_FOLDER_PATTERN, replan


def test_pattern_titolo_only():
    assert expand_pattern("{titolo}", track=1, title="Keep Ya Head Up") == "Keep Ya Head Up"


def test_pattern_nn_titolo():
    assert expand_pattern("{nn} - {titolo}", track=3, title="Temptations") == "03 - Temptations"


def test_pattern_nn_titolo_senza_traccia():
    # senza numero traccia il separatore superfluo sparisce
    assert expand_pattern("{nn} - {titolo}", track=None, title="Temptations") == "Temptations"


def test_pattern_album_mancante():
    out = expand_pattern("{nn} - {artista} - {album} - {titolo}",
                         track=2, artist="A", album=None, title="T")
    assert out == "02 - A - T"


def test_pattern_anno():
    assert expand_pattern("{nn} - {titolo} ({anno})", track=1, title="T", year=1998) == "01 - T (1998)"
    assert expand_pattern("{nn} - {titolo} ({anno})", track=1, title="T", year=None) == "01 - T"


def test_pattern_disco():
    assert expand_pattern(DEFAULT_FOLDER_PATTERN, artist="A", album="B", disc=1) == "A - B (CD1)"
    assert expand_pattern(DEFAULT_FOLDER_PATTERN, artist="A", album="B", disc=None) == "A - B"


def test_pattern_caratteri_vietati_sanificati():
    out = expand_pattern("{artista} - {titolo}", artist="AC/DC", title="Back in Black?")
    assert out == "AC DC - Back in Black"


def test_pattern_solo_campi_vuoti():
    assert expand_pattern("{nn} - {artista} - {titolo}", track=None) == ""
