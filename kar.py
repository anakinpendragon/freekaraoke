"""Convert MusicXML or MIDI files with lyrics into Soft Karaoke (.kar) files."""

from __future__ import annotations

from i18n import tr

import contextlib
import io
import tempfile
from dataclasses import dataclass
from pathlib import Path

import mido

MUSICXML_SUFFIXES = {".mxl", ".musicxml", ".xml"}
MIDI_SUFFIXES = {".mid", ".midi", ".kar"}
SUPPORTED_SUFFIXES = MUSICXML_SUFFIXES | MIDI_SUFFIXES

MAX_LINE_CHARS = 32
LINES_PER_SCREEN = 4
LINE_BREAK_GAP_BEATS = 1
OUTPUT_TICKS_PER_BEAT = 480

TYPOGRAPHIC_TO_ASCII = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "ʼ": "'",
        "“": '"',
        "”": '"',
        "–": "-",
        "—": "-",
        "…": "...",
        "‿": " ",  # MusicXML elision tie between syllables on one note
    }
)


class ConversionError(ValueError):
    """The input cannot be turned into a karaoke file; the message is user-facing."""


@dataclass
class Syllable:
    tick: int
    end: int
    text: str
    word_start: bool = True
    forced_break: str = ""  # "/" or "\\" when the source already marks a break


@dataclass
class Song:
    midi: mido.MidiFile
    syllables: list[Syllable]
    title: str = ""
    artist: str = ""


def convert(path: Path, title: str = "", artist: str = "") -> tuple[mido.MidiFile, str]:
    """Return the karaoke MIDI file and the title that was used."""
    song = load_source(path)
    final_title = title.strip() or song.title
    return build_kar(song, final_title, artist.strip() or song.artist), final_title


def load_source(path: Path) -> Song:
    """Read a score or MIDI file that has lyrics."""
    suffix = path.suffix.lower()
    if suffix in MUSICXML_SUFFIXES:
        song = read_musicxml(path)
    elif suffix in MIDI_SUFFIXES:
        song = _read_midi(path)
    else:
        raise ConversionError(tr("Formato não suportado: {0}").format(suffix or tr('sem extensão')))

    if not song.syllables:
        raise ConversionError(
            tr("O arquivo não tem letra. Baixe o MusicXML de uma partitura com letra ou use um MIDI que contenha eventos de letra.")
        )
    return song


def read_musicxml(path: Path) -> Song:
    # Imported here because music21 takes seconds to load. Its warning about optional
    # packages is hidden: nothing used here needs them.
    with contextlib.redirect_stderr(io.StringIO()):
        import music21

    try:
        score = music21.converter.parse(str(path))
    except Exception as exc:  # music21 raises many unrelated types on bad input
        raise ConversionError(tr("Não consegui ler o MusicXML: {0}").format(exc)) from exc
    if not isinstance(score, music21.stream.Score):
        raise ConversionError(tr("O MusicXML não contém uma partitura completa."))

    _merge_split_endings(score, music21)
    try:
        expanded = score.expandRepeats()
    except Exception:  # music21 cannot follow these repeats: play straight through instead.
        _remove_repeats(score, music21)
        expanded = score

    try:
        with tempfile.TemporaryDirectory() as tmp:
            midi_path = Path(tmp) / "score.mid"
            expanded.write("midi", fp=str(midi_path))
            midi = _with_resolution(mido.MidiFile(str(midi_path)), OUTPUT_TICKS_PER_BEAT)
    except Exception as exc:  # music21 raises many unrelated types on unusual scores
        raise ConversionError(tr("Não consegui preparar a música dessa partitura: {0}").format(exc)) from exc

    metadata = score.metadata
    return Song(
        midi=midi,
        syllables=_musicxml_syllables(expanded, midi.ticks_per_beat),
        title=(metadata.title or "") if metadata else "",
        artist=(metadata.composer or "") if metadata else "",
    )


def _merge_split_endings(score, music21) -> None:
    """Join a first or second ending exported as one marking per measure, as MuseScore can do.

    music21 refuses such a score, although its meaning is clear.
    """
    for part in score.parts:
        previous = None
        for bracket in list(part.recurse().getElementsByClass(music21.spanner.RepeatBracket)):
            measures = bracket.getSpannedElements()
            if previous is not None and bracket.number == previous.number and _follows(previous, measures):
                for measure in measures:
                    previous.addSpannedElements(measure)
                part.remove(bracket, recurse=True)
            else:
                previous = bracket


def _follows(bracket, measures: list) -> bool:
    return bool(measures) and measures[0].number == bracket.getSpannedElements()[-1].number + 1


def _remove_repeats(score, music21) -> None:
    for part in score.parts:
        for measure in part.getElementsByClass("Measure"):
            if isinstance(measure.leftBarline, music21.bar.Repeat):
                measure.leftBarline = None
            if isinstance(measure.rightBarline, music21.bar.Repeat):
                measure.rightBarline = None
            for expression in list(measure.getElementsByClass(music21.repeat.RepeatExpression)):
                measure.remove(expression)
        for bracket in list(part.recurse().getElementsByClass(music21.spanner.RepeatBracket)):
            part.remove(bracket, recurse=True)


def _with_resolution(midi: mido.MidiFile, ticks_per_beat: int) -> mido.MidiFile:
    """Rescale to a resolution old karaoke players handle; music21 writes a very high one."""
    scaled = mido.MidiFile(type=midi.type, ticks_per_beat=ticks_per_beat, charset=midi.charset)
    for track in midi.tracks:
        new_track = mido.MidiTrack()
        source_tick = 0
        target_tick = 0
        for message in track:
            source_tick += message.time
            tick = round(source_tick * ticks_per_beat / midi.ticks_per_beat)
            new_track.append(message.copy(time=tick - target_tick))
            target_tick = tick
        scaled.tracks.append(new_track)
    return scaled


def _musicxml_syllables(score, ticks_per_beat: int) -> list[Syllable]:
    best: list[Syllable] = []
    for part in score.parts:
        syllables = _part_syllables(part, ticks_per_beat)
        if len(syllables) > len(best):
            best = syllables
    return best


def _part_syllables(part, ticks_per_beat: int) -> list[Syllable]:
    syllables = []
    passes: dict[int, int] = {}
    previous_continues = False
    for measure in part.getElementsByClass("Measure"):
        # After expansion a measure number recurs once per pass, and each pass
        # sings its own verse.
        passes[measure.number] = passes.get(measure.number, 0) + 1
        verse = passes[measure.number]
        for note in measure.recurse().notes:
            lyric = _lyric_for_verse(note.lyrics, verse)
            if lyric is None or not (lyric.text or "").strip():
                continue
            start = measure.offset + note.getOffsetInHierarchy(measure)
            syllables.append(
                Syllable(
                    tick=round(float(start) * ticks_per_beat),
                    end=round(float(start + note.quarterLength) * ticks_per_beat),
                    text=lyric.text.strip(),
                    word_start=not previous_continues,
                )
            )
            previous_continues = lyric.syllabic in ("begin", "middle")
    syllables.sort(key=lambda s: s.tick)
    return syllables


def _lyric_for_verse(lyrics, verse: int):
    for lyric in lyrics:
        if lyric.number == verse:
            return lyric
    # A refrain usually only has verse 1 written, and is sung on every pass.
    return lyrics[0] if len(lyrics) == 1 else None


def _read_midi(path: Path) -> Song:
    try:
        midi = mido.MidiFile(str(path), charset="latin1")
    except Exception as exc:  # mido raises several types on corrupt files
        raise ConversionError(tr("Não consegui ler o MIDI: {0}").format(exc)) from exc

    syllables = _midi_syllables(midi, "lyrics") or _midi_syllables(midi, "text")
    return Song(midi=midi, syllables=syllables)


def _midi_syllables(midi: mido.MidiFile, event_type: str) -> list[Syllable]:
    events: list[tuple[int, str]] = []
    for track in midi.tracks:
        tick = 0
        found = []
        for message in track:
            tick += message.time
            if message.type == event_type and not message.text.startswith("@"):
                found.append((tick, _fix_encoding(message.text)))
        if len(found) > len(events):
            events = found

    has_spacing = any(
        raw != raw.strip() or raw[:1] in ("/", "\\") for _, raw in events
    )
    syllables = []
    previous = ""
    for tick, raw in events:
        forced = raw[0] if raw[:1] in ("/", "\\") else ""
        body = raw[1:] if forced else raw
        text = body.strip().rstrip("-")
        if not text:
            previous = raw
            continue
        if has_spacing:
            word_start = (
                bool(forced) or body[:1].isspace() or previous[-1:].isspace()
            )
        else:
            word_start = not previous.endswith("-")
        syllables.append(
            Syllable(
                tick=tick,
                end=tick + midi.ticks_per_beat,
                text=text,
                word_start=word_start or not syllables,
                forced_break=forced,
            )
        )
        previous = raw
    return syllables


def _fix_encoding(text: str) -> str:
    """Undo UTF-8 lyrics having been decoded as latin-1."""
    try:
        return text.encode("latin1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def build_kar(song: Song, title: str, artist: str) -> mido.MidiFile:
    source = song.midi
    output = mido.MidiFile(type=1, ticks_per_beat=source.ticks_per_beat, charset="latin1")

    tracks = [_without_lyrics(track) for track in source.tracks]
    output.tracks.append(tracks[0])
    output.tracks.append(_identification_track())
    output.tracks.append(_words_track(song.syllables, title, artist, source.ticks_per_beat))
    output.tracks.extend(tracks[1:])
    return output


def _without_lyrics(track: mido.MidiTrack) -> mido.MidiTrack:
    cleaned = mido.MidiTrack()
    carried = 0
    for message in track:
        if message.type in ("lyrics", "text"):
            carried += message.time
            continue
        cleaned.append(message.copy(time=message.time + carried))
        carried = 0
    return cleaned


def _identification_track() -> mido.MidiTrack:
    return mido.MidiTrack(
        [
            mido.MetaMessage("track_name", name="Soft Karaoke", time=0),
            mido.MetaMessage("text", text="@KMIDI KARAOKE FILE", time=0),
            mido.MetaMessage("end_of_track", time=0),
        ]
    )


def _words_track(
    syllables: list[Syllable], title: str, artist: str, ticks_per_beat: int
) -> mido.MidiTrack:
    track = mido.MidiTrack([mido.MetaMessage("track_name", name="Words", time=0)])
    header = ["@LPORT"]
    if title or artist:
        # The first @T is the title, so keep its slot even when only the artist is known.
        header.append(f"@T{_latin1(title)}")
    if artist:
        header.append(f"@T{_latin1(artist)}")
    track.extend(mido.MetaMessage("text", text=text, time=0) for text in header)

    now = 0
    for tick, text in _karaoke_events(syllables, ticks_per_beat):
        track.append(mido.MetaMessage("text", text=text, time=tick - now))
        now = tick
    track.append(mido.MetaMessage("end_of_track", time=0))
    return track


def _karaoke_events(
    syllables: list[Syllable], ticks_per_beat: int
) -> list[tuple[int, str]]:
    """Add Soft Karaoke markers: "/" is a new line, "\\" a new screen."""
    source_has_breaks = any(s.forced_break for s in syllables)
    gap_limit = LINE_BREAK_GAP_BEATS * ticks_per_beat
    events = []
    line_chars = 0
    lines_on_screen = 1

    for index, syllable in enumerate(syllables):
        text = _latin1(syllable.text).replace("/", "").replace("\\", "").lstrip("@")
        if index == 0:
            prefix = "\\"
        elif source_has_breaks:
            prefix = syllable.forced_break or (" " if syllable.word_start else "")
        elif syllable.word_start:
            word_chars = _word_length(syllables, index)
            gap = syllable.tick - syllables[index - 1].end
            if gap >= gap_limit or line_chars + 1 + word_chars > MAX_LINE_CHARS:
                lines_on_screen += 1
                if lines_on_screen > LINES_PER_SCREEN:
                    prefix = "\\"
                    lines_on_screen = 1
                else:
                    prefix = "/"
            else:
                prefix = " "
        else:
            prefix = ""

        line_chars = len(text) if prefix in ("\\", "/") else line_chars + len(prefix) + len(text)
        events.append((syllable.tick, prefix + text))
    return events


def _word_length(syllables: list[Syllable], start: int) -> int:
    length = len(syllables[start].text)
    for syllable in syllables[start + 1 :]:
        if syllable.word_start:
            break
        length += len(syllable.text)
    return length


def _latin1(text: str) -> str:
    """.kar text events are single-byte; replace what latin-1 cannot represent."""
    return text.translate(TYPOGRAPHIC_TO_ASCII).encode("latin1", "replace").decode("latin1")
