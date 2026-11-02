import io
import struct
import sys
from pathlib import Path

import mido
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from st3 import (  # noqa: E402
    SYNC_LINE_UPPER,
    SYNC_SYLLABLE,
    V300_BLOCK_XOR,
    V350_BLOCK_XOR,
    St3Error,
    _lzss,
    _unxor,
    parse_st3,
)
from tolerant_midi import read_midi  # noqa: E402


def literal_lzss(data: bytes) -> bytes:
    """Encode with literals only, which any conforming decoder must accept."""
    output = bytearray()
    for start in range(0, len(data), 8):
        chunk = data[start : start + 8]
        output.append(0xFF)
        output += chunk
    return bytes(output)


def header(magic: bytes, title: str = "Canção", singer: str = "Cantor") -> bytes:
    block = bytearray(0x10E)
    encoded_title = title.encode("cp1252")
    encoded_singer = singer.encode("cp1252")
    block[: len(encoded_title)] = encoded_title
    block[0x70 : 0x70 + len(encoded_singer)] = encoded_singer
    return magic + b"\x1a" + struct.pack("<I", len(block)) + bytes(block)


def standard_midi() -> bytes:
    midi = mido.MidiFile(type=1, ticks_per_beat=120)
    track = mido.MidiTrack()
    midi.tracks.append(track)
    track.append(mido.Message("note_on", note=60, velocity=90, time=0))
    track.append(mido.Message("note_off", note=60, velocity=0, time=120))
    buffer = io.BytesIO()
    midi.save(file=buffer)
    return buffer.getvalue()


def build_v300(lyrics: bytes, records: list[tuple]) -> bytes:
    first_line = b"Primeira"
    sync = b"".join(struct.pack("<IHHHB", *record) for record in records)
    stored_sync = _unxor(literal_lzss(sync), V300_BLOCK_XOR)
    midi = standard_midi()
    stored_midi = _unxor(literal_lzss(midi), V300_BLOCK_XOR)
    return (
        header(b"STAR DATA V3.00")
        + struct.pack("<I", len(first_line))
        + first_line
        + struct.pack("<II", len(stored_sync) + 4, len(sync))
        + stored_sync
        + struct.pack("<I", len(lyrics))
        + lyrics
        + struct.pack("<I", len(stored_midi) + 5)
        + b"\0"
        + struct.pack("<I", len(midi))
        + stored_midi
    )


def v350_body() -> bytes:
    conductor = (
        b"\x00\x00\x02" + (500000).to_bytes(3, "little")  # tempo
        + b"\x00\x00\x01\x04\x02\x18\x08"  # time signature
        + b"\x00\x00\x03\x00\x00"  # key signature, ignored
    )
    music = (
        b"\x00\x00\x43\x03\x00Voz"  # track name
        + b"\x00\x00\x00\xc0\x05"  # program change
        + b"\x10\x81\x70\x00\x90\x3c\x40"  # delta 16, gate 240, note 60
    )
    lyrics = (
        b"\x00\x00\x11\x00\x00\x09\x00"  # upper line
        + b"\x10\x81\x70\x10\x00\x00\x04\x00"  # syllable at tick 16
    )
    tracks = b"".join(struct.pack("<I", len(track)) + track for track in (music, lyrics))
    table = struct.pack("<3I", len(music), len(lyrics), len(conductor))
    return struct.pack("<I", 3) + table + conductor + tracks


def build_v350(body: bytes, stored: bytes, stored_length: int, text_field_gap: int = 28) -> bytes:
    name = b"CEVENT\0\0\0\0" + b"\x20\x00"
    fields = struct.pack("<HH", 480, 3) + b"\0\0\0\0" + struct.pack("<II", len(body), stored_length)
    event_chunk = name + fields + stored
    lyrics = "Can ção".encode("cp1252") + b"!!"
    lyric_chunk = (
        b"LYRICS\0" + bytes(text_field_gap) + struct.pack("<I", len(lyrics)) + lyrics
    )
    return (
        header(b"STAR DATA V3.50")
        + struct.pack("<I", len(event_chunk))
        + event_chunk
        + struct.pack("<I", len(lyric_chunk))
        + lyric_chunk
    )


def note_events(midi):
    events = []
    for track in midi.tracks:
        tick = 0
        for message in track:
            tick += message.time
            if message.type in ("note_on", "note_off"):
                events.append((tick, message.type, message.note))
    return sorted(events)


def test_lzss_literals_and_back_reference():
    # Flags 0b01: literal "A", then a match copying from the window start offset.
    # Offset 0xFEE is where the literal was written; length nibble 0 copies 3 bytes.
    data = bytes([0b00000001, ord("A"), 0xEE, 0xF0])
    assert _lzss(data) == b"AAAA"


def test_v300_reads_music_lyrics_and_sync():
    song = parse_st3(
        build_v300(
            "Canção".encode("cp1252"),
            [(0, 0, 6, 0, SYNC_LINE_UPPER), (25, 0, 3, 36, SYNC_SYLLABLE), (61, 3, 3, 36, SYNC_SYLLABLE)],
        )
    )

    assert (song.version, song.title, song.singer) == ("3.00", "Canção", "Cantor")
    assert note_events(song.midi) == [(0, "note_on", 60), (120, "note_off", 60)]
    syllables = [event for event in song.sync if event.kind == SYNC_SYLLABLE]
    assert [(e.tick, song.text_of(e)) for e in syllables] == [(25, "Can"), (61, "ção")]


@pytest.mark.parametrize("compressed", [False, True])
def test_v350_plain_and_compressed_decode_alike(compressed):
    body = v350_body()
    if compressed:
        stored = _unxor(literal_lzss(body), V350_BLOCK_XOR)
        data = build_v350(body, stored, len(stored))
    else:
        data = build_v350(body, body, len(body))

    song = parse_st3(data)

    assert song.version == "3.50"
    assert song.midi.ticks_per_beat == 480
    assert note_events(song.midi) == [(16, "note_on", 60), (256, "note_off", 60)]
    assert song.lyrics == "Can ção!!"
    syllable = next(e for e in song.sync if e.kind == SYNC_SYLLABLE)
    assert (syllable.tick, syllable.duration, song.text_of(syllable)) == (16, 240, "Can ")
    tempos = [m.tempo for t in song.midi.tracks for m in t if m.type == "set_tempo"]
    assert tempos == [500000]


def test_v350_zero_stored_length_means_plain():
    body = v350_body()
    song = parse_st3(build_v350(body, body, 0))
    assert note_events(song.midi)[0] == (16, "note_on", 60)


@pytest.mark.parametrize("gap", [8, 28, 56])
def test_lyrics_header_size_varies(gap):
    body = v350_body()
    assert parse_st3(build_v350(body, body, len(body), gap)).lyrics == "Can ção!!"


def test_not_st3_is_rejected():
    with pytest.raises(St3Error):
        parse_st3(b"MThd not a karaoke file")


def test_truncated_file_is_rejected():
    body = v350_body()
    data = build_v350(body, body, len(body))
    with pytest.raises(St3Error):
        parse_st3(data[: len(data) // 2])


def smf(track_body: bytes) -> bytes:
    return (
        b"MThd" + struct.pack(">IHHH", 6, 1, 1, 96)
        + b"MTrk" + struct.pack(">I", len(track_body)) + track_body
    )


def test_tolerant_midi_keeps_notes_before_truncation():
    midi = read_midi(smf(b"\x00\x90\x3c\x40\x60\x80\x3c"))
    assert note_events(midi) == [(0, "note_on", 60)]


def test_tolerant_midi_handles_running_status():
    midi = read_midi(smf(b"\x00\x90\x3c\x40\x10\x3e\x40\x00\xff\x2f\x00"))
    assert note_events(midi) == [(0, "note_on", 60), (16, "note_on", 62)]


def test_tolerant_midi_running_status_survives_meta_event():
    midi = read_midi(smf(b"\x00\x90\x3c\x40\x00\xff\x01\x01a\x10\x3e\x40\x00\xff\x2f\x00"))
    assert note_events(midi) == [(0, "note_on", 60), (16, "note_on", 62)]


def test_v350_keeps_sysex_for_the_synth():
    reset = b"\xf0\x7e\x7f\x09\x01\xf7"
    track = b"\x00\x00\x0d" + struct.pack("<H", len(reset)) + reset + b"\x00\x00\x0d\x03\x00\x00\x00\x00"
    conductor = b"\x00\x00\x02" + (500000).to_bytes(3, "little")
    table = struct.pack("<2I", len(track), len(conductor))
    body = struct.pack("<I", 2) + table + conductor + struct.pack("<I", len(track)) + track
    data = build_v350(body, body, len(body)).replace(
        struct.pack("<HH", 480, 3), struct.pack("<HH", 480, 2), 1
    )

    song = parse_st3(data)

    sysex = [m for t in song.midi.tracks for m in t if m.type == "sysex"]
    assert [tuple(m.data) for m in sysex] == [(0x7E, 0x7F, 0x09, 0x01)]


def test_v350_stray_high_delta_bit_is_masked():
    lyrics = b"\x81\x80\x80\x80\x10\x00\x10\x00\x00\x04\x00"  # delta 2**28 + 16
    conductor = b"\x00\x00\x02" + (500000).to_bytes(3, "little")
    tracks = struct.pack("<I", len(lyrics)) + lyrics
    table = struct.pack("<2I", len(lyrics), len(conductor))
    body = struct.pack("<I", 2) + table + conductor + tracks
    data = build_v350(body, body, len(body)).replace(
        struct.pack("<HH", 480, 3), struct.pack("<HH", 480, 2), 1
    )

    song = parse_st3(data)

    assert [event.tick for event in song.sync] == [16]


def test_tolerant_midi_drops_invalid_key_signature():
    midi = read_midi(smf(b"\x00\xff\x59\x02\xff\xa1\x00\x90\x3c\x40\x00\xff\x2f\x00"))
    assert note_events(midi) == [(0, "note_on", 60)]


def test_tolerant_midi_skips_data_without_status():
    midi = read_midi(smf(b"\x00\x3c\x00\x90\x3c\x40\x00\xff\x2f\x00"))
    assert [m.type for m in midi.tracks[0]].count("note_on") == 1


def test_tolerant_midi_ignores_wrong_track_lengths():
    first = b"\x00\x90\x3c\x40\x00\xff\x2f\x00"
    second = b"\x00\x90\x3e\x40\x00\xff\x2f\x00"
    data = (
        b"MThd" + struct.pack(">IHHH", 6, 1, 2, 96)
        + b"MTrk" + struct.pack(">I", 9999) + first
        + b"MTrk" + struct.pack(">I", 1) + second
    )
    assert [note for _, _, note in note_events(read_midi(data))] == [60, 62]


def test_tolerant_midi_respects_declared_track_count():
    first = b"\x00\x90\x3c\x40\x00\xff\x2f\x00"
    hidden = b"\x00\x90\x3e\x40\x00\xff\x2f\x00"
    data = (
        b"MThd" + struct.pack(">IHHH", 6, 1, 1, 96)
        + b"MTrk" + struct.pack(">I", len(first)) + first
        + b"MTrk" + struct.pack(">I", len(hidden)) + hidden
    )
    assert [note for _, _, note in note_events(read_midi(data))] == [60]


def test_tolerant_midi_undoes_text_mode_transfer():
    # Delta 0x0A would become CR LF, shifting everything after it.
    body = b"\x00\x90\x3c\x40\x0a\x80\x3c\x00\x10\x90\x3e\x40\x00\xff\x2f\x00"
    intact = smf(body)
    assert note_events(read_midi(intact.replace(b"\n", b"\r\n"))) == note_events(read_midi(intact))


def test_tolerant_midi_keeps_genuine_crlf_bytes():
    # Delta 0x0D, note 0x0A: a real CR LF pair in a structurally valid file.
    data = smf(b"\x00\x90\x3c\x40\x0d\x0a\x40\x00\xff\x2f\x00")
    assert (13, "note_on", 60) not in note_events(read_midi(data))
    assert len(note_events(read_midi(data))) == 2


def test_tolerant_midi_requires_header():
    with pytest.raises(ValueError):
        read_midi(b"not midi")
