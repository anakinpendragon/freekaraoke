"""Unified song model for the player: music plus a timed two-line lyric display."""

from __future__ import annotations

from i18n import tr

import base64
import bisect
import dataclasses
import hashlib
import io
import json
import os
import re
import statistics
from dataclasses import dataclass, field
from pathlib import Path

import mido

from st3 import (
    SYNC_LINE_LOWER,
    SYNC_LINE_UPPER,
    SYNC_SYLLABLE,
    TEXT_ENCODING,
    St3Error,
    SyncEvent,
    parse_st3,
)
from tolerant_midi import read_midi

MUSICXML_SUFFIXES = {".musicxml", ".mxl", ".xml"}
SUPPORTED_SUFFIXES = {".st3", ".kar", ".mid", ".midi"} | MUSICXML_SUFFIXES
MAX_KAR_LINE_CHARS = 42
# A track this many times longer than the typical one is corrupt data, not music.
RUNAWAY_TRACK_FACTOR = 5
DEFAULT_TEMPO = 500_000
# Bump when what a score turns into changes, so old prepared copies are not reused.
SCORE_CACHE_VERSION = "1"


_CREDIT = re.compile(
    r"^\W*(karaok|kar\b|\.kar|midi\b|seq|lyrics entered|letra|sincroniz|synchroni|solo track|arranj|arranged|brought to you)",
    re.IGNORECASE,
)
_CONTACT = re.compile(r"https?:|www\.|@", re.IGNORECASE)
_BY_PREFIX = re.compile(
    r"^(?:(?:words|music|lyrics|recorded|performed|sung)(?:\s*(?:&|and)\s*(?:words|music|lyrics))?\s+)?by\s+",
    re.IGNORECASE,
)


def clean_singer(text: str) -> str:
    """Return the singer's name, or nothing when the text credits who made the file."""
    text = "".join(char for char in text if char.isprintable()).strip()
    if _CREDIT.match(text) or _CONTACT.search(text):
        return ""
    return _BY_PREFIX.sub("", text).strip(" -")


def singer_from_titles(titles: list[str]) -> str:
    """Soft Karaoke's first @T is the title; the singer is the next one that is not a credit."""
    return next((name for name in map(clean_singer, titles[1:]) if name), "")


class SongError(ValueError):
    """The file cannot be played; the message is user-facing."""


@dataclass(frozen=True)
class LineState:
    text: str = ""
    sung_chars: float = 0.0


class LyricTimeline:
    """Answers "what is on screen at this tick" from sync events."""

    def __init__(self, lyrics: str, sync: list[SyncEvent]):
        self.lyrics = lyrics
        self._upper = _sorted_events(sync, SYNC_LINE_UPPER)
        self._lower = _sorted_events(sync, SYNC_LINE_LOWER)
        self._syllables = _sorted_events(sync, SYNC_SYLLABLE)
        self._upper_ticks = [event.tick for event in self._upper]
        self._lower_ticks = [event.tick for event in self._lower]
        self._syllable_ticks = [event.tick for event in self._syllables]

    @property
    def has_lyrics(self) -> bool:
        return bool(self._syllables)

    @property
    def syllables(self) -> list[SyncEvent]:
        return self._syllables

    def lines_at(self, tick: int) -> tuple[LineState, LineState]:
        head = self._sung_position(tick)
        return (
            self._line(self._upper, self._upper_ticks, tick, head),
            self._line(self._lower, self._lower_ticks, tick, head),
        )

    def _sung_position(self, tick: int) -> float:
        index = bisect.bisect_right(self._syllable_ticks, tick) - 1
        if index < 0:
            return -1.0
        syllable = self._syllables[index]
        progress = 1.0
        if syllable.duration > 0:
            progress = min(1.0, (tick - syllable.tick) / syllable.duration)
        return syllable.position + progress * syllable.length

    def _line(self, events, ticks, tick: int, head: float) -> LineState:
        index = bisect.bisect_right(ticks, tick) - 1
        if index < 0:
            return LineState()
        event = events[index]
        text = self.lyrics[event.position : event.position + event.length]
        return LineState(text, max(0.0, min(float(len(text)), head - event.position)))


def _sorted_events(sync: list[SyncEvent], kind: int) -> list[SyncEvent]:
    return sorted((event for event in sync if event.kind == kind), key=lambda e: e.tick)


@dataclass
class Song:
    path: Path
    title: str
    singer: str
    midi_bytes: bytes
    ticks_per_beat: int
    total_ticks: int
    timeline: LyricTimeline
    _tempo_ticks: list[int] = field(default_factory=list)
    _tempo_seconds: list[float] = field(default_factory=list)
    _tempo_values: list[int] = field(default_factory=list)

    def seconds_at(self, tick: int) -> float:
        index = bisect.bisect_right(self._tempo_ticks, tick) - 1
        elapsed = (tick - self._tempo_ticks[index]) * self._tempo_values[index]
        return self._tempo_seconds[index] + elapsed / (self.ticks_per_beat * 1_000_000)

    def tick_at_seconds(self, seconds: float) -> int:
        """Inverse of seconds_at, clamped to the song."""
        return bisect.bisect_left(range(self.total_ticks), seconds, key=self.seconds_at)

    @property
    def total_seconds(self) -> float:
        return self.seconds_at(self.total_ticks)


def load_song(path: str | Path) -> Song:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise SongError(tr("Formato não suportado: {0}").format(suffix or tr('sem extensão')))
    if suffix in MUSICXML_SUFFIXES:
        return _load_musicxml(path)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise SongError(tr("Não consegui abrir o arquivo: {0}").format(exc.strerror or exc)) from exc

    if suffix == ".st3":
        try:
            decoded = parse_st3(data)
        except St3Error as exc:
            raise SongError(str(exc)) from exc
        return _build_song(
            path, decoded.midi, decoded.title, clean_singer(decoded.singer), decoded.lyrics, decoded.sync
        )

    try:
        midi = read_midi(data)
    except ValueError as exc:
        raise SongError(tr("Arquivo MIDI inválido: {0}").format(exc)) from exc
    title, singer, lyrics, sync = parse_kar_lyrics(midi)
    return _build_song(path, midi, title, singer, lyrics, sync)


def score_cache_path(path: Path) -> Path | None:
    """Where a prepared copy of this score goes; it changes when the score changes."""
    try:
        status = path.stat()
    except OSError:
        return None
    identity = f"{SCORE_CACHE_VERSION}|{path.absolute()}|{status.st_size}|{status.st_mtime_ns}"
    name = hashlib.sha256(identity.encode("utf-8", "surrogateescape")).hexdigest()
    base = os.environ.get("XDG_CACHE_HOME") or os.environ.get("LOCALAPPDATA") or str(Path.home() / ".cache")
    return Path(base) / "freekaraoke" / "scores" / f"{name}.json"


def is_prepared_score(path: Path) -> bool:
    cached = score_cache_path(path)
    return cached is not None and cached.is_file()


def _read_prepared_score(cached: Path | None) -> tuple | None:
    if cached is None:
        return None
    try:
        data = json.loads(cached.read_text(encoding="utf-8"))
        midi = read_midi(base64.b64decode(data["midi"]))
        sync = [SyncEvent(*record) for record in data["sync"]]
        return midi, data["title"], data["singer"], data["lyrics"], sync
    except (OSError, ValueError, KeyError, TypeError):
        return None  # A missing or damaged copy only means preparing the score again.


def _save_prepared_score(cached: Path | None, midi, title, singer, lyrics, sync) -> None:
    if cached is None:
        return
    buffer = io.BytesIO()
    midi.save(file=buffer)
    data = {
        "midi": base64.b64encode(buffer.getvalue()).decode("ascii"),
        "title": title,
        "singer": singer,
        "lyrics": lyrics,
        "sync": [dataclasses.astuple(event) for event in sync],
    }
    temporary = cached.with_suffix(".tmp")
    try:
        cached.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(data), encoding="utf-8")
        os.replace(temporary, cached)
    except OSError:
        pass  # Only the next opening gets slower.


def _load_musicxml(path: Path) -> Song:
    """Scores play directly, with the same lyric timing that a converted file would get.

    Interpreting a score can take seconds, so the result is kept for the next time.
    """
    cached = score_cache_path(path)
    prepared = _read_prepared_score(cached)
    if prepared is not None:
        return _build_song(path, *prepared)

    # Imported here because these bring in music21, which only scores need.
    from kar import ConversionError, read_musicxml
    from st3_writer import sync_records

    try:
        score = read_musicxml(path)
    except ConversionError as exc:
        raise SongError(str(exc)) from exc
    lyrics = ""
    sync: list[SyncEvent] = []
    if score.syllables:
        encoded, records = sync_records(score)
        lyrics = encoded.decode(TEXT_ENCODING, "replace")
        sync = [SyncEvent(*record) for record in records]
    singer = clean_singer(score.artist)
    _save_prepared_score(cached, score.midi, score.title, singer, lyrics, sync)
    return _build_song(path, score.midi, score.title, singer, lyrics, sync)


def _build_song(path, midi, title, singer, lyrics, sync) -> Song:
    _drop_runaway_tracks(midi)
    total_ticks = _last_note_tick(midi)
    if not total_ticks:
        raise SongError(tr("O arquivo não tem notas para tocar."))
    if len(midi.tracks) > 1:
        midi.type = 1
    buffer = io.BytesIO()
    midi.save(file=buffer)

    song = Song(
        path=path,
        title=title or path.stem,
        singer=singer,
        midi_bytes=buffer.getvalue(),
        ticks_per_beat=midi.ticks_per_beat,
        total_ticks=total_ticks,
        timeline=LyricTimeline(lyrics, sync),
    )
    _fill_tempo_map(song, midi)
    return song


def _track_end(track) -> int:
    return sum(message.time for message in track)


def _drop_runaway_tracks(midi: mido.MidiFile) -> None:
    ends = [_track_end(track) for track in midi.tracks if _has_notes(track)]
    if len(ends) < 2:
        return
    limit = statistics.median(ends) * RUNAWAY_TRACK_FACTOR
    midi.tracks[:] = [track for track in midi.tracks if _track_end(track) <= limit or not _has_notes(track)]


def _has_notes(track) -> bool:
    return any(message.type == "note_on" for message in track)


def _last_note_tick(midi: mido.MidiFile) -> int:
    last = 0
    for track in midi.tracks:
        tick = 0
        for message in track:
            tick += message.time
            if message.type in ("note_on", "note_off"):
                last = max(last, tick)
    return last


def _fill_tempo_map(song: Song, midi: mido.MidiFile) -> None:
    changes = {0: DEFAULT_TEMPO}
    for track in midi.tracks:
        tick = 0
        for message in track:
            tick += message.time
            if message.type == "set_tempo":
                changes[tick] = message.tempo
    seconds = 0.0
    previous_tick = 0
    previous_tempo = changes[0]
    for tick in sorted(changes):
        seconds += (tick - previous_tick) * previous_tempo / (midi.ticks_per_beat * 1_000_000)
        song._tempo_ticks.append(tick)
        song._tempo_seconds.append(seconds)
        song._tempo_values.append(changes[tick])
        previous_tick = tick
        previous_tempo = changes[tick]


def parse_kar_lyrics(midi: mido.MidiFile) -> tuple[str, str, str, list[SyncEvent]]:
    """Convert Soft Karaoke or plain MIDI lyric events into the .st3 sync model."""
    titles: list[str] = []
    candidates: list[list[tuple[int, str]]] = []
    for track in midi.tracks:
        for event_type in ("text", "lyrics"):
            events = []
            tick = 0
            for message in track:
                tick += message.time
                if message.type != event_type:
                    continue
                text = _decode_text(message.text)
                if text.startswith("@"):
                    if text.startswith("@T"):
                        titles.append(text[2:].strip())
                elif text:
                    events.append((tick, text))
            candidates.append(events)
    events = max(candidates, key=len, default=[])

    title = titles[0] if titles else ""
    singer = singer_from_titles(titles)
    if len(events) < 2:
        return title, singer, "", []
    lyrics, sync = _kar_sync(events, midi.ticks_per_beat)
    return title, singer, lyrics, sync


def _decode_text(text: str) -> str:
    raw = text.encode("latin1", "replace")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", "replace")


def _kar_sync(events: list[tuple[int, str]], ticks_per_beat: int) -> tuple[str, list[SyncEvent]]:
    lyrics = ""
    lines: list[tuple[int, int, int]] = []  # (first tick, start, length)
    syllables: list[SyncEvent] = []
    line_start = 0
    line_tick = None
    break_pending = False

    def close_line() -> None:
        nonlocal line_start, line_tick
        if line_tick is not None and len(lyrics) > line_start:
            lines.append((line_tick, line_start, len(lyrics) - line_start))
        line_start = len(lyrics)
        line_tick = None

    for tick, raw in events:
        text = raw
        if text[0] in "/\\":
            break_pending = True
            text = text[1:]
        ends_line = text.endswith(("\r", "\n"))
        text = text.replace("\r", "").replace("\n", "")
        if text:
            wraps = (
                len(lyrics) - line_start + len(text) > MAX_KAR_LINE_CHARS
                and (text[0] == " " or lyrics.endswith(" "))
            )
            if break_pending or wraps:
                close_line()
                break_pending = False
                if not line_tick:
                    text = text.lstrip(" ") or text
            if line_tick is None:
                line_tick = tick
            syllables.append(SyncEvent(tick, len(lyrics), len(text), 0, SYNC_SYLLABLE))
            lyrics += text
        if ends_line:
            break_pending = True
    close_line()

    for index, syllable in enumerate(syllables):
        following = syllables[index + 1].tick if index + 1 < len(syllables) else None
        limit = 2 * ticks_per_beat
        syllable.duration = min(following - syllable.tick, limit) if following else limit

    sync = list(syllables)
    for index, (_, start, length) in enumerate(lines):
        # A line replaces the one in its slot when the line before it starts: by
        # then its slot's previous occupant has been fully sung.
        shown_at = 0 if index < 2 else lines[index - 1][0]
        kind = SYNC_LINE_UPPER if index % 2 == 0 else SYNC_LINE_LOWER
        sync.append(SyncEvent(shown_at, start, length, 0, kind))
    return lyrics, sync
