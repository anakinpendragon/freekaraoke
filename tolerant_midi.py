"""Standard MIDI file reader that keeps what it can from malformed files.

Old karaoke files often have truncated tracks, missing running status or invalid
meta events, which make strict readers reject the whole song.
"""

from __future__ import annotations

import struct

import mido

_TEXT_METAS = {
    0x01: ("text", "text"),
    0x02: ("copyright", "text"),
    0x03: ("track_name", "name"),
    0x04: ("instrument_name", "name"),
    0x05: ("lyrics", "text"),
    0x06: ("marker", "text"),
    0x07: ("cue_marker", "text"),
}
_MAJOR_KEYS = ["Cb", "Gb", "Db", "Ab", "Eb", "Bb", "F", "C", "G", "D", "A", "E", "B", "F#", "C#"]
_MINOR_KEYS = ["Abm", "Ebm", "Bbm", "Fm", "Cm", "Gm", "Dm", "Am", "Em", "Bm", "F#m", "C#m", "G#m", "D#m", "A#m"]


def read_midi(data: bytes) -> mido.MidiFile:
    """Parse MIDI bytes; raises ValueError only if there is no usable header."""
    start = data.find(b"MThd")
    if start < 0 or start + 14 > len(data):
        raise ValueError("Cabeçalho MIDI não encontrado.")
    data = data[:start] + _undo_text_mode_transfer(data[start:])
    midi_type, track_count, division = struct.unpack_from(">HHH", data, start + 8)
    if division == 0 or division & 0x8000:
        raise ValueError("Divisão de tempo MIDI não suportada.")

    midi = mido.MidiFile(type=min(midi_type, 2), ticks_per_beat=division, charset="latin1")
    # Tracks are located by their tag: some files declare wrong track lengths. The
    # header's count still rules, since extra tracks would double the melody.
    offset = data.find(b"MTrk", start + 8)
    while offset >= 0 and (not track_count or len(midi.tracks) < track_count):
        following = data.find(b"MTrk", offset + 8)
        end = following if following >= 0 else len(data)
        midi.tracks.append(_read_track(data, offset + 8, end))
        offset = following
    if not midi.tracks:
        raise ValueError("O arquivo MIDI não tem trilhas.")
    return midi


def _undo_text_mode_transfer(data: bytes) -> bytes:
    """Reverse LF to CRLF damage, but only if that makes the file's structure exact."""
    if _chunks_end_exactly(data) or b"\r\n" not in data:
        return data
    repaired = data.replace(b"\r\n", b"\n")
    return repaired if _chunks_end_exactly(repaired) else data


def _chunks_end_exactly(data: bytes) -> bool:
    offset = 0
    while offset + 8 <= len(data):
        if data[offset : offset + 4] not in (b"MThd", b"MTrk"):
            return False
        offset += 8 + struct.unpack_from(">I", data, offset + 4)[0]
    return offset == len(data)


def _read_track(data: bytes, offset: int, end: int) -> mido.MidiTrack:
    track = mido.MidiTrack()
    status = 0
    pending = 0
    try:
        while offset < end:
            delta, offset = _read_vlq(data, offset, end)
            pending += delta
            first = data[offset]
            if first == 0xFF:
                kind = data[offset + 1]
                length, offset = _read_vlq(data, offset + 2, end)
                payload = data[offset : offset + length]
                offset += length
                # Running status survives meta and sysex events: the spec says it
                # should not, but real files rely on it and would lose notes.
                if kind == 0x2F:
                    break
                message = _meta_message(kind, payload)
            elif first in (0xF0, 0xF7):
                length, offset = _read_vlq(data, offset + 1, end)
                payload = data[offset : offset + length]
                offset += length
                message = _sysex_message(first, payload)
            elif first >= 0xF0:
                # System common/realtime have no place in a file; skip the byte.
                offset += 1
                message = None
            else:
                if first & 0x80:
                    status = first
                    offset += 1
                if not status:
                    # A data byte with no status to apply it to.
                    offset += 1
                    continue
                message, offset = _channel_message(status, data, offset)
            if message is not None:
                track.append(message.copy(time=pending))
                pending = 0
    except IndexError:
        pass  # Truncated track: keep the events read so far.
    track.append(mido.MetaMessage("end_of_track", time=0))
    return track


def _read_vlq(data: bytes, offset: int, end: int) -> tuple[int, int]:
    value = 0
    for _ in range(4):
        if offset >= end:
            raise IndexError
        byte = data[offset]
        offset += 1
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            break
    return value, offset


def _channel_message(status: int, data: bytes, offset: int):
    kind = status & 0xF0
    channel = status & 0x0F
    first = data[offset] & 0x7F
    if kind == 0xC0:
        return mido.Message("program_change", channel=channel, program=first), offset + 1
    if kind == 0xD0:
        return mido.Message("aftertouch", channel=channel, value=first), offset + 1
    second = data[offset + 1] & 0x7F
    offset += 2
    if kind == 0x80:
        return mido.Message("note_off", channel=channel, note=first, velocity=second), offset
    if kind == 0x90:
        return mido.Message("note_on", channel=channel, note=first, velocity=second), offset
    if kind == 0xA0:
        return mido.Message("polytouch", channel=channel, note=first, value=second), offset
    if kind == 0xB0:
        return mido.Message("control_change", channel=channel, control=first, value=second), offset
    pitch = ((second << 7) | first) - 8192
    return mido.Message("pitchwheel", channel=channel, pitch=pitch), offset


def _meta_message(kind: int, payload: bytes):
    if kind in _TEXT_METAS:
        message_type, attribute = _TEXT_METAS[kind]
        return mido.MetaMessage(message_type, **{attribute: payload.decode("latin1")})
    if kind == 0x51 and len(payload) == 3:
        return mido.MetaMessage("set_tempo", tempo=int.from_bytes(payload, "big"))
    if kind == 0x58 and len(payload) == 4 and payload[0]:
        return mido.MetaMessage(
            "time_signature",
            numerator=payload[0],
            denominator=2 ** payload[1],
            clocks_per_click=payload[2],
            notated_32nd_notes_per_beat=payload[3],
        )
    if kind == 0x59 and len(payload) == 2:
        sharps = int.from_bytes(payload[:1], "big", signed=True)
        if -7 <= sharps <= 7 and payload[1] in (0, 1):
            keys = _MINOR_KEYS if payload[1] else _MAJOR_KEYS
            return mido.MetaMessage("key_signature", key=keys[sharps + 7])
    return None  # Invalid or irrelevant to playback.


def _sysex_message(first: int, payload: bytes):
    body = payload[:-1] if payload.endswith(b"\xf7") else payload
    if first != 0xF0 or any(byte > 0x7F for byte in body):
        return None
    return mido.Message("sysex", data=body)
