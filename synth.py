"""Minimal ctypes binding for libfluidsynth: load a MIDI from memory and play it.

The player's own tick is the clock for the lyrics, so they cannot drift from
the audio. Using ctypes also avoids a compiled dependency in the AppImage.
"""

from __future__ import annotations

from i18n import tr

import ctypes
import ctypes.util
import os
import sys
from ctypes import POINTER, c_char, c_char_p, c_double, c_float, c_int, c_size_t, c_void_p
from pathlib import Path

FLUID_FAILED = -1
FLUID_PLAYER_PLAYING = 1
FLUID_PLAYER_TEMPO_INTERNAL = 0
# DirectSound comes first on Windows because Windows resamples for it: FluidSynth's WASAPI
# output can run at the wrong speed when the device's rate differs from the synthesizer's.
AUDIO_DRIVERS = (
    ("dsound", "wasapi", "waveout", "sdl3", "sdl2")
    if sys.platform == "win32"
    else ("pulseaudio", "pipewire", "alsa", "jack", "sdl3", "sdl2", "oss")
)
WINDOWS_LIBRARY = Path(__file__).resolve().parent / "fluidsynth" / "libfluidsynth-3.dll"
SOUNDFONT_SEARCH = (
    str(Path(__file__).resolve().parent / "soundfonts" / "FluidR3_GM.sf2"),
    "/usr/share/sounds/sf2/FluidR3_GM.sf2",
    "/usr/share/soundfonts/FluidR3_GM.sf2",
    "/usr/share/sounds/sf2/default-GM.sf2",
    "/usr/share/soundfonts/default.sf2",
    "/usr/share/sounds/sf2/TimGM6mb.sf2",
)
SAMPLE_RATE = 44100.0
# Measured with FluidR3_GM: above this, some songs clip on their loudest passages.
DEFAULT_GAIN = 0.25


class SynthError(RuntimeError):
    """The synthesizer could not start; the message is user-facing."""


def find_soundfont() -> str | None:
    configured = os.environ.get("KARAOKE_SOUNDFONT")
    for candidate in ((configured,) if configured else ()) + SOUNDFONT_SEARCH:
        if Path(candidate).is_file():
            return candidate
    return None


def _load_library():
    name = os.environ.get("KARAOKE_FLUIDSYNTH_LIB")
    if not name and sys.platform == "win32" and WINDOWS_LIBRARY.is_file():
        # Its other DLLs sit next to it, where Windows would not look on its own.
        os.add_dll_directory(str(WINDOWS_LIBRARY.parent))
        name = str(WINDOWS_LIBRARY)
    name = name or ctypes.util.find_library("fluidsynth")
    try:
        library = ctypes.CDLL(name or "libfluidsynth.so.3")
    except OSError as exc:
        raise SynthError(tr("A biblioteca FluidSynth não foi encontrada.")) from exc

    signatures = {
        "new_fluid_settings": (c_void_p, []),
        "delete_fluid_settings": (None, [c_void_p]),
        "fluid_settings_setstr": (c_int, [c_void_p, c_char_p, c_char_p]),
        "fluid_settings_setnum": (c_int, [c_void_p, c_char_p, c_double]),
        "new_fluid_synth": (c_void_p, [c_void_p]),
        "delete_fluid_synth": (None, [c_void_p]),
        "fluid_synth_sfload": (c_int, [c_void_p, c_char_p, c_int]),
        "fluid_synth_set_gain": (None, [c_void_p, c_float]),
        "fluid_synth_all_sounds_off": (c_int, [c_void_p, c_int]),
        "fluid_synth_write_s16": (
            c_int,
            [c_void_p, c_int, c_void_p, c_int, c_int, c_void_p, c_int, c_int],
        ),
        "new_fluid_audio_driver": (c_void_p, [c_void_p, c_void_p]),
        "delete_fluid_audio_driver": (None, [c_void_p]),
        "new_fluid_player": (c_void_p, [c_void_p]),
        "delete_fluid_player": (None, [c_void_p]),
        "fluid_player_add_mem": (c_int, [c_void_p, POINTER(c_char), c_size_t]),
        "fluid_player_play": (c_int, [c_void_p]),
        "fluid_player_stop": (c_int, [c_void_p]),
        "fluid_player_join": (c_int, [c_void_p]),
        "fluid_player_seek": (c_int, [c_void_p, c_int]),
        "fluid_player_get_current_tick": (c_int, [c_void_p]),
        "fluid_player_get_status": (c_int, [c_void_p]),
        "fluid_player_set_tempo": (c_int, [c_void_p, c_int, c_double]),
        "fluid_set_log_function": (c_void_p, [c_int, c_void_p, c_void_p]),
    }
    for function, (result, arguments) in signatures.items():
        symbol = getattr(library, function)
        symbol.restype = result
        symbol.argtypes = arguments
    # Silence warning and info logs from malformed songs; errors surface as return codes.
    for level in (1, 2, 3, 4):
        library.fluid_set_log_function(level, None, None)
    return library


class Synth:
    """Owns one synthesizer and the current song's player."""

    def __init__(
        self, soundfont: str, *, realtime: bool = True, gain: float = DEFAULT_GAIN, sample_rate: float = SAMPLE_RATE
    ):
        self._lib = _load_library()
        self._player = None
        self._driver = None
        self._midi_buffer = None
        self._paused = False
        self._pending_seek: int | None = None
        self._gain = gain
        self.audio_driver_name = ""

        self._settings = self._lib.new_fluid_settings()
        self._lib.fluid_settings_setnum(self._settings, b"synth.gain", gain)
        self._lib.fluid_settings_setnum(self._settings, b"synth.sample-rate", sample_rate)
        self._synth = self._lib.new_fluid_synth(self._settings)
        if not self._synth:
            self.close()
            raise SynthError(tr("Não consegui iniciar o sintetizador."))
        if self._lib.fluid_synth_sfload(self._synth, os.fsencode(soundfont), 1) == FLUID_FAILED:
            self.close()
            raise SynthError(tr("Não consegui carregar o SoundFont: {0}").format(soundfont))
        if realtime:
            self._open_audio()

    def _open_audio(self) -> None:
        for name in AUDIO_DRIVERS:
            if self._lib.fluid_settings_setstr(self._settings, b"audio.driver", name.encode()) == FLUID_FAILED:
                continue
            self._driver = self._lib.new_fluid_audio_driver(self._settings, self._synth)
            if self._driver:
                self.audio_driver_name = name
                return
        self.close()
        raise SynthError(tr("Não encontrei nenhuma saída de áudio disponível."))

    def load(self, midi_bytes: bytes) -> None:
        self.unload()
        self._player = self._lib.new_fluid_player(self._synth)
        # FluidSynth reads the buffer when playback starts, so it must stay alive.
        self._midi_buffer = ctypes.create_string_buffer(midi_bytes, len(midi_bytes))
        status = self._lib.fluid_player_add_mem(self._player, self._midi_buffer, len(midi_bytes))
        if status == FLUID_FAILED:
            self.unload()
            raise SynthError(tr("A FluidSynth recusou o arquivo MIDI."))
        self._paused = True

    def play(self) -> None:
        if self._player:
            self._lib.fluid_player_play(self._player)
            self._paused = False

    def pause(self) -> None:
        if self._player:
            self._paused = True
            self._lib.fluid_player_stop(self._player)
            self._lib.fluid_synth_all_sounds_off(self._synth, -1)

    def seek(self, tick: int) -> None:
        # FluidSynth ignores a seek until it has read the file, which it does when playback
        # first runs, so that one waits. Silence keeps the song's start from being heard.
        if self._player and tick > 0 and self._raw_tick() == 0:
            self._pending_seek = int(tick)
            self._lib.fluid_synth_set_gain(self._synth, 0.0)
            return
        if self._player:
            self._lib.fluid_player_seek(self._player, max(0, int(tick)))
            self._lib.fluid_synth_all_sounds_off(self._synth, -1)

    def set_gain(self, gain: float) -> None:
        self._gain = gain
        if self._pending_seek is None:
            self._lib.fluid_synth_set_gain(self._synth, gain)

    def set_tempo_factor(self, factor: float) -> None:
        """Scale the song's own tempo: 1.0 is as written, 0.5 is half speed."""
        if self._player:
            self._lib.fluid_player_set_tempo(self._player, FLUID_PLAYER_TEMPO_INTERNAL, factor)

    @property
    def tick(self) -> int:
        raw = self._raw_tick()
        if self._pending_seek is None:
            return raw
        target = self._pending_seek
        if raw > 0:
            self._pending_seek = None
            self._lib.fluid_player_seek(self._player, target)
            self._lib.fluid_synth_all_sounds_off(self._synth, -1)
            self._lib.fluid_synth_set_gain(self._synth, self._gain)
        return target

    def _raw_tick(self) -> int:
        return self._lib.fluid_player_get_current_tick(self._player) if self._player else 0

    @property
    def is_playing(self) -> bool:
        return bool(self._player) and not self._paused

    @property
    def finished(self) -> bool:
        """True when playback ran to the end on its own, not when paused."""
        if not self._player or self._paused:
            return False
        return self._lib.fluid_player_get_status(self._player) != FLUID_PLAYER_PLAYING

    def render_s16(self, frames: int) -> bytes:
        """Render stereo audio without an audio driver; used for offline checks."""
        left = (ctypes.c_int16 * (frames * 2))()
        self._lib.fluid_synth_write_s16(self._synth, frames, left, 0, 2, left, 1, 2)
        return bytes(left)

    def unload(self) -> None:
        if self._player:
            self._lib.fluid_player_stop(self._player)
            self._lib.fluid_player_join(self._player)
            self._lib.delete_fluid_player(self._player)
            self._lib.fluid_synth_all_sounds_off(self._synth, -1)
        if self._pending_seek is not None and getattr(self, "_synth", None):
            self._lib.fluid_synth_set_gain(self._synth, self._gain)
        self._pending_seek = None
        self._player = None
        self._midi_buffer = None
        self._paused = False

    def close(self) -> None:
        self.unload()
        if self._driver:
            self._lib.delete_fluid_audio_driver(self._driver)
            self._driver = None
        if getattr(self, "_synth", None):
            self._lib.delete_fluid_synth(self._synth)
            self._synth = None
        if getattr(self, "_settings", None):
            self._lib.delete_fluid_settings(self._settings)
            self._settings = None
