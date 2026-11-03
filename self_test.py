"""Checks that an installed copy works: run with --self-test RESULT_FILE [VIDEO_FILE].

This is how a build made for a system without a machine to try it on gets tested. The
results go to a file because a Windows program with a window has no console to print to.
"""

from __future__ import annotations

import array
import os
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path


def run(result_path: str, video_path: str | None) -> int:
    work = Path(tempfile.mkdtemp())
    # Nothing here may touch the settings, numbers or cache of whoever runs it.
    os.environ["XDG_CACHE_HOME"] = str(work / "cache")
    os.environ["QT_QPA_PLATFORM"] = "offscreen"

    import mido
    from PySide6.QtCore import QSettings
    from PySide6.QtGui import QColor, QImage
    from PySide6.QtWidgets import QApplication

    application = QApplication.instance() or QApplication([])
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(work / "config"))

    songs = work / "musicas"
    songs.mkdir()
    for name in ("a.kar", "b.kar"):
        _make_song(mido, songs / name)

    def fluidsynth():
        import ctypes

        import synth

        library = synth._load_library()
        library.fluid_version_str.restype = ctypes.c_char_p
        soundfont = synth.find_soundfont()
        assert soundfont and Path(soundfont).parent.parent == Path(synth.__file__).parent, soundfont
        return f"{library.fluid_version_str().decode()}, {Path(soundfont).name}"

    def audio():
        import song
        import synth

        engine = synth.Synth(synth.find_soundfont(), realtime=False)
        engine.load(song.load_song(songs / "a.kar").midi_bytes)
        engine.play()
        samples = array.array("h")
        for _ in range(40):
            samples.frombytes(engine.render_s16(1024))
        engine.close()
        peak = max(abs(sample) for sample in samples)
        assert peak > 500, peak
        return f"pico {peak}"

    def envelope():
        import score
        import song
        import synth

        loaded = song.load_song(songs / "a.kar")
        ticks, levels = score.render_music_envelope(loaded.midi_bytes, synth.find_soundfont(), loaded.total_ticks)
        assert len(ticks) > 20 and max(levels) > -60
        return f"{len(ticks)} blocos"

    def window():
        from player import PlayerWindow, config_directory

        instance = PlayerWindow(realtime=False)
        instance.show()
        instance.set_song_folder(str(songs))
        assert instance._catalog_list.count() == 2
        instance.add_to_queue(instance._catalog[0])
        instance.play_queue()
        assert instance._synth.is_playing
        application.processEvents()
        for name in ("light", "dark"):
            instance._apply_theme(name)
            application.processEvents()
        assert not instance.grab().isNull()
        pdf = instance.song_list_pdf_path()
        assert pdf.read_bytes().startswith(b"%PDF")
        assert not list(songs.glob("*.pdf"))
        assert (config_directory() / "freekaraoke.sqlite").stat().st_size > 0
        assert Path(instance._settings.fileName()).name == "freekaraoke.ini"
        instance.close()
        return str(pdf.parent)

    def musicxml():
        import song

        path = work / "partitura.musicxml"
        path.write_text(_score_with_split_ending(), encoding="utf-8")
        start = time.monotonic()
        first = song.load_song(path)
        assert first.title == "Canção"
        assert "avó" in first.timeline.lyrics
        # Measures 1, 2, 3, then 1 again and the second ending: five measures of two.
        assert len(first.timeline.syllables) == 10, len(first.timeline.syllables)
        assert song.is_prepared_score(path)
        return f"{time.monotonic() - start:.1f} s na primeira abertura"

    def st3():
        from conversor import convert_file

        path = work / "partitura.musicxml"
        data, _ = convert_file(path)
        assert data.startswith(b"STAR DATA V3.00")
        import st3 as reader

        assert reader.parse_st3(data).lyrics
        return f"{len(data)} bytes"

    def recording():
        import numpy as np
        import soundfile

        import song
        import synth
        from recorder import OUTPUT_RATE, Recording

        loaded = song.load_song(songs / "a.kar")
        take = Recording("Teste", loaded.ticks_per_beat, synth.find_soundfont())
        block = (0.2 * np.sin(np.arange(2400) * 0.1)).astype(np.float32)
        for index in range(60):
            take.add(block, OUTPUT_RATE, index * 10, 0.5, 1.0, loaded.midi_bytes)
        target = work / "gravacao.opus"
        take.save(target)
        with soundfile.SoundFile(target) as result:
            assert result.samplerate == OUTPUT_RATE and result.channels == 2 and result.subtype == "OPUS"
            assert abs(result.frames - 60 * 2400) < 4800, result.frames
        return f"{target.stat().st_size // 1024} KB em Opus"

    def fonts():
        from PySide6.QtGui import QFontDatabase

        from player import DEFAULT_FONT_FAMILY, load_bundled_fonts

        load_bundled_fonts()
        assert DEFAULT_FONT_FAMILY in QFontDatabase.families()

    def svg():
        from theme import ICON_DIRECTORY

        assert not QImage(str(ICON_DIRECTORY / "chevron-for-dark.svg")).isNull()

    def background_image():
        from background import BackgroundRotator

        folder = work / "imagens"
        folder.mkdir()
        image = QImage(16, 16, QImage.Format.Format_RGB32)
        image.fill(QColor("red"))
        image.save(str(folder / "a.jpg"))
        rotator = BackgroundRotator()
        seen = []
        rotator.frame_ready.connect(seen.append)
        assert rotator.set_folder(folder) == 1
        rotator.start()
        application.processEvents()
        rotator.stop()
        assert seen

    def background_video():
        from background import BackgroundRotator

        assert video_path and Path(video_path).is_file(), "sem vídeo de teste"
        folder = work / "videos"
        folder.mkdir()
        (folder / "v.mp4").write_bytes(Path(video_path).read_bytes())
        rotator = BackgroundRotator()
        frames = []
        rotator.frame_ready.connect(frames.append)
        rotator.set_folder(folder)
        rotator.start()
        end = time.monotonic() + 20
        while len(frames) < 3 and time.monotonic() < end:
            application.processEvents()
            time.sleep(0.01)
        rotator.stop()
        assert len(frames) >= 3, len(frames)
        assert rotator._player.audioOutput() is None
        return f"{len(frames)} quadros"

    def microphone():
        from score import MicrophoneMeter

        meter = MicrophoneMeter()
        levels = []
        meter.level_measured.connect(levels.append)
        meter.consume(array.array("h", [3276] * 800).tobytes())
        assert len(levels) == 1
        if sys.platform != "win32":
            # Qt can crash when opening one on a machine with a partial audio install, such
            # as a Linux build container. Windows is where the format handling needs checking.
            return "abertura não testada neste sistema"
        started = meter.start()  # A build machine may have no microphone at all.
        meter.stop()
        return "microfone encontrado" if started else "sem microfone nesta máquina"

    def linux_plugins():
        # Only the AppImage sets this; running from source uses whatever the machine has.
        if not os.environ.get("KARAOKE_FLUIDSYNTH_LIB"):
            return "não se aplica"
        import PySide6

        qt = Path(PySide6.__file__).parent / "Qt"
        checked = [*(qt / "plugins").glob("platforms/*.so"), *(qt / "plugins").glob("multimedia/*.so")]
        for plugin in checked:
            output = subprocess.run(["ldd", str(plugin)], capture_output=True, text=True).stdout
            # A Qt library from the system instead of the package is another version, which
            # fails on load: ldd alone would call that fine.
            outside = [
                line.split()[2] for line in output.splitlines()
                if "libQt6" in line and "=> /" in line and not line.split()[2].startswith(str(qt))
            ]
            assert not outside, (plugin.name, outside)
            if plugin.name in ("libqxcb.so", "libqwayland.so", "libffmpegmediaplugin.so"):
                absent = [line.split()[0] for line in output.splitlines() if "not found" in line]
                assert not absent, (plugin.name, absent)

    checks = (
        ("FluidSynth e SoundFont do pacote", fluidsynth),
        ("som sintetizado", audio),
        ("nível da música para a nota", envelope),
        ("janela, fila, tema, PDF e banco", window),
        ("partitura MusicXML com repetição", musicxml),
        ("conversão para .st3", st3),
        ("gravação em Opus", recording),
        ("fonte incluída", fonts),
        ("imagem SVG do tema", svg),
        ("imagem de fundo", background_image),
        ("vídeo de fundo", background_video),
        ("microfone", microphone),
        ("bibliotecas do Qt", linux_plugins),
    )
    failed = 0
    with open(result_path, "w", encoding="utf-8") as report:
        from i18n import LANGUAGE

        report.write(f"sistema: {sys.platform}; idioma: {LANGUAGE}\n")
        for name, check in checks:
            try:
                detail = check()
                report.write(f"ok    {name}" + (f": {detail}" if detail else "") + "\n")
            except Exception:
                failed += 1
                report.write(f"FALHA {name}\n{traceback.format_exc()}\n")
            report.flush()
        report.write(f"RESULTADO: {'tudo certo' if not failed else f'{failed} falhas'}\n")
    return 1 if failed else 0


def _make_song(mido, path: Path) -> None:
    midi = mido.MidiFile(type=1, ticks_per_beat=96, charset="latin1")
    words = mido.MidiTrack(
        [
            mido.MetaMessage("text", text="@KMIDI KARAOKE FILE", time=0),
            mido.MetaMessage("text", text="@TCanção", time=0),
            mido.MetaMessage("text", text="@TLegião", time=0),
            mido.MetaMessage("text", text="\\Can", time=0),
            mido.MetaMessage("text", text="tar", time=96),
        ]
    )
    music = mido.MidiTrack()
    for _ in range(16):
        music.append(mido.Message("note_on", note=60, velocity=100, time=0))
        music.append(mido.Message("note_off", note=60, velocity=0, time=96))
    midi.tracks.extend([words, music])
    midi.save(str(path))


def _score_with_split_ending() -> str:
    """Measures 1-3 repeat, with an ending marked once per measure, as MuseScore can export."""

    def note(word):
        return (
            '<note><pitch><step>C</step><octave>4</octave></pitch><duration>1</duration><type>quarter</type>'
            f'<lyric number="1"><syllabic>single</syllabic><text>{word}</text></lyric></note>'
        )

    attributes = "<attributes><divisions>1</divisions><time><beats>2</beats><beat-type>4</beat-type></time></attributes>"
    first = '<barline location="left"><ending number="1" type="start"/></barline>'
    middle_end = '<barline location="right"><ending number="1" type="discontinue"/></barline>'
    last_end = '<barline location="right"><ending number="1" type="stop"/><repeat direction="backward"/></barline>'
    second = '<barline location="left"><ending number="2" type="start"/></barline>'
    second_end = '<barline location="right"><ending number="2" type="stop"/></barline>'
    measures = (
        f'<measure number="1">{attributes}{note("um")}{note("dois")}</measure>'
        f'<measure number="2">{first}{note("tres")}{note("quatro")}{middle_end}</measure>'
        f'<measure number="3">{first}{note("avó")}{note("seis")}{last_end}</measure>'
        f'<measure number="4">{second}{note("sete")}{note("oito")}{second_end}</measure>'
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?><score-partwise version="3.1"><work><work-title>Canção</work-title></work>'
        '<part-list><score-part id="P1"><part-name>Voz</part-name></score-part></part-list>'
        f'<part id="P1">{measures}</part></score-partwise>'
    )
