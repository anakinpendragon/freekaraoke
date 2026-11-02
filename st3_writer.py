"""Writes V3.00 ``.st3`` files, which embed a standard MIDI file.

V3.00 is the generation written here because its music block is plain MIDI. The
layout follows what all sample files share: see st3.py for the format overview.
"""

from __future__ import annotations

import io
import struct

import mido

from kar import Song, _karaoke_events, _without_lyrics
from st3 import (
    MAGIC_V300,
    SYNC_LINE_LOWER,
    SYNC_LINE_UPPER,
    SYNC_RECORD,
    SYNC_SYLLABLE,
    TEXT_ENCODING,
    V300_BLOCK_XOR,
    _unxor,
)

HEADER_LENGTH = 270
TITLE_FIELD = (0x14, 0x44)
SINGER_FIELD = (0x84, 0xC4)
COUNTS_OFFSET = 0x104
# Purpose unknown; 1 is the most common value in sample files.
UNKNOWN_HEADER_FIELD = 1

_WINDOW = 4096
_WINDOW_START = 0xFEE
_MIN_MATCH = 3
_MAX_MATCH = 18
_MAX_DISTANCE = _WINDOW - _MAX_MATCH
_MAX_CANDIDATES = 64


def build_st3(song: Song, title: str, artist: str) -> bytes:
    lyrics, records = sync_records(song)
    sync = b"".join(SYNC_RECORD.pack(*record) for record in records)
    midi_data = _midi_bytes(song.midi)

    first = records[0]
    first_line = lyrics[first[1] : first[1] + first[2]]

    stored_sync = _unxor(lzss_compress(sync), V300_BLOCK_XOR)
    stored_midi = _unxor(lzss_compress(midi_data), V300_BLOCK_XOR)
    return b"".join(
        [
            _header(title, artist, len(records), len(lyrics)),
            struct.pack("<I", len(first_line)),
            first_line,
            struct.pack("<II", len(stored_sync) + 4, len(sync)),
            stored_sync,
            struct.pack("<I", len(lyrics)),
            lyrics,
            struct.pack("<I", len(stored_midi) + 5),
            b"\0",
            struct.pack("<I", len(midi_data)),
            stored_midi,
        ]
    )


def _header(title: str, artist: str, record_count: int, text_length: int) -> bytes:
    if record_count > 0xFFFF or text_length > 0xFFFF:
        raise ValueError("A letra é longa demais para o formato .st3.")
    header = bytearray(0x14 + HEADER_LENGTH)
    header[:15] = MAGIC_V300
    header[15] = 0x1A
    struct.pack_into("<I", header, 0x10, HEADER_LENGTH)
    _put_text(header, TITLE_FIELD, title)
    _put_text(header, SINGER_FIELD, artist)
    struct.pack_into(
        "<HHHH", header, COUNTS_OFFSET, record_count, text_length, SYNC_RECORD.size, UNKNOWN_HEADER_FIELD
    )
    return bytes(header)


def _put_text(header: bytearray, field: tuple[int, int], text: str) -> None:
    start, end = field
    encoded = text.encode(TEXT_ENCODING, "replace")[: end - start - 1]
    header[start : start + len(encoded)] = encoded


def sync_records(song: Song) -> tuple[bytes, list[tuple[int, int, int, int, int]]]:
    """Lay out lyrics as sample files do: the lower line moves up as it starts."""
    ticks_per_beat = song.midi.ticks_per_beat
    lyrics = bytearray()
    syllables = []
    lines: list[tuple[int, int, int]] = []  # (first tick, start, length)
    for (tick, marked), source in zip(_karaoke_events(song.syllables, ticks_per_beat), song.syllables):
        starts_line = marked[0] in "/\\"
        text = (marked[1:] if starts_line else marked).encode(TEXT_ENCODING, "replace")
        if starts_line:
            lines.append((tick, len(lyrics), 0))
        duration = min(max(source.end - source.tick, 1), 0xFFFF)
        syllables.append((tick, len(lyrics), len(text), duration, SYNC_SYLLABLE))
        lyrics += text
        first_tick, start, _ = lines[-1]
        lines[-1] = (first_tick, start, len(lyrics) - start)

    end = len(lyrics)
    display = []
    for index, (first_tick, start, length) in enumerate(lines):
        shown_at = 0 if index == 0 else first_tick
        following = lines[index + 1] if index + 1 < len(lines) else (0, end, 0)
        display.append((shown_at, start, length, 0, SYNC_LINE_UPPER))
        display.append((shown_at, following[1], following[2], 0, SYNC_LINE_LOWER))

    # At equal ticks the display changes first, so a syllable colours the new line.
    records = sorted(display + syllables, key=lambda record: (record[0], record[4] == SYNC_SYLLABLE))
    return bytes(lyrics), records


def _midi_bytes(midi: mido.MidiFile) -> bytes:
    clean = mido.MidiFile(type=1, ticks_per_beat=midi.ticks_per_beat, charset="latin1")
    clean.tracks.extend(_without_lyrics(track) for track in midi.tracks)
    buffer = io.BytesIO()
    clean.save(file=buffer)
    return buffer.getvalue()


def lzss_compress(data: bytes) -> bytes:
    """Greedy LZSS matching the loader's window: 4096 zeroed bytes, start 0xFEE."""
    output = bytearray()
    positions: dict[bytes, list[int]] = {}
    flags = 0
    flag_bit = 0
    flag_index = -1
    i = 0
    size = len(data)

    while i < size:
        if flag_bit == 0:
            flag_index = len(output)
            output.append(0)
            flags = 0
        length, source = _longest_match(data, i, positions)
        if length >= _MIN_MATCH:
            offset = (_WINDOW_START + source) & 0xFFF
            output.append(offset & 0xFF)
            output.append(((offset >> 4) & 0xF0) | (length - _MIN_MATCH))
        else:
            length = 1
            flags |= 1 << flag_bit
            output.append(data[i])
        for position in range(i, i + length):
            if position + _MIN_MATCH <= size:
                positions.setdefault(data[position : position + _MIN_MATCH], []).append(position)
        i += length
        output[flag_index] = flags
        flag_bit = (flag_bit + 1) % 8
    return bytes(output)


def _longest_match(data: bytes, i: int, positions: dict[bytes, list[int]]) -> tuple[int, int]:
    best_length = 0
    best_source = 0
    limit = min(_MAX_MATCH, len(data) - i)
    for source in reversed(positions.get(data[i : i + _MIN_MATCH], [])[-_MAX_CANDIDATES:]):
        if i - source > _MAX_DISTANCE:
            break
        length = _MIN_MATCH
        while length < limit and data[source + length] == data[i + length]:
            length += 1
        if length > best_length:
            best_length, best_source = length, source
            if length == limit:
                break
    return best_length, best_source
