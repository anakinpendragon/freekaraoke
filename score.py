"""Rhythm score from 0 to 100: does the voice sound when singing is due? Pitch is ignored."""

from __future__ import annotations

import bisect
import math
import threading

import numpy as np
from PySide6.QtCore import QObject, Signal
from PySide6.QtMultimedia import QAudioFormat, QAudioSource, QMediaDevices

from synth import Synth, SynthError

FRAME_MS = 50
SAMPLE_RATE = 16000
# NumPy type and full-scale value of each sample format a microphone may deliver.
SAMPLE_FORMATS = {
    QAudioFormat.SampleFormat.Int16: ("<i2", 32768.0),
    QAudioFormat.SampleFormat.Int32: ("<i4", 2147483648.0),
    QAudioFormat.SampleFormat.Float: ("<f4", 1.0),
}
# Microphones burst at full level for a moment when opened; that is not a voice.
SETTLE_FRAMES = 1000 // FRAME_MS
# Quietest level that can count as a voice, however silent the room is.
MIN_VOICE_DB = -50.0
# A voice must stand this far above the background, which includes the music that
# the speakers send back into the microphone.
VOICE_ABOVE_BACKGROUND_DB = 9.0
# With the song's own level known, a voice must exceed what the speakers explain.
VOICE_ABOVE_MUSIC_DB = 6.0
# Passages this far below the song's loudest say nothing about the speakers.
AUDIBLE_MUSIC_RANGE_DB = 50.0
ROOM_FLOOR_PERCENTILE = 0.05
ENVELOPE_BLOCK_FRAMES = 2205
ENVELOPE_WARMUP_FRAMES = 64
# Fewer frames than this outside the sung phrases do not reveal the background.
MIN_BACKGROUND_FRAMES = 20
FALLBACK_BACKGROUND_PERCENTILE = 0.1
LEVEL_WITH_VOICE_DB = -20.0
LEVEL_WITHOUT_VOICE_DB = -80.0
# A syllable counts as sung when the voice sounds for this share of its window.
SYLLABLE_VOICED_SHARE = 0.25
TOLERANCE_BEATS = 0.5
PHRASE_GAP_BEATS = 2
# With less than this share of the syllables heard (the song was skipped), no score.
MIN_COVERAGE = 0.5


class RhythmScorer:
    def __init__(self, syllables, ticks_per_beat: int):
        self._syllables = sorted(((s.tick, s.tick + s.duration) for s in syllables))
        self._tolerance = round(TOLERANCE_BEATS * ticks_per_beat)
        self._phrases = self._merge_phrases(round(PHRASE_GAP_BEATS * ticks_per_beat))
        self._frames: list[tuple[int, float]] = []
        self._music_ticks: list[int] = []
        self._music_levels: list[float] = []

    def set_music_envelope(self, ticks: list[int], levels: list[float]) -> None:
        """The song's own level over time, which tells apart its loud parts from a voice."""
        self._music_levels = levels
        self._music_ticks = ticks

    def add_level(self, tick: int, level_db: float) -> None:
        self._frames.append((tick, level_db))

    def add_frame(self, tick: int, voiced: bool) -> None:
        self.add_level(tick, LEVEL_WITH_VOICE_DB if voiced else LEVEL_WITHOUT_VOICE_DB)

    def _voiced_frames(self) -> list[tuple[int, bool]]:
        if len(self._music_ticks) >= MIN_BACKGROUND_FRAMES:
            return self._voiced_against_music()
        threshold = self._voice_threshold()
        return [(tick, level > threshold) for tick, level in self._frames]

    def _voiced_against_music(self) -> list[tuple[int, bool]]:
        """Compare each moment with how loud the speakers are known to be at that moment."""
        audible_from = max(self._music_levels) - AUDIBLE_MUSIC_RANGE_DB
        expected = [self._music_level_at(tick) for tick, _ in self._frames]
        differences = []
        outside = []
        for (tick, level), music in zip(self._frames, expected):
            if music > audible_from:
                differences.append(level - music)
                if not self._in_phrase(tick):
                    outside.append(level - music)
        if len(outside) >= MIN_BACKGROUND_FRAMES:
            leak = sorted(outside)[len(outside) // 2]
        elif differences:
            ordered = sorted(differences)
            leak = ordered[len(ordered) // 5]
        else:
            leak = 0.0
        levels = sorted(level for _, level in self._frames)
        room = levels[int(len(levels) * ROOM_FLOOR_PERCENTILE)]
        return [
            (tick, level > max(MIN_VOICE_DB, max(music + leak, room) + VOICE_ABOVE_MUSIC_DB))
            for (tick, level), music in zip(self._frames, expected)
        ]

    def _music_level_at(self, tick: int) -> float:
        index = bisect.bisect_right(self._music_ticks, tick) - 1
        return self._music_levels[max(index, 0)]

    def _voice_threshold(self) -> float:
        """The background is measured where no singing is due: intro, solos, ending."""
        outside = sorted(level for tick, level in self._frames if not self._in_phrase(tick))
        if len(outside) >= MIN_BACKGROUND_FRAMES:
            background = outside[len(outside) // 2]
        else:
            levels = sorted(level for _, level in self._frames)
            background = levels[int(len(levels) * FALLBACK_BACKGROUND_PERCENTILE)]
        return max(MIN_VOICE_DB, background + VOICE_ABOVE_BACKGROUND_DB)

    def result(self) -> int | None:
        """The score, or None if too little of the song was listened to."""
        if not self._syllables or not self._frames:
            return None
        frames = sorted(self._voiced_frames())
        ticks = [tick for tick, _ in frames]
        heard = 0
        sung = 0
        for start, end in self._syllables:
            first = bisect.bisect_left(ticks, start - self._tolerance)
            last = bisect.bisect_right(ticks, end + self._tolerance)
            if first == last:
                continue
            heard += 1
            voiced = sum(frames[index][1] for index in range(first, last))
            sung += voiced / (last - first) >= SYLLABLE_VOICED_SHARE
        if heard < MIN_COVERAGE * len(self._syllables):
            return None

        voiced_ticks = [tick for tick, voiced in frames if voiced]
        if not voiced_ticks or not sung:
            return 0
        recall = sung / heard
        precision = sum(self._in_phrase(tick) for tick in voiced_ticks) / len(voiced_ticks)
        return round(100 * 2 * precision * recall / (precision + recall))

    def _merge_phrases(self, gap: int) -> list[tuple[int, int]]:
        phrases: list[list[int]] = []
        for start, end in self._syllables:
            if phrases and start - phrases[-1][1] <= gap:
                phrases[-1][1] = max(phrases[-1][1], end)
            else:
                phrases.append([start, end])
        return [(start - self._tolerance, end + self._tolerance) for start, end in phrases]

    def _in_phrase(self, tick: int) -> bool:
        index = bisect.bisect_right(self._phrases, (tick, math.inf)) - 1
        return index >= 0 and tick <= self._phrases[index][1]


def render_music_envelope(
    midi_bytes: bytes, soundfont: str, total_ticks: int, cancelled: threading.Event | None = None
) -> tuple[list[int], list[float]]:
    """Render the song silently, far faster than real time, and return its level per tick."""
    synth = Synth(soundfont, realtime=False)
    ticks: list[int] = []
    levels: list[float] = []
    try:
        synth.load(midi_bytes)
        synth.play()
        synth.render_s16(ENVELOPE_WARMUP_FRAMES)  # Lets the player load the file first.
        while synth.tick <= total_ticks and not synth.finished:
            if cancelled is not None and cancelled.is_set():
                return [], []
            samples = np.frombuffer(synth.render_s16(ENVELOPE_BLOCK_FRAMES), dtype=np.int16)
            power = float(np.mean(samples.astype(np.float64) ** 2))
            ticks.append(synth.tick)
            levels.append(10 * math.log10(power / 32768**2) if power else -120.0)
    finally:
        synth.close()
    return ticks, levels


class MusicEnvelopeWorker(threading.Thread):
    """Hands a song's level to its scorer in the background, while the song plays."""

    def __init__(self, scorer: RhythmScorer, midi_bytes: bytes, soundfont: str, total_ticks: int):
        super().__init__(daemon=True)
        self._scorer = scorer
        self._arguments = (midi_bytes, soundfont, total_ticks)
        self._cancelled = threading.Event()

    def run(self) -> None:
        try:
            ticks, levels = render_music_envelope(*self._arguments, self._cancelled)
        except SynthError:
            return  # The scorer falls back to comparing with the background.
        if ticks and not self._cancelled.is_set():
            self._scorer.set_music_envelope(ticks, levels)

    def cancel(self) -> None:
        self._cancelled.set()


class MicrophoneMeter(QObject):
    """Reports the microphone's level only. Nothing is recorded or stored."""

    level_measured = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._source: QAudioSource | None = None
        self._device = None
        self._pending = bytearray()
        self._frames_to_skip = 0
        self._configure(QAudioFormat.SampleFormat.Int16, SAMPLE_RATE, 1)

    def _configure(self, sample_format, rate: int, channels: int) -> None:
        self._dtype, self._full_scale = SAMPLE_FORMATS[sample_format]
        self._frame_bytes = rate * FRAME_MS // 1000 * channels * np.dtype(self._dtype).itemsize

    def start(self) -> bool:
        """Returns False when there is no usable microphone."""
        if self._source is None:
            device = QMediaDevices.defaultAudioInput()
            audio_format = QAudioFormat()
            audio_format.setSampleRate(SAMPLE_RATE)
            audio_format.setChannelCount(1)
            audio_format.setSampleFormat(QAudioFormat.SampleFormat.Int16)
            if device.isNull():
                return False
            if not device.isFormatSupported(audio_format):
                # Windows often offers only the device's own format; a level can come from any.
                audio_format = device.preferredFormat()
            if audio_format.sampleFormat() not in SAMPLE_FORMATS:
                return False
            self._configure(audio_format.sampleFormat(), audio_format.sampleRate(), audio_format.channelCount())
            self._source = QAudioSource(device, audio_format, self)
        self._pending.clear()
        self._frames_to_skip = SETTLE_FRAMES
        self._device = self._source.start()
        if self._device is None:
            return False
        self._device.readyRead.connect(self._read)
        return True

    def stop(self) -> None:
        if self._source is not None:
            self._source.stop()
        self._device = None
        self._pending.clear()

    def _read(self) -> None:
        if self._device is not None:
            self.consume(bytes(self._device.readAll()))

    def consume(self, data: bytes) -> None:
        self._pending += data
        while len(self._pending) >= self._frame_bytes:
            frame = np.frombuffer(bytes(self._pending[: self._frame_bytes]), dtype=self._dtype)
            del self._pending[: self._frame_bytes]
            if self._frames_to_skip:
                self._frames_to_skip -= 1
            else:
                self.level_measured.emit(level_db(frame, self._full_scale))


def level_db(samples, full_scale: float = 32768.0) -> float:
    """Level relative to full scale; digital silence is reported as -120 dB."""
    power = float(np.mean((np.asarray(samples, dtype=np.float64) / full_scale) ** 2)) if len(samples) else 0.0
    return 10 * math.log10(power) if power > 0 else -120.0
