"""Reader for RealOrche/Star3 ``.st3`` karaoke files.

The format has no public documentation; this was worked out from sample files
and a static reading of the loader. There are two generations:

V3.00
    Header, first lyric line, then three blocks: lyric sync records
    (XOR + LZSS), lyric text, and a complete standard MIDI file (XOR + LZSS).

V3.50
    Header, then named chunks. ``CEVENT`` holds a conductor track and event
    tracks in a private encoding; ``LYRICS`` holds the lyric text.

Lyric text has no separators. Sync records point into it by position and length:
type 16 colours a syllable, 17 and 18 load the upper and lower display lines.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path

import mido

from tolerant_midi import read_midi

MAGIC_V300 = b"STAR DATA V3.00"
MAGIC_V350 = b"STAR DATA V3.50"
TEXT_ENCODING = "cp1252"

# Fixed by the format: the original loader XORs compressed blocks with these
# repeating sequences before decompressing them.
V300_BLOCK_XOR = bytes.fromhex("ee7a162b7c44705d1c20fefbea7af1ad84a4c22f")
V350_BLOCK_XOR = bytes.fromhex("cb7816224f8c7be891329c68b4a75e1450e698bd440a9808")

SYNC_SYLLABLE = 16
SYNC_LINE_UPPER = 17
SYNC_LINE_LOWER = 18
SYNC_TYPES = {SYNC_SYLLABLE, SYNC_LINE_UPPER, SYNC_LINE_LOWER}
SYNC_RECORD = struct.Struct("<IHHHB")

SYSEX_EVENT = 0x0D
_META_TEXT_TYPES = {
    0x41: "text",
    0x42: "copyright",
    0x43: "track_name",
    0x44: "instrument_name",
    0x45: "lyrics",
    0x46: "marker",
    0x47: "cue_marker",
}


class St3Error(ValueError):
    """The file is not a readable .st3."""


@dataclass
class SyncEvent:
    tick: int
    position: int
    length: int
    duration: int
    kind: int


@dataclass
class St3Song:
    version: str
    title: str
    singer: str
    midi: mido.MidiFile
    lyrics: str
    sync: list[SyncEvent] = field(default_factory=list)

    def text_of(self, event: SyncEvent) -> str:
        return self.lyrics[event.position : event.position + event.length]


def read_st3(path: str | Path) -> St3Song:
    return parse_st3(Path(path).read_bytes())


def parse_st3(data: bytes) -> St3Song:
    try:
        if data.startswith(MAGIC_V350):
            return _parse_v350(data)
        if data.startswith(MAGIC_V300):
            return _parse_v300(data)
    except (struct.error, IndexError, EOFError, OSError) as exc:
        raise St3Error(f"Arquivo .st3 truncado ou corrompido: {exc}") from exc
    raise St3Error("Não é um arquivo .st3 reconhecido.")


def _header_text(data: bytes, start: int, end: int) -> str:
    return data[start:end].split(b"\0", 1)[0].decode(TEXT_ENCODING, "replace").strip()


def _unxor(data: bytes, key: bytes) -> bytes:
    return bytes(byte ^ key[i % len(key)] for i, byte in enumerate(data))


def _lzss(data: bytes, size_hint: int = 0) -> bytes:
    """LZSS with a 4096-byte zeroed window, start 0xFEE, and a low-bit-first flag."""
    window = bytearray(4096)
    write = 0xFEE
    output = bytearray()
    flags = 0
    i = 0
    end = len(data)
    while True:
        flags >>= 1
        if not flags & 0x100:
            if i >= end:
                break
            flags = data[i] | 0xFF00
            i += 1
        if flags & 1:
            if i >= end:
                break
            byte = data[i]
            i += 1
            output.append(byte)
            window[write] = byte
            write = (write + 1) & 0xFFF
        else:
            if i + 1 >= end:
                break
            low, high = data[i], data[i + 1]
            i += 2
            offset = low | ((high & 0xF0) << 4)
            for k in range((high & 0x0F) + 3):
                byte = window[(offset + k) & 0xFFF]
                output.append(byte)
                window[write] = byte
                write = (write + 1) & 0xFFF
    if size_hint and len(output) > size_hint:
        del output[size_hint:]
    return bytes(output)


def _parse_v300(data: bytes) -> St3Song:
    header_length = struct.unpack_from("<I", data, 0x10)[0]
    first_line = 0x14 + header_length
    offset = first_line + 4 + struct.unpack_from("<I", data, first_line)[0]

    sync_size, sync_length = struct.unpack_from("<II", data, offset)
    offset += 8
    sync_data = _lzss(_unxor(data[offset : offset + sync_size - 4], V300_BLOCK_XOR), sync_length)
    offset += sync_size - 4

    text_length = struct.unpack_from("<I", data, offset)[0]
    offset += 4
    lyrics = data[offset : offset + text_length].decode(TEXT_ENCODING, "replace")
    offset += text_length

    midi_size = struct.unpack_from("<I", data, offset)[0]
    offset += 4
    midi_length = struct.unpack_from("<I", data, offset + 1)[0]
    midi_data = _lzss(_unxor(data[offset + 5 : offset + midi_size], V300_BLOCK_XOR), midi_length)
    try:
        midi = read_midi(midi_data)
    except ValueError as exc:
        raise St3Error(f"MIDI interno inválido: {exc}") from exc

    return St3Song(
        version="3.00",
        title=_header_text(data, 0x14, 0x44),
        singer=_header_text(data, 0x84, 0xC4),
        midi=midi,
        lyrics=lyrics,
        sync=_read_sync_records(sync_data),
    )


def _read_sync_records(data: bytes) -> list[SyncEvent]:
    events = []
    for tick, position, length, duration, kind in SYNC_RECORD.iter_unpack(
        data[: len(data) - len(data) % SYNC_RECORD.size]
    ):
        if kind in SYNC_TYPES:
            events.append(SyncEvent(tick, position, length, duration, kind))
    return events


def _parse_v350(data: bytes) -> St3Song:
    name = data.find(b"CEVENT\0")
    if name < 4:
        raise St3Error("Bloco CEVENT não encontrado.")
    chunk_end = name + struct.unpack_from("<I", data, name - 4)[0]
    ticks_per_beat, table_size = struct.unpack_from("<HH", data, name + 12)
    body_length, stored_length = struct.unpack_from("<II", data, name + 20)
    if stored_length and stored_length != body_length:
        body = _lzss(
            _unxor(data[chunk_end - stored_length : chunk_end], V350_BLOCK_XOR), body_length
        )
    else:
        body = data[chunk_end - body_length : chunk_end]
    table = struct.unpack_from(f"<{table_size}I", body, 4)

    conductor_start = 4 + 4 * table_size
    conductor_end = conductor_start + table[-1]

    midi = mido.MidiFile(type=1, ticks_per_beat=ticks_per_beat or 120, charset="latin1")
    sync: list[SyncEvent] = []
    midi.tracks.append(_to_midi_track(_read_events(body, conductor_start, conductor_end, sync)))

    offset = conductor_end
    while offset + 4 <= len(body):
        length = struct.unpack_from("<I", body, offset)[0]
        offset += 4
        if offset + length > len(body):
            raise St3Error(f"Trilha ultrapassa o bloco CEVENT em {offset:#x}.")
        events = _read_events(body, offset, offset + length, sync)
        midi.tracks.append(_to_midi_track(events))
        offset += length
    sync.sort(key=lambda event: event.tick)

    return St3Song(
        version="3.50",
        title=_header_text(data, 0x14, 0x44),
        singer=_header_text(data, 0x84, 0xC4),
        midi=midi,
        lyrics=_read_lyrics_chunk(data, chunk_end),
        sync=sync,
    )


def _read_lyrics_chunk(data: bytes, offset: int) -> str:
    """Join the chunk's text blocks: zero padding of varying size, length, text."""
    name = data.find(b"LYRICS\0", offset)
    if name < 4:
        return ""
    chunk_end = name + struct.unpack_from("<I", data, name - 4)[0]
    cursor = name + 6
    text = bytearray()
    while cursor + 4 <= min(chunk_end, len(data)):
        length = struct.unpack_from("<I", data, cursor)[0]
        if length and cursor + 4 + length <= chunk_end:
            text += data[cursor + 4 : cursor + 4 + length]
            cursor += 4 + length
        else:
            cursor += 1
    return text.decode(TEXT_ENCODING, "replace")


def _read_vlq(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    while True:
        byte = data[offset]
        offset += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            return value, offset


def _read_events(
    data: bytes, offset: int, end: int, sync: list[SyncEvent]
) -> list[tuple[int, object]]:
    """Return (absolute tick, mido message) pairs, adding note-offs from the gate time."""
    events: list[tuple[int, object]] = []
    tick = 0
    while offset < end:
        delta, offset = _read_vlq(data, offset)
        # Some files carry a stray high bit in a delta; real ticks fit in 28 bits.
        tick = (tick + delta) & 0x0FFFFFFF
        gate, offset = _read_vlq(data, offset)
        kind = data[offset]
        offset += 1

        if kind in SYNC_TYPES:
            position, length = struct.unpack_from("<HH", data, offset)
            offset += 4
            sync.append(SyncEvent(tick, position, length, gate, kind))
        elif kind == 0x00:
            offset = _read_channel_event(data, offset, tick, gate, events)
        elif kind == 0x01:
            numerator, power, clocks, thirty_seconds = data[offset : offset + 4]
            offset += 4
            events.append(
                (
                    tick,
                    mido.MetaMessage(
                        "time_signature",
                        numerator=numerator or 4,
                        denominator=2**power,
                        clocks_per_click=clocks or 24,
                        notated_32nd_notes_per_beat=thirty_seconds or 8,
                    ),
                )
            )
        elif kind == 0x02:
            tempo = int.from_bytes(data[offset : offset + 3], "little")
            offset += 3
            if tempo:
                events.append((tick, mido.MetaMessage("set_tempo", tempo=tempo)))
        elif kind == 0x03:
            offset += 2
        else:
            length = struct.unpack_from("<H", data, offset)[0]
            offset += 2
            payload = data[offset : offset + length]
            offset += length
            message_type = _META_TEXT_TYPES.get(kind)
            if kind == SYSEX_EVENT:
                sysex = _sysex_message(payload)
                if sysex:
                    events.append((tick, sysex))
            elif message_type:
                text = payload.decode("latin1")
                field_name = "name" if message_type in ("track_name", "instrument_name") else "text"
                events.append((tick, mido.MetaMessage(message_type, **{field_name: text})))
    if offset != end:
        raise St3Error(f"Evento ultrapassa o fim da trilha em {offset:#x}.")
    return events


def _sysex_message(payload: bytes):
    if not payload.startswith(b"\xf0"):
        return None
    body = payload[1:-1] if payload.endswith(b"\xf7") else payload[1:]
    if any(byte > 0x7F for byte in body):
        return None
    return mido.Message("sysex", data=body)


def _read_channel_event(
    data: bytes, offset: int, tick: int, gate: int, events: list[tuple[int, object]]
) -> int:
    status = data[offset]
    offset += 1
    if status < 0x80 or status >= 0xF0:
        raise St3Error(f"Evento de canal inválido {status:#x} em {offset:#x}.")
    channel = status & 0x0F
    kind = status & 0xF0
    first = data[offset] & 0x7F
    if kind in (0xC0, 0xD0):
        offset += 1
        if kind == 0xC0:
            message = mido.Message("program_change", channel=channel, program=first)
        else:
            message = mido.Message("aftertouch", channel=channel, value=first)
        events.append((tick, message))
        return offset

    second = data[offset + 1] & 0x7F
    offset += 2
    if kind == 0x90:
        events.append((tick, mido.Message("note_on", channel=channel, note=first, velocity=second)))
        if second:
            events.append(
                (
                    tick + max(gate, 1),
                    mido.Message("note_off", channel=channel, note=first, velocity=0),
                )
            )
    elif kind == 0x80:
        events.append((tick, mido.Message("note_off", channel=channel, note=first, velocity=second)))
    elif kind == 0xA0:
        events.append(
            (tick, mido.Message("polytouch", channel=channel, note=first, value=second))
        )
    elif kind == 0xB0:
        events.append(
            (tick, mido.Message("control_change", channel=channel, control=first, value=second))
        )
    else:
        pitch = ((second << 7) | first) - 8192
        events.append((tick, mido.Message("pitchwheel", channel=channel, pitch=pitch)))
    return offset


def _to_midi_track(events: list[tuple[int, object]]) -> mido.MidiTrack:
    # Stable sort, with note-offs first at equal ticks so a repeated note restarts.
    ordered = sorted(events, key=lambda item: (item[0], item[1].type != "note_off"))
    track = mido.MidiTrack()
    now = 0
    for tick, message in ordered:
        track.append(message.copy(time=tick - now))
        now = tick
    track.append(mido.MetaMessage("end_of_track", time=0))
    return track
