"""Records the singer and mixes the voice with a clean copy of the music into an Opus file.

Only the microphone is captured live. The music is rendered again afterwards by following
what happened during the performance (position, volume, tempo), so nothing depends on
capturing the system's sound output, which differs on every machine.
"""

from __future__ import annotations

import datetime
import re
import tempfile
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from synth import Synth

OUTPUT_RATE = 48000  # What Opus works at.
OUTPUT_SUFFIX = ".opus"
# libsndfile can crash on large writes to Ogg files, so audio goes in small pieces.
WRITE_FRAMES = 4800
RENDER_WARMUP_FRAMES = 64
# The singer hears the music late and the microphone delivers the voice late, both by a
# small fixed amount of buffering, so the voice is moved earlier by the same amount.
VOICE_LEAD_SECONDS = 0.15
# Mixing keeps the voice as captured; louder peaks are curved instead of clipped.
LIMITER_THRESHOLD = 0.9
MIN_RECORDING_SECONDS = 1.0
# Differences beyond this between the clock read live and the rendered one mean a jump.
JUMP_BEATS = 2


@dataclass(frozen=True)
class CapturedBlock:
    tick: int
    samples: int
    gain: float
    tempo_factor: float
    midi_bytes: bytes


class Recording:
    """One song's voice, kept in a temporary file because a song can last many minutes."""

    def __init__(self, title: str, ticks_per_beat: int, soundfont: str):
        self.title = title
        self._ticks_per_beat = ticks_per_beat
        self._soundfont = soundfont
        self._voice = tempfile.TemporaryFile()
        self._blocks: list[CapturedBlock] = []
        self._capture_rate = OUTPUT_RATE

    def add(self, samples: np.ndarray, rate: int, tick: int, gain: float, tempo_factor: float, midi_bytes: bytes) -> None:
        self._capture_rate = rate
        self._voice.write(samples.astype("<f4").tobytes())
        self._blocks.append(CapturedBlock(tick, len(samples), gain, tempo_factor, midi_bytes))

    @property
    def seconds(self) -> float:
        return sum(block.samples for block in self._blocks) / self._capture_rate

    def discard(self) -> None:
        self._voice.close()

    def save(self, target: Path) -> None:
        import soundfile

        if self.seconds < MIN_RECORDING_SECONDS:
            raise ValueError("too short")
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_name(f".{target.name}.tmp")
        try:
            with soundfile.SoundFile(
                partial, "w", samplerate=OUTPUT_RATE, channels=2, format="OGG", subtype="OPUS"
            ) as output:
                for mixed in self._mixed_blocks():
                    for start in range(0, len(mixed), WRITE_FRAMES):
                        output.write(mixed[start : start + WRITE_FRAMES])
            partial.replace(target)
        finally:
            partial.unlink(missing_ok=True)
            self.discard()

    def _mixed_blocks(self):
        synth = Synth(self._soundfont, realtime=False, sample_rate=OUTPUT_RATE)
        voice = _VoiceReader(self._voice, self._capture_rate)
        current_midi = None
        try:
            for block in self._blocks:
                if block.midi_bytes is not current_midi:
                    current_midi = block.midi_bytes
                    synth.load(current_midi)
                    synth.play()
                    synth.render_s16(RENDER_WARMUP_FRAMES)
                synth.set_gain(block.gain)
                synth.set_tempo_factor(block.tempo_factor)
                if abs(synth.tick - block.tick) > JUMP_BEATS * self._ticks_per_beat:
                    synth.seek(block.tick)
                frames = round(block.samples * OUTPUT_RATE / self._capture_rate)
                music = np.frombuffer(synth.render_s16(frames), dtype=np.int16).astype(np.float32) / 32768
                sung = voice.read(block.samples, frames)
                yield _limit(music.reshape(-1, 2) + sung[:, np.newaxis])
        finally:
            synth.close()


class _VoiceReader:
    def __init__(self, file, rate: int):
        self._file = file
        self._rate = rate
        file.seek(round(VOICE_LEAD_SECONDS * rate) * 4)

    def read(self, samples: int, frames: int) -> np.ndarray:
        raw = np.frombuffer(self._file.read(samples * 4), dtype="<f4")
        # What the lead skipped at the start leaves silence to fill at the very end.
        whole = np.zeros(samples, dtype=np.float32)
        whole[: len(raw)] = raw
        if samples == frames:
            return whole
        return np.interp(np.linspace(0, samples - 1, frames), np.arange(samples), whole).astype(np.float32)


def _limit(audio: np.ndarray) -> np.ndarray:
    level = np.abs(audio)
    over = level > LIMITER_THRESHOLD
    if over.any():
        room = 1 - LIMITER_THRESHOLD
        curved = LIMITER_THRESHOLD + room * np.tanh((level[over] - LIMITER_THRESHOLD) / room)
        audio = audio.copy()
        audio[over] = np.sign(audio[over]) * curved
    return audio


def recording_path(
    folder: Path, title: str, moment: datetime.datetime | None = None, taken: Iterable[Path] = ()
) -> Path:
    """A name that sorts by date and is valid on every system, never replacing a file.

    Taken names are those of recordings still being saved, whose files do not exist yet.
    """
    moment = moment or datetime.datetime.now()
    taken = set(taken)
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f\s]+', " ", title)
    safe_title = cleaned.strip(" .")[:80].strip() or "FreeKaraoke"
    base = f"{safe_title} {moment:%Y-%m-%d %H-%M-%S}"
    path = folder / f"{base}{OUTPUT_SUFFIX}"
    counter = 2
    while path.exists() or path in taken:
        path = folder / f"{base} {counter}{OUTPUT_SUFFIX}"
        counter += 1
    return path


class RecordingSaver(threading.Thread):
    """Encodes in the background. Not a daemon: closing the program must not lose the song."""

    def __init__(self, recording: Recording, target: Path, on_done):
        super().__init__()
        self._recording = recording
        self.target = target
        self._on_done = on_done

    def run(self) -> None:
        try:
            self._recording.save(self.target)
        except Exception as exc:  # The audio libraries raise many types; the user must hear of any.
            self._on_done(self.target, str(exc))
        else:
            self._on_done(self.target, "")
