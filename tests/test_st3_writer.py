import struct
import sys
from pathlib import Path

import mido
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conversor import convert_file, main  # noqa: E402
from song import load_song  # noqa: E402
from st3 import SYNC_LINE_LOWER, SYNC_LINE_UPPER, SYNC_SYLLABLE, _lzss, parse_st3  # noqa: E402
from st3_writer import lzss_compress  # noqa: E402

SCORE = """<?xml version="1.0" encoding="UTF-8"?>
<score-partwise version="3.1"><work><work-title>Canção</work-title></work>
<identification><creator type="composer">Autor</creator></identification>
<part-list><score-part id="P1"><part-name>Voz</part-name></score-part></part-list>
<part id="P1"><measure number="1"><attributes><divisions>1</divisions><time><beats>4</beats><beat-type>4</beat-type></time></attributes>
{notes}
</measure></part></score-partwise>"""


def note(step, syllabic, text):
    return (
        f"<note><pitch><step>{step}</step><octave>4</octave></pitch><duration>1</duration>"
        f'<type>quarter</type><lyric number="1"><syllabic>{syllabic}</syllabic><text>{text}</text></lyric></note>'
    )


@pytest.fixture
def score(tmp_path):
    path = tmp_path / "song.musicxml"
    notes = note("C", "begin", "Can") + note("D", "end", "ção") + note("E", "single", "de") + note("F", "single", "amor")
    path.write_text(SCORE.format(notes=notes), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    "data",
    [b"", b"a", b"abc", b"abcabcabcabcabcabcabcabcabc", bytes(5000), bytes(range(256)) * 40, b"\x00\x90\x3c\x40" * 3000],
)
def test_lzss_round_trip(data):
    assert _lzss(lzss_compress(data)) == data


def test_lzss_round_trip_beyond_the_window():
    # Pseudo-random bytes with repeats further apart than the 4096-byte window.
    block = bytes((i * 7919 >> 3) & 0xFF for i in range(6000))
    data = block + block + block[:100]
    assert _lzss(lzss_compress(data)) == data


def test_lzss_actually_compresses():
    assert len(lzss_compress(b"\x00\x90\x3c\x40" * 3000)) < 12000 / 3


def test_written_st3_reads_back_with_music_lyrics_and_header(score):
    data, title = convert_file(score)
    song = parse_st3(data)

    assert (title, song.version, song.title, song.singer) == ("Canção", "3.00", "Canção", "Autor")
    assert song.lyrics == "Canção de amor"
    syllables = [e for e in song.sync if e.kind == SYNC_SYLLABLE]
    tpb = song.midi.ticks_per_beat
    assert [(e.tick, song.text_of(e), e.duration) for e in syllables] == [
        (0, "Can", tpb),
        (tpb, "ção", tpb),
        (2 * tpb, " de", tpb),
        (3 * tpb, " amor", tpb),
    ]
    assert sum(m.type == "note_on" and m.velocity > 0 for t in song.midi.tracks for m in t) == 4
    assert not any(m.type in ("lyrics", "text") for t in song.midi.tracks for m in t)


def test_written_st3_header_matches_invariants_of_real_files(score):
    data, _ = convert_file(score)
    assert data[:16] == b"STAR DATA V3.00\x1a"
    assert struct.unpack_from("<I", data, 0x10)[0] == 270
    records, text_length, record_size, _ = struct.unpack_from("<HHHH", data, 0x104)
    song = parse_st3(data)
    assert records == len(song.sync)
    assert text_length == len(song.lyrics)
    assert record_size == 11


def test_written_st3_block_sizes_reach_the_end_of_file(score):
    data, _ = convert_file(score)
    offset = 0x14 + 270
    offset += 4 + struct.unpack_from("<I", data, offset)[0]
    offset += 4 + struct.unpack_from("<I", data, offset)[0]  # size counts the length field after it
    offset += 4 + struct.unpack_from("<I", data, offset)[0]
    assert offset + 4 + struct.unpack_from("<I", data, offset)[0] == len(data)


def test_written_st3_shifts_the_lower_line_up_as_it_starts(tmp_path):
    long_word = "palavra"
    notes = note("C", "single", long_word) * 4
    measures = "".join(
        f'<measure number="{n}">' + (
            '<attributes><divisions>1</divisions><time><beats>4</beats><beat-type>4</beat-type></time></attributes>' if n == 1 else ""
        ) + notes + "</measure>"
        for n in range(1, 5)
    )
    xml = SCORE.replace('<measure number="1"><attributes><divisions>1</divisions><time><beats>4</beats><beat-type>4</beat-type></time></attributes>\n{notes}\n</measure>', measures)
    path = tmp_path / "long.musicxml"
    path.write_text(xml, encoding="utf-8")

    song = parse_st3(convert_file(path)[0])

    uppers = [e for e in song.sync if e.kind == SYNC_LINE_UPPER]
    lowers = [e for e in song.sync if e.kind == SYNC_LINE_LOWER]
    assert len(uppers) >= 2
    # Each new upper line is what was on the lower line before.
    assert (uppers[1].position, uppers[1].length) == (lowers[0].position, lowers[0].length)
    assert uppers[1].tick == lowers[1].tick


def test_player_loads_and_colours_a_written_st3(score, tmp_path):
    target = tmp_path / "out.st3"
    assert main([str(score), str(target)]) == 0
    song = load_song(target)
    tpb = song.ticks_per_beat
    upper, _ = song.timeline.lines_at(tpb + tpb // 2)
    assert upper.text == "Canção de amor"
    assert upper.sung_chars == pytest.approx(4.5)


def test_cli_creates_st3_by_default_and_honours_kar_option(score, tmp_path, capsys):
    assert main([str(score)]) == 0
    assert (tmp_path / "song.st3").read_bytes().startswith(b"STAR DATA V3.00")

    assert main([str(score), "--formato", "kar"]) == 0
    assert (tmp_path / "song.kar").read_bytes().startswith(b"MThd")

    other = tmp_path / "x.kar"
    assert main([str(score), str(other)]) == 0
    assert mido.MidiFile(str(other)).type == 1
