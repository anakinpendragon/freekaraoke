import array
import io
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import mido
import pytest
from PySide6.QtCore import QPoint

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# Keep tests away from the user's saved folders and font.
os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="karaoke-test-config")
os.environ["XDG_CACHE_HOME"] = tempfile.mkdtemp(prefix="karaoke-test-cache")

from song import LyricTimeline, SongError, load_song, parse_kar_lyrics  # noqa: E402
from st3 import SYNC_LINE_LOWER, SYNC_LINE_UPPER, SYNC_SYLLABLE, SyncEvent  # noqa: E402
from synth import Synth, find_soundfont  # noqa: E402

TPB = 96
FRAMES_PER_SECOND = 44100


def kar_midi(words, notes=8, gap=TPB):
    midi = mido.MidiFile(type=1, ticks_per_beat=TPB, charset="latin1")
    conductor = mido.MidiTrack([mido.MetaMessage("set_tempo", tempo=500000, time=0)])
    identification = mido.MidiTrack([mido.MetaMessage("text", text="@KMIDI KARAOKE FILE", time=0)])
    lyric_track = mido.MidiTrack(
        [
            mido.MetaMessage("text", text="@LPORT", time=0),
            mido.MetaMessage("text", text="@TMinha canção", time=0),
            mido.MetaMessage("text", text="@TCantor", time=0),
        ]
    )
    for index, word in enumerate(words):
        lyric_track.append(mido.MetaMessage("text", text=word, time=0 if index == 0 else gap))
    music = mido.MidiTrack()
    for _ in range(notes):
        music.append(mido.Message("note_on", note=60, velocity=100, time=0))
        music.append(mido.Message("note_off", note=60, velocity=0, time=gap))
    midi.tracks.extend([conductor, identification, lyric_track, music])
    return midi


def write(midi, path):
    midi.save(str(path))
    return path


def test_kar_sung_position_advances_through_syllables():
    title, singer, lyrics, sync = parse_kar_lyrics(kar_midi(["\\Can", "tar", " bem", "/Ou", "tra"]))
    timeline = LyricTimeline(lyrics, sync)

    assert (title, singer) == ("Minha canção", "Cantor")
    upper, lower = timeline.lines_at(0)
    assert (upper.text, lower.text) == ("Cantar bem", "Outra")
    assert upper.sung_chars == 0

    upper, _ = timeline.lines_at(TPB)  # Start of "tar": "Can" is complete.
    assert upper.sung_chars == 3
    upper, _ = timeline.lines_at(TPB + TPB // 2)  # Halfway through "tar".
    assert upper.sung_chars == pytest.approx(4.5)
    upper, lower = timeline.lines_at(3 * TPB)
    assert upper.sung_chars == len("Cantar bem")
    assert lower.sung_chars == 0


def test_kar_replaces_each_finished_line_while_the_next_is_sung():
    words = ["\\um", "/dois", "/tres", "/quatro"]
    _, _, lyrics, sync = parse_kar_lyrics(kar_midi(words))
    timeline = LyricTimeline(lyrics, sync)

    assert [line.text for line in timeline.lines_at(0)] == ["um", "dois"]
    # While "dois" is sung, "um" has been replaced by the next line.
    assert [line.text for line in timeline.lines_at(TPB)] == ["tres", "dois"]
    assert [line.text for line in timeline.lines_at(2 * TPB)] == ["tres", "quatro"]


def test_kar_lyric_events_break_on_carriage_return():
    midi = mido.MidiFile(type=1, ticks_per_beat=TPB, charset="latin1")
    midi.tracks.append(
        mido.MidiTrack(
            [
                mido.MetaMessage("lyrics", text="um ", time=0),
                mido.MetaMessage("lyrics", text="dois\r", time=TPB),
                mido.MetaMessage("lyrics", text="tres", time=TPB),
            ]
        )
    )
    _, _, lyrics, sync = parse_kar_lyrics(midi)
    assert [line.text for line in LyricTimeline(lyrics, sync).lines_at(0)] == ["um dois", "tres"]


def test_kar_long_line_wraps_at_a_word_boundary():
    words = ["\\" + "palavra"] + [" palavra"] * 9
    _, _, lyrics, sync = parse_kar_lyrics(kar_midi(words))
    upper, lower = LyricTimeline(lyrics, sync).lines_at(0)
    assert 0 < len(upper.text) <= 42
    assert lower.text.startswith("palavra")


def test_st3_style_line_and_syllable_events():
    timeline = LyricTimeline(
        "Primeira linhaSegunda",
        [
            SyncEvent(0, 0, 14, 0, SYNC_LINE_UPPER),
            SyncEvent(0, 14, 7, 0, SYNC_LINE_LOWER),
            SyncEvent(10, 0, 8, 20, SYNC_SYLLABLE),
            SyncEvent(40, 14, 7, 10, SYNC_SYLLABLE),
        ],
    )
    upper, lower = timeline.lines_at(20)
    assert (upper.text, lower.text) == ("Primeira linha", "Segunda")
    assert upper.sung_chars == 4
    assert lower.sung_chars == 0
    upper, lower = timeline.lines_at(45)
    assert upper.sung_chars == 14
    assert lower.sung_chars == pytest.approx(3.5)


def test_load_song_rejects_unknown_and_noteless_files(tmp_path):
    with pytest.raises(SongError):
        load_song(tmp_path / "a.pdf")
    with pytest.raises(SongError):
        load_song(write(kar_midi([], notes=0), tmp_path / "vazio.kar"))


def test_load_song_drops_a_runaway_corrupt_track(tmp_path):
    midi = kar_midi(["\\la", "la"])
    second = mido.MidiTrack()
    for _ in range(8):
        second.append(mido.Message("note_on", note=64, velocity=90, time=0))
        second.append(mido.Message("note_off", note=64, velocity=0, time=TPB))
    midi.tracks.append(second)
    corrupt = mido.MidiTrack([mido.Message("note_on", note=127, velocity=1, time=500_000)])
    midi.tracks.append(corrupt)

    song = load_song(write(midi, tmp_path / "ruim.kar"))

    assert song.total_ticks == 8 * TPB


def test_song_seconds_follow_tempo_changes(tmp_path):
    midi = kar_midi(["\\la", "la"])
    midi.tracks[0].append(mido.MetaMessage("set_tempo", tempo=250000, time=4 * TPB))
    song = load_song(write(midi, tmp_path / "tempo.kar"))
    assert song.seconds_at(4 * TPB) == pytest.approx(2.0)
    assert song.seconds_at(6 * TPB) == pytest.approx(2.5)


soundfont = find_soundfont()
needs_soundfont = pytest.mark.skipif(soundfont is None, reason="sem SoundFont no sistema")


def peak(pcm: bytes) -> int:
    samples = array.array("h")
    samples.frombytes(pcm)
    return max((abs(sample) for sample in samples), default=0)


def advance(synth, seconds, block=512):
    output = bytearray()
    for _ in range(int(seconds * FRAMES_PER_SECOND / block)):
        output += synth.render_s16(block)
    return bytes(output)


def midi_bytes(midi):
    buffer = io.BytesIO()
    midi.save(file=buffer)
    return buffer.getvalue()


@needs_soundfont
def test_synth_makes_sound_and_tick_follows_rendered_time():
    synth = Synth(soundfont, realtime=False)
    try:
        synth.load(midi_bytes(kar_midi(["\\la", "la"], notes=16)))
        synth.play()
        audio = advance(synth, 2.0)
        assert peak(audio) > 500
        # At 120 BPM, 2 seconds are 4 beats.
        assert synth.tick == pytest.approx(4 * TPB, abs=TPB / 4)
    finally:
        synth.close()


@needs_soundfont
def test_synth_pause_silences_and_holds_position_then_resumes():
    synth = Synth(soundfont, realtime=False)
    try:
        synth.load(midi_bytes(kar_midi(["\\la", "la"], notes=16)))
        synth.play()
        advance(synth, 1.0)
        synth.pause()
        advance(synth, 0.1)
        held = synth.tick
        audio = advance(synth, 1.0)
        # Notes stop at once; only a faint reverb tail may remain.
        assert peak(audio) < 200
        assert synth.tick == held
        assert not synth.finished

        synth.play()
        advance(synth, 1.0)
        assert synth.tick == pytest.approx(held + 2 * TPB, abs=TPB / 2)
    finally:
        synth.close()


@needs_soundfont
def test_synth_seek_moves_the_clock():
    synth = Synth(soundfont, realtime=False)
    try:
        synth.load(midi_bytes(kar_midi(["\\la", "la"], notes=32)))
        synth.play()
        advance(synth, 0.5)
        synth.seek(16 * TPB)
        advance(synth, 0.5)
        assert synth.tick == pytest.approx(17 * TPB, abs=TPB / 2)
    finally:
        synth.close()


@needs_soundfont
def test_synth_reports_natural_end():
    synth = Synth(soundfont, realtime=False)
    try:
        synth.load(midi_bytes(kar_midi(["\\la", "la"], notes=2)))
        synth.play()
        advance(synth, 1.5)
        assert not synth.finished
        # FluidSynth lets the sound ring out for a few seconds after the last note.
        advance(synth, 4.0)
        assert synth.finished

        synth.play()
        advance(synth, 0.5)
        assert synth.tick == pytest.approx(TPB, abs=TPB / 2)
    finally:
        synth.close()


@needs_soundfont
def test_synth_tempo_factor_scales_the_clock():
    synth = Synth(soundfont, realtime=False)
    try:
        synth.load(midi_bytes(kar_midi(["\\la", "la"], notes=64)))
        synth.play()
        synth.set_tempo_factor(0.5)
        advance(synth, 2.0)
        # 2 seconds at 120 BPM are 4 beats; at half speed, 2 beats.
        assert synth.tick == pytest.approx(2 * TPB, abs=TPB / 4)
    finally:
        synth.close()


@needs_soundfont
def test_window_tempo_control_updates_clock_and_resets_per_song(tmp_path, qapp):
    from player import PlayerWindow

    path = write(kar_midi(["\\Can", "tar"], notes=8), tmp_path / "x.kar")
    window = PlayerWindow(soundfont, realtime=False)
    try:
        window.open_song(str(path))
        assert window._time.text() == "0:00 / 0:04"
        for _ in range(10):
            window.change_tempo(-0.05)
        assert window._tempo_label.text() == "50%"
        assert window._time.text() == "0:00 / 0:08"
        for _ in range(20):
            window.change_tempo(-0.05)
        assert window._tempo_label.text() == "50%"
        window.open_song(str(path))
        assert window._tempo_label.text() == "100%"
    finally:
        window.close()


@needs_soundfont
def test_synth_seek_while_paused_applies_on_resume():
    synth = Synth(soundfont, realtime=False)
    try:
        synth.load(midi_bytes(kar_midi(["\\la", "la"], notes=64)))
        synth.play()
        advance(synth, 1.0)
        synth.pause()
        advance(synth, 0.1)
        synth.seek(10 * TPB)
        synth.play()
        advance(synth, 0.5)
        assert synth.tick == pytest.approx(11 * TPB, abs=TPB / 2)
    finally:
        synth.close()


def test_tick_at_seconds_inverts_seconds_at(tmp_path):
    midi = kar_midi(["\\la", "la"])
    midi.tracks[0].append(mido.MetaMessage("set_tempo", tempo=250000, time=4 * TPB))
    song = load_song(write(midi, tmp_path / "tempo.kar"))
    assert song.tick_at_seconds(1.0) == 2 * TPB
    assert song.tick_at_seconds(2.5) == 6 * TPB
    assert song.tick_at_seconds(9999) == song.total_ticks
    assert song.tick_at_seconds(-1) == 0


@needs_soundfont
def test_window_plays_and_shows_lyrics(tmp_path, qapp):
    from player import PlayerWindow

    path = write(kar_midi(["\\Can", "tar", "/bem"], notes=8), tmp_path / "x.kar")
    window = PlayerWindow(soundfont, realtime=False)
    try:
        assert window.open_song(str(path))
        assert window._title.text() == "Minha canção — Cantor"
        assert window._synth.is_playing
        advance(window._synth, 0.6)
        window._refresh()
        assert window._lyrics._upper.text == "Cantar"
        assert window._lyrics._upper.sung_chars > 3
        window.toggle_play()
        assert not window._synth.is_playing
        window.stop()
        assert window._slider.value() == 0
    finally:
        window.close()


@needs_soundfont
def test_window_reports_bad_file_without_crashing(tmp_path, qapp, monkeypatch):
    from player import PlayerWindow

    messages = []
    window = PlayerWindow(soundfont, realtime=False)
    monkeypatch.setattr(window, "_show_error", messages.append)
    bad = tmp_path / "ruim.kar"
    bad.write_bytes(b"isto nao e midi")
    try:
        assert not window.open_song(str(bad))
        assert messages
    finally:
        window.close()


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


def song_folder(tmp_path, names):
    folder = tmp_path / "musicas"
    folder.mkdir(exist_ok=True)
    for name in names:
        write(kar_midi([f"\\{Path(name).stem}", "la"], notes=4), folder / name)
    return folder


def test_catalog_numbers_songs_by_name_including_subfolders(tmp_path):
    folder = song_folder(tmp_path, ["b.kar", "A.KAR"])
    (folder / "sub").mkdir()
    write(kar_midi(["\\c", "la"]), folder / "sub" / "c.kar")
    (folder / "nota.txt").write_text("x")

    entries = scan_folder(folder)

    assert [(entry.number, entry.name) for entry in entries] == [(1, "A"), (2, "b"), (3, "c")]
    assert entries[0].label == "0001  A — Cantor"


def scan_folder(folder):
    from library import Library

    library = Library(folder.parent / "teste.sqlite")
    try:
        return library.sync(folder)
    finally:
        library.close()


@pytest.fixture(autouse=True)
def no_waiting_for_a_click(monkeypatch):
    """An unexpected error dialog would wait forever for a click: make it fail the test."""
    from PySide6.QtWidgets import QFileDialog, QMessageBox

    def unexpected_dialog(_parent, _title, message, *_args):
        raise AssertionError(f"Janela de erro inesperada: {message}")

    monkeypatch.setattr(QMessageBox, "warning", unexpected_dialog)
    monkeypatch.setattr(QMessageBox, "question", unexpected_dialog)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", unexpected_dialog)


@pytest.fixture(autouse=True)
def clean_config():
    """Each test starts with no saved settings, song numbers or PDF."""
    from player import open_settings

    # Qt shares settings in memory between instances, so deleting the file is not enough.
    settings = open_settings()
    settings.clear()
    settings.sync()
    shutil.rmtree(Path(os.environ["XDG_CONFIG_HOME"]) / "freekaraoke", ignore_errors=True)


@pytest.fixture
def window(qapp):
    from player import PlayerWindow

    instance = PlayerWindow(soundfont, realtime=False)
    yield instance
    instance.close()
    from player import open_settings

    open_settings().clear()



@pytest.mark.parametrize("typed", ["99", "0", "abc"])
@needs_soundfont
def test_invalid_number_is_rejected_and_kept_for_correction(tmp_path, window, typed):
    window.set_song_folder(str(song_folder(tmp_path, ["a.kar"])))
    window._number_input.setText(typed)
    window.queue_typed_number()
    assert window._song is None
    assert window._number_input.text() == typed
    assert typed in window.statusBar().currentMessage()



@needs_soundfont
def test_settings_restore_folder_and_font(tmp_path, qapp):
    from PySide6.QtGui import QFont
    from player import DEFAULT_FONT_SIZE, PlayerWindow, open_settings

    first = PlayerWindow(soundfont, realtime=False)
    first.set_song_folder(str(song_folder(tmp_path, ["a.kar"])))
    first.set_lyric_font(QFont("Liberation Sans", 55))
    first.close()

    second = PlayerWindow(soundfont, realtime=False)
    try:
        assert second._catalog_list.count() == 1
        assert second._lyrics.lyric_font().pointSize() == 55
        second.reset_font()
        assert second._lyrics.lyric_font().pointSize() == DEFAULT_FONT_SIZE
    finally:
        second.close()
        open_settings().clear()


def test_bundled_arial_compatible_font_loads(qapp):
    from PySide6.QtGui import QFontDatabase
    from player import DEFAULT_FONT_FAMILY, load_bundled_fonts

    load_bundled_fonts()
    assert DEFAULT_FONT_FAMILY in QFontDatabase.families()


def test_lyric_lines_sit_together_at_the_bottom(qapp):
    from PySide6.QtGui import QFontMetricsF
    from player import LyricWidget

    widget = LyricWidget()
    widget.resize(800, 600)
    upper, lower = widget.line_baselines()
    assert lower > 500
    # Next to each other: the gap is about one line, however large the font is.
    assert lower - upper < 1.3 * QFontMetricsF(widget.lyric_font()).height()


def wait_until(qapp, condition, seconds=10):
    import time

    end = time.monotonic() + seconds
    while time.monotonic() < end:
        qapp.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    return False


def test_background_rotates_images_and_skips_bad_files(tmp_path, qapp):
    from PySide6.QtGui import QColor, QImage
    from background import BackgroundRotator

    colours = {"a.png": "red", "b.png": "blue"}
    for name, colour in colours.items():
        image = QImage(16, 16, QImage.Format.Format_RGB32)
        image.fill(QColor(colour))
        image.save(str(tmp_path / name))
    (tmp_path / "aa.jpg").write_bytes(b"nao e imagem")
    (tmp_path / "nota.txt").write_text("x")

    rotator = BackgroundRotator()
    seen = []
    rotator.frame_ready.connect(lambda image: seen.append(image.pixelColor(0, 0).name()))
    assert rotator.set_folder(tmp_path) == 3
    rotator.start()
    assert wait_until(qapp, lambda: seen)
    assert seen[0] == QColor("red").name()

    rotator._timer.setInterval(1)
    rotator._timer.start(1)
    assert wait_until(qapp, lambda: QColor("blue").name() in seen)
    rotator.stop()


def test_background_gives_up_when_nothing_is_readable(tmp_path, qapp):
    from background import BackgroundRotator

    (tmp_path / "a.jpg").write_bytes(b"x")
    rotator = BackgroundRotator()
    rotator.set_folder(tmp_path)
    rotator.start()
    assert wait_until(qapp, lambda: not rotator._running)


ffmpeg = shutil.which("ffmpeg")


@pytest.mark.skipif(ffmpeg is None, reason="sem ffmpeg para criar o vídeo de teste")
def test_background_plays_video_frames_without_audio_output(tmp_path, qapp):
    from background import BackgroundRotator

    video = tmp_path / "v.mp4"
    subprocess.run(
        [ffmpeg, "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=64x64:rate=10:duration=1",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-shortest", str(video)],
        check=True,
    )
    rotator = BackgroundRotator()
    frames = []
    rotator.frame_ready.connect(frames.append)
    rotator.set_folder(tmp_path)
    rotator.start()

    assert wait_until(qapp, lambda: len(frames) >= 3)
    assert rotator._player.audioOutput() is None
    rotator.stop()


@needs_soundfont
def test_fullscreen_shows_only_the_stage_and_esc_restores(window):
    window.show()
    window.leave_fullscreen()  # Esc outside full screen does nothing.
    assert not window.isFullScreen()

    window.toggle_fullscreen()
    assert window.isFullScreen()
    assert window._lyrics.isVisible()
    for hidden in (window._controls, window._title, window.menuBar()):
        assert not hidden.isVisible()

    window.leave_fullscreen()
    assert not window.isFullScreen()
    for shown in (window._controls, window._title, window.menuBar()):
        assert shown.isVisible()

    window.toggle_fullscreen()
    window.toggle_fullscreen()
    assert not window.isFullScreen()


@needs_soundfont
def test_song_window_opens_from_the_button_and_stays_open(tmp_path, window):
    window.show()
    assert not window._song_window.isVisible()
    window._songs_button.click()
    assert window._song_window.isVisible()
    assert window._song_window.isModal() is False
    window._song_window.close()
    window.show_song_window()
    assert window._song_window.isVisible()



@needs_soundfont
def test_invalid_number_message_appears_in_the_song_window(tmp_path, window):
    window.set_song_folder(str(song_folder(tmp_path, ["a.kar"])))
    window._number_input.setText("99")
    window.queue_typed_number()
    assert "99" in window._song_message.text()


@needs_soundfont
def test_font_size_shortcuts_change_and_save_within_limits(window):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from player import DEFAULT_FONT_SIZE, FONT_SIZE_STEP, MAX_FONT_SIZE, MIN_FONT_SIZE, open_settings

    window.show()
    qapp_process = QApplication_instance()
    window.activateWindow()
    qapp_process.processEvents()
    QTest.keyClick(window, Qt.Key.Key_Plus, Qt.KeyboardModifier.ControlModifier)
    assert window._lyrics.lyric_font().pointSize() == DEFAULT_FONT_SIZE + FONT_SIZE_STEP
    QTest.keyClick(window, Qt.Key.Key_Equal, Qt.KeyboardModifier.ControlModifier)
    assert window._lyrics.lyric_font().pointSize() == DEFAULT_FONT_SIZE + 2 * FONT_SIZE_STEP
    QTest.keyClick(window, Qt.Key.Key_Minus, Qt.KeyboardModifier.ControlModifier)
    assert window._lyrics.lyric_font().pointSize() == DEFAULT_FONT_SIZE + FONT_SIZE_STEP
    assert open_settings().value("lyric_font")

    window.change_font_size(10_000)
    assert window._lyrics.lyric_font().pointSize() == MAX_FONT_SIZE
    window.change_font_size(-10_000)
    assert window._lyrics.lyric_font().pointSize() == MIN_FONT_SIZE


def QApplication_instance():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance()


def test_settings_are_an_ini_file_in_the_freekaraoke_folder(qapp):
    from player import open_settings

    path = Path(open_settings().fileName())
    assert path == Path(os.environ["XDG_CONFIG_HOME"]) / "freekaraoke" / "freekaraoke.ini"



def kar_by(tmp_path, name, *titles):
    folder = tmp_path / "musicas"
    folder.mkdir(exist_ok=True)
    midi = kar_midi(["\\la", "la"], notes=2)
    words = midi.tracks[2]
    kept = [m for m in words if not (m.type == "text" and m.text.startswith("@T"))]
    words[:] = [mido.MetaMessage("text", text=f"@T{t}", time=0) for t in titles] + kept
    write(midi, folder / name)
    return folder


@pytest.mark.parametrize(
    "titles, expected",
    [
        (["Musica", "ABBA"], "ABBA"),
        (["Musica", "By Nirvana"], "Nirvana"),
        (["Musica", "Words & Music by Michael Jackson"], "Michael Jackson"),
        (["Musica", "Karaoke by Fulano", "Queen"], "Queen"),
        (["Musica", "Karaoke do Brasil - Familia Ribeiro - 1999"], ""),
        (["Musica", "Lyrics entered by Fulano"], ""),
        (["Musica", "http://www.geocities.com/karaoke.html", "Skank"], "Skank"),
        (["Musica", "fulano@provedor.com.br"], ""),
        (["Musica", "Brought to you by Fulano"], ""),
        (["Musica", "Recorded by Bryan Adams"], "Bryan Adams"),
        (["Musica", "The Beatles\x0b\x7f"], "The Beatles"),
        (["Musica"], ""),
        ([], ""),
    ],
)
def test_catalog_reads_the_singer_and_skips_credits(tmp_path, titles, expected):
    folder = kar_by(tmp_path, "x.kar", *titles)
    assert scan_folder(folder)[0].singer == expected


def test_catalog_reads_singer_from_st3_header_with_accents(tmp_path):
    from catalog import read_singer

    data = bytearray(b"STAR DATA V3.00\x1a" + bytes(0x200))
    name = "Legião Urbana".encode("cp1252")
    data[0x84 : 0x84 + len(name)] = name
    path = tmp_path / "x.st3"
    path.write_bytes(bytes(data))
    assert read_singer(path) == "Legião Urbana"


def test_catalog_read_singer_tolerates_short_and_missing_files(tmp_path):
    from catalog import read_singer

    short = tmp_path / "x.kar"
    short.write_bytes(b"\xff\x01\x05@Tab")
    assert read_singer(short) == ""
    assert read_singer(tmp_path / "falta.kar") == ""


def test_singer_spellings_are_merged_under_the_most_common():
    from catalog import CatalogEntry, singer_key, singer_names

    entries = [
        CatalogEntry(i, Path(f"{i}.kar"), singer)
        for i, singer in enumerate(
            ["The Beatles", "The Beatles", "Beatles", "Legião Urbana", "Legiao Urbana", "LEGIÃO URBANA", "The The", ""],
            start=1,
        )
    ]
    assert singer_key("The Beatles") == singer_key("beatles")
    assert singer_key("Legião  Urbana!") == singer_key("legiao urbana")
    names = singer_names(entries)
    assert "The Beatles" in names and "Beatles" not in names
    assert sum("rbana" in name for name in names) == 1
    assert "The The" in names
    assert "" not in names


def visible_names(window):
    return [e.name for row, e in enumerate(window._catalog) if not window._catalog_list.isRowHidden(row)]


@needs_soundfont
def test_singer_filter_in_the_song_window(tmp_path, window):
    kar_by(tmp_path, "um.kar", "M", "The Beatles")
    kar_by(tmp_path, "dois.kar", "M", "Beatles")
    kar_by(tmp_path, "tres.kar", "M", "Queen")
    folder = kar_by(tmp_path, "quatro.kar", "M")
    window.set_song_folder(str(folder))
    combo = window._singer_filter

    assert combo.currentText() == "Todos os cantores"
    assert len(visible_names(window)) == 4

    combo.setCurrentText("Queen")
    assert visible_names(window) == ["tres"]
    assert window._catalog_label.text() == "Músicas: 1 de 4"

    combo.setCurrentText("beat")
    assert visible_names(window) == ["dois", "um"]

    window._search_input.setText("um")
    assert visible_names(window) == ["um"]
    window._search_input.clear()

    combo.setCurrentText("Others")
    assert visible_names(window) == ["quatro"]

    combo.setCurrentText("")
    assert len(visible_names(window)) == 4
    assert window._catalog_label.text() == "Músicas: 4"



def test_label_shows_singer_when_known():
    from catalog import CatalogEntry

    assert CatalogEntry(7, Path("a.kar"), "ABBA").label == "0007  a — ABBA"
    assert CatalogEntry(7, Path("a.kar")).label == "0007  a — Others"


def queue_names(window):
    return [entry.name for entry in window._queue]


def queue_labels(window):
    return [window._queue_list.item(row).text() for row in range(window._queue_list.count())]


@needs_soundfont
def test_adding_to_the_queue_does_not_start_playing(tmp_path, window):
    window.set_song_folder(str(song_folder(tmp_path, ["a.kar", "b.kar", "c.kar"])))
    assert not window._play_button.isEnabled()

    window._number_input.setText("2")
    window.queue_typed_number()
    window._catalog_list.setCurrentRow(0)
    window.queue_selected()

    assert window._song is None
    assert not window._active
    assert window._number_input.text() == ""
    assert queue_labels(window) == ["0002  b — Cantor", "0001  a — Cantor"]
    assert window._play_button.isEnabled()


@needs_soundfont
@pytest.mark.parametrize("start", ["toggle_play", "play_queue"])
def test_play_starts_the_first_song_of_the_queue(tmp_path, window, start):
    window.set_song_folder(str(song_folder(tmp_path, ["a.kar", "b.kar"])))
    window.add_to_queue(window._catalog[1])
    window.add_to_queue(window._catalog[0])

    getattr(window, start)()

    assert window._song.path.stem == "b"
    assert window._active and window._synth.is_playing
    assert queue_names(window) == ["a"]


@needs_soundfont
def test_play_queue_with_nothing_queued_only_informs(tmp_path, window):
    window.set_song_folder(str(song_folder(tmp_path, ["a.kar"])))
    window.play_queue()
    assert not window._active
    assert "vazia" in window._song_message.text()


@needs_soundfont
def test_play_queue_resumes_a_paused_song_instead_of_skipping(tmp_path, window):
    window.set_song_folder(str(song_folder(tmp_path, ["a.kar", "b.kar"])))
    window.add_to_queue(window._catalog[0])
    window.add_to_queue(window._catalog[1])
    window.play_queue()
    window.toggle_play()
    assert not window._synth.is_playing

    window.play_queue()
    assert window._song.path.stem == "a"
    assert window._synth.is_playing
    assert queue_names(window) == ["b"]


@needs_soundfont
def test_finished_song_plays_next_and_stop_keeps_queue(tmp_path, window):
    window.set_song_folder(str(song_folder(tmp_path, ["a.kar", "b.kar", "c.kar"])))
    for entry in window._catalog:
        window.add_to_queue(entry)
    window.toggle_play()
    assert window._song.path.stem == "a"

    advance(window._synth, 8.0)
    window._refresh()
    assert window._song.path.stem == "b"
    assert queue_names(window) == ["c"]

    window.stop()
    assert not window._active
    assert queue_names(window) == ["c"]
    window.toggle_play()
    assert window._song.path.stem == "c"
    assert queue_names(window) == []


@needs_soundfont
def test_unreadable_song_in_queue_is_skipped(tmp_path, window, monkeypatch):
    folder = song_folder(tmp_path, ["b.kar"])
    (folder / "a.kar").write_bytes(b"isto nao e midi")
    errors = []
    monkeypatch.setattr(window, "_show_error", errors.append)
    window.set_song_folder(str(folder))
    window.add_to_queue(window._catalog[0])
    window.add_to_queue(window._catalog[1])

    window.play_queue()

    assert window._song.path.stem == "b"
    assert errors == []


@needs_soundfont
def test_remove_from_queue_and_search_filter(tmp_path, window):
    window.set_song_folder(str(song_folder(tmp_path, ["amor.kar", "bola.kar", "casa.kar"])))
    window._search_input.setText("OL")
    hidden = [window._catalog_list.isRowHidden(row) for row in range(3)]
    assert hidden == [True, False, True]

    for entry in window._catalog:
        window.add_to_queue(entry)
    window._queue_list.setCurrentRow(0)
    window.remove_selected_from_queue()
    assert queue_names(window) == ["bola", "casa"]


@needs_soundfont
def test_enter_in_number_field_adds_only_once(tmp_path, window):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    window.set_song_folder(str(song_folder(tmp_path, ["a.kar", "b.kar"])))
    window.show_song_window()
    window._number_input.setText("1")
    QTest.keyClick(window._number_input, Qt.Key.Key_Return)
    window._number_input.setText("2")
    QTest.keyClick(window._number_input, Qt.Key.Key_Return)
    assert queue_names(window) == ["a", "b"]
    assert window._song_window.isVisible()


@needs_soundfont
def test_delete_key_removes_selected_queue_items_one_after_another(tmp_path, window, qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    window.set_song_folder(str(song_folder(tmp_path, ["a.kar", "b.kar", "c.kar", "d.kar"])))
    for entry in window._catalog:
        window.add_to_queue(entry)

    window.show_song_window()
    window._song_window.activateWindow()
    window._queue_list.setFocus()
    window._queue_list.setCurrentRow(0)
    qapp.processEvents()
    QTest.keyClick(window._queue_list, Qt.Key.Key_Delete)
    assert queue_names(window) == ["b", "c", "d"]
    QTest.keyClick(window._queue_list, Qt.Key.Key_Delete)
    assert queue_names(window) == ["c", "d"]
    assert window._queue_list.count() == 2


@needs_soundfont
def test_song_stops_at_the_end_when_the_queue_is_empty(tmp_path, window):
    window.set_song_folder(str(song_folder(tmp_path, ["a.kar"])))
    window.add_to_queue(window._catalog[0])
    window.play_queue()
    advance(window._synth, 8.0)
    window._refresh()
    assert not window._active
    assert not window._synth.is_playing

    audio = advance(window._synth, 2.0)
    assert peak(audio) < 200
    assert not window._synth.is_playing


@needs_soundfont
def test_repeated_add_does_not_queue_a_song_twice(tmp_path, window):
    window.set_song_folder(str(song_folder(tmp_path, ["a.kar", "b.kar"])))
    window.add_to_queue(window._catalog[0])
    window.add_to_queue(window._catalog[0])
    assert queue_names(window) == ["a"]
    assert "Já está na fila" in window._song_message.text()

    window.play_queue()
    window.add_to_queue(window._catalog[0])
    assert queue_names(window) == []
    assert "Já está tocando" in window._song_message.text()


@needs_soundfont
def test_finished_song_can_be_queued_again(tmp_path, window):
    window.set_song_folder(str(song_folder(tmp_path, ["a.kar"])))
    window.add_to_queue(window._catalog[0])
    window.play_queue()
    advance(window._synth, 8.0)
    window._refresh()
    assert not window._active

    window.add_to_queue(window._catalog[0])
    assert queue_names(window) == ["a"]
    assert not window._active


@needs_soundfont
def test_queue_button_ignores_a_selection_hidden_by_the_filter(tmp_path, window):
    kar_by(tmp_path, "um.kar", "M", "ABBA")
    folder = kar_by(tmp_path, "dois.kar", "M", "Queen")
    window.set_song_folder(str(folder))
    window._catalog_list.setCurrentRow(window._catalog.index(next(e for e in window._catalog if e.name == "um")))
    window._singer_filter.setCurrentText("Queen")
    window.queue_selected()
    assert queue_names(window) == []


def row_point(queue, row, *, lower):
    rect = queue.visualItemRect(queue.item(row))
    return rect.bottomLeft() - QPoint(-1, 1) if lower else rect.topLeft() + QPoint(1, 1)


@pytest.fixture
def filled_queue(tmp_path, window):
    window.set_song_folder(str(song_folder(tmp_path, ["a.kar", "b.kar", "c.kar", "d.kar", "e.kar"])))
    for entry in window._catalog[:4]:
        window.add_to_queue(entry)
    window.resize(1000, 650)
    window.show_song_window()
    return window


@needs_soundfont
@pytest.mark.parametrize(
    "source_row, target_row, lower, expected",
    [
        (3, 0, False, ["d", "a", "b", "c"]),  # up, to the very top
        (0, 3, True, ["b", "c", "d", "a"]),  # down, to the very end
        (0, 2, False, ["b", "a", "c", "d"]),  # down, above a row
        (2, 1, True, ["a", "b", "c", "d"]),  # onto the same place
        (1, 1, False, ["a", "b", "c", "d"]),  # onto itself
    ],
)
def test_dragging_inside_the_queue_reorders_it(filled_queue, source_row, target_row, lower, expected):
    queue = filled_queue._queue_list
    queue.setCurrentRow(source_row)
    queue.handle_drop(queue, row_point(queue, target_row, lower=lower))
    assert queue_names(filled_queue) == expected
    assert [label.split()[1] for label in queue_labels(filled_queue)] == expected


@needs_soundfont
def test_dragging_to_empty_area_moves_to_the_end(filled_queue):
    queue = filled_queue._queue_list
    queue.setCurrentRow(0)
    queue.handle_drop(queue, QPoint(5, queue.viewport().height() - 2))
    assert queue_names(filled_queue) == ["b", "c", "d", "a"]


@needs_soundfont
def test_dragging_from_the_catalog_inserts_at_the_drop_position(filled_queue):
    queue = filled_queue._queue_list
    filled_queue._catalog_list.setCurrentRow(4)
    queue.handle_drop(filled_queue._catalog_list, row_point(queue, 1, lower=False))
    assert queue_names(filled_queue) == ["a", "e", "b", "c", "d"]
    assert not filled_queue._active


@needs_soundfont
def test_dragging_a_queued_song_from_the_catalog_does_not_duplicate_it(filled_queue):
    queue = filled_queue._queue_list
    filled_queue._catalog_list.setCurrentRow(0)
    queue.handle_drop(filled_queue._catalog_list, row_point(queue, 2, lower=False))
    assert queue_names(filled_queue) == ["a", "b", "c", "d"]


@needs_soundfont
def test_reordered_queue_plays_in_the_new_order(filled_queue):
    queue = filled_queue._queue_list
    queue.setCurrentRow(3)
    queue.handle_drop(queue, row_point(queue, 0, lower=False))
    filled_queue.play_queue()
    assert filled_queue._song.path.stem == "d"


def press(window, *keys):
    from PySide6.QtTest import QTest

    for key in keys:
        QTest.keyClick(window, key)


@needs_soundfont
def test_typing_a_number_on_the_stage_queues_it_on_enter(tmp_path, window):
    from PySide6.QtCore import Qt

    window.set_song_folder(str(song_folder(tmp_path, ["a.kar", "b.kar", "c.kar"])))
    window.show()
    press(window, Qt.Key.Key_0, Qt.Key.Key_2)
    assert window._lyrics.notice() == "Música nº 02"
    assert queue_names(window) == []

    press(window, Qt.Key.Key_Return)
    assert queue_names(window) == ["b"]
    assert not window._active
    assert "Na fila: b" in window._lyrics.notice()

    press(window, Qt.Key.Key_3, Qt.Key.Key_Enter)
    assert queue_names(window) == ["b", "c"]


@needs_soundfont
def test_typed_number_works_in_fullscreen_and_while_playing(tmp_path, window):
    from PySide6.QtCore import Qt

    window.set_song_folder(str(song_folder(tmp_path, ["a.kar", "b.kar"])))
    window.show()
    window.add_to_queue(window._catalog[0])
    window.play_queue()
    window.toggle_fullscreen()
    press(window, Qt.Key.Key_2, Qt.Key.Key_Return)
    assert queue_names(window) == ["b"]
    assert window._synth.is_playing
    assert window.isFullScreen()


@needs_soundfont
def test_typed_number_can_be_corrected_cancelled_or_rejected(tmp_path, window):
    from PySide6.QtCore import Qt

    window.set_song_folder(str(song_folder(tmp_path, ["a.kar", "b.kar"])))
    window.show()

    press(window, Qt.Key.Key_1, Qt.Key.Key_5, Qt.Key.Key_Backspace)
    assert window._lyrics.notice() == "Música nº 1"
    press(window, Qt.Key.Key_Backspace)
    assert window._lyrics.notice() == ""

    press(window, Qt.Key.Key_9, Qt.Key.Key_9, Qt.Key.Key_Return)
    assert "99" in window._lyrics.notice()
    assert queue_names(window) == []
    press(window, Qt.Key.Key_1, Qt.Key.Key_Return)
    assert queue_names(window) == ["a"]

    window.toggle_fullscreen()
    press(window, Qt.Key.Key_2)
    window.leave_fullscreen()  # Esc first cancels what was being typed.
    assert window.isFullScreen()
    assert window._lyrics.notice() == ""
    press(window, Qt.Key.Key_Return)
    assert queue_names(window) == ["a"]
    window.leave_fullscreen()
    assert not window.isFullScreen()


@needs_soundfont
def test_typed_number_expires(tmp_path, window, qapp):
    from PySide6.QtCore import Qt

    window.set_song_folder(str(song_folder(tmp_path, ["a.kar"])))
    window.show()
    press(window, Qt.Key.Key_1)
    window._notice_timer.start(1)
    assert wait_until(qapp, lambda: not window._lyrics.notice())
    press(window, Qt.Key.Key_Return)
    assert queue_names(window) == []


def pdf_pages(path):
    from PySide6.QtPdf import QPdfDocument

    document = QPdfDocument()
    document.load(str(path))
    # QtPdf splits text into fragments, so compare it without any whitespace.
    return ["".join(document.getAllText(page).text().split()) for page in range(document.pageCount())]


def test_pdf_lists_every_song_across_pages_with_number_and_singer(tmp_path, qapp):
    from catalog import CatalogEntry
    from player import load_bundled_fonts
    from song_list_pdf import export_song_list

    load_bundled_fonts()
    entries = [
        CatalogEntry(number, Path(f"Canção número {number}.kar"), "Legião Urbana" if number % 2 else "")
        for number in range(1, 301)
    ]
    path = tmp_path / "lista.pdf"

    export_song_list(entries, path)

    assert path.read_bytes().startswith(b"%PDF")
    pages = pdf_pages(path)
    assert len(pages) >= 3
    text = "\n".join(pages)
    for number in (1, 150, 300):
        assert f"{number:04d}" in text
        assert f"Cançãonúmero{number}" in text
    assert "LegiãoUrbana" in text
    assert "Others" in text
    assert "300músicas" in pages[0]
    assert f"Página{len(pages)}de{len(pages)}" in pages[-1]
    assert all("Música" in page for page in pages)


def test_pdf_with_exact_page_of_rows_has_no_blank_extra_page(tmp_path, qapp):
    from catalog import CatalogEntry
    from song_list_pdf import export_song_list

    def count(n):
        path = tmp_path / f"{n}.pdf"
        export_song_list([CatalogEntry(i, Path(f"m{i}.kar")) for i in range(1, n + 1)], path)
        return pdf_pages(path)

    assert len(count(1)) == 1
    previous = 1
    for n in range(40, 140):
        pages = count(n)
        assert len(pages) in (previous, previous + 1)
        assert f"{n:04d}" in pages[-1]
        previous = len(pages)


def test_pdf_reports_a_path_it_cannot_write(tmp_path, qapp):
    from catalog import CatalogEntry
    from song_list_pdf import export_song_list

    with pytest.raises(OSError):
        export_song_list([CatalogEntry(1, Path("a.kar"))], tmp_path / "falta" / "x.pdf")


@needs_soundfont
def test_window_saves_a_copy_of_the_pdf_and_requires_a_folder(tmp_path, window):
    window.choose_pdf_file()
    assert "pasta" in window._song_message.text()

    window.set_song_folder(str(song_folder(tmp_path, ["a.kar", "b.kar"])))
    target = tmp_path / "lista.pdf"
    window.export_pdf(str(target))
    assert target.read_bytes().startswith(b"%PDF")
    assert "Lista salva" in window._song_message.text()


def library_for(tmp_path):
    from library import Library

    return Library(tmp_path / "config" / "numeros.sqlite")


def numbers(entries):
    return {entry.name: entry.number for entry in entries}


def touch_song(folder, name, content=b"x"):
    path = folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_first_sync_numbers_by_name_and_creates_the_database(tmp_path):
    folder = tmp_path / "m"
    for name in ("b.kar", "A.kar", "c.st3"):
        touch_song(folder, name)
    library = library_for(tmp_path)
    assert numbers(library.sync(folder)) == {"A": 1, "b": 2, "c": 3}
    assert (tmp_path / "config" / "numeros.sqlite").is_file()


def test_new_song_gets_next_number_without_changing_the_others(tmp_path):
    folder = tmp_path / "m"
    touch_song(folder, "b.kar")
    touch_song(folder, "c.kar")
    library = library_for(tmp_path)
    library.sync(folder)

    touch_song(folder, "a.kar", b"nova")
    entries = library.sync(folder)

    assert numbers(entries) == {"b": 1, "c": 2, "a": 3}
    assert [entry.name for entry in entries] == ["a", "b", "c"]  # The list stays alphabetical.


def test_removed_songs_number_is_not_reused_and_returns_with_the_file(tmp_path):
    folder = tmp_path / "m"
    a = touch_song(folder, "a.kar", b"1")
    touch_song(folder, "b.kar", b"22")
    library = library_for(tmp_path)
    library.sync(folder)

    a.unlink()
    assert numbers(library.sync(folder)) == {"b": 2}
    touch_song(folder, "c.kar", b"333")
    assert numbers(library.sync(folder)) == {"b": 2, "c": 3}

    touch_song(folder, "a.kar", b"1")
    assert numbers(library.sync(folder)) == {"a": 1, "b": 2, "c": 3}


def test_renamed_or_moved_file_keeps_its_number(tmp_path):
    folder = tmp_path / "m"
    touch_song(folder, "a.kar", b"1")
    b = touch_song(folder, "b.kar", b"22")
    library = library_for(tmp_path)
    library.sync(folder)

    (folder / "sub").mkdir()
    b.rename(folder / "sub" / "novo nome.kar")

    assert numbers(library.sync(folder)) == {"a": 1, "novo nome": 2}


def test_identical_stamps_are_not_guessed_as_renames(tmp_path):
    folder = tmp_path / "m"
    a = touch_song(folder, "a.kar", b"1")
    stamp = a.stat().st_mtime_ns
    b = touch_song(folder, "b.kar", b"1")
    os.utime(b, ns=(stamp, stamp))
    library = library_for(tmp_path)
    library.sync(folder)

    a.unlink()
    b.unlink()
    for name in ("c.kar", "d.kar"):
        os.utime(touch_song(folder, name, b"1"), ns=(stamp, stamp))

    assert numbers(library.sync(folder)) == {"c": 3, "d": 4}


def test_numbers_survive_reopening_and_other_folders_do_not_collide(tmp_path):
    first = tmp_path / "um"
    second = tmp_path / "dois"
    touch_song(first, "a.kar")
    touch_song(second, "z.kar")
    library = library_for(tmp_path)
    library.sync(first)
    library.close()

    library = library_for(tmp_path)
    assert numbers(library.sync(second)) == {"z": 2}
    # Syncing another folder must not mark the first one's songs as gone.
    assert numbers(library.sync(first)) == {"a": 1}


def test_changed_file_updates_singer_and_keeps_number(tmp_path):
    folder = tmp_path / "m"
    path = write(kar_midi(["\\la", "la"]), (folder.mkdir() or folder) / "a.kar")
    library = library_for(tmp_path)
    assert library.sync(folder)[0].singer == "Cantor"

    midi = kar_midi(["\\la", "la"])
    midi.tracks[2][2] = mido.MetaMessage("text", text="@TOutro", time=0)
    write(midi, path)
    entry = library.sync(folder)[0]
    assert (entry.number, entry.singer) == (1, "Outro")


def test_file_name_that_is_not_valid_utf8_is_kept(tmp_path):
    folder = tmp_path / "m"
    folder.mkdir()
    raw = os.fsencode(folder) + b"/can\xe7ao.kar"
    with open(raw, "wb") as file:
        file.write(b"x")
    library = library_for(tmp_path)
    entries = library.sync(folder)
    assert os.fsencode(entries[0].path) == raw
    assert entries[0].number == 1


def test_pdf_signature_changes_only_when_files_change(tmp_path):
    folder = tmp_path / "m"
    touch_song(folder, "a.kar")
    library = library_for(tmp_path)
    library.sync(folder)
    assert not library.pdf_is_current()
    library.mark_pdf_current()

    library.sync(folder)
    assert library.pdf_is_current()

    touch_song(folder, "b.kar", b"nova")
    library.sync(folder)
    assert not library.pdf_is_current()


@pytest.fixture
def pdf_exports(monkeypatch):
    import player

    calls = []
    real = player.export_song_list

    def counting(entries, path):
        calls.append(len(entries))
        real(entries, path)

    monkeypatch.setattr(player, "export_song_list", counting)
    return calls


@needs_soundfont
def test_pdf_is_generated_once_and_again_only_after_changes(tmp_path, window, pdf_exports):
    from player import PlayerWindow

    folder = song_folder(tmp_path, ["a.kar", "b.kar"])
    window.set_song_folder(str(folder))
    assert pdf_exports == [2]
    pdf = window.song_list_pdf_path()
    assert pdf == Path(os.environ["XDG_CONFIG_HOME"]) / "freekaraoke" / "lista-de-musicas.pdf"
    assert pdf.read_bytes().startswith(b"%PDF")
    assert sorted(path.name for path in folder.iterdir()) == ["a.kar", "b.kar"]  # Folder untouched.

    window.refresh_song_folder()
    assert pdf_exports == [2]

    write(kar_midi(["\\c", "la"]), folder / "c.kar")
    window.refresh_song_folder()
    assert pdf_exports == [2, 3]

    (folder / "a.kar").unlink()
    window.refresh_song_folder()
    assert pdf_exports == [2, 3, 2]


@needs_soundfont
def test_pdf_is_not_regenerated_on_restart_but_is_if_deleted(tmp_path, pdf_exports, qapp):
    from player import PlayerWindow

    folder = song_folder(tmp_path, ["a.kar"])
    first = PlayerWindow(soundfont, realtime=False)
    first.set_song_folder(str(folder))
    first.close()
    assert pdf_exports == [1]

    second = PlayerWindow(soundfont, realtime=False)  # Restores the folder by itself.
    try:
        assert second._catalog_list.count() == 1
        assert pdf_exports == [1]
        second.song_list_pdf_path().unlink()
        second.refresh_song_folder()
        assert pdf_exports == [1, 1]
    finally:
        second.close()


@needs_soundfont
def test_number_typed_finds_the_stable_number_not_the_list_position(tmp_path, window):
    folder = song_folder(tmp_path, ["b.kar", "c.kar"])
    window.set_song_folder(str(folder))
    write(kar_midi(["\\a", "la"]), folder / "a.kar")
    window.refresh_song_folder()
    assert [entry.name for entry in window._catalog] == ["a", "b", "c"]
    assert window._catalog_list.item(0).text() == "0003  a — Cantor"

    window._number_input.setText("3")
    window.queue_typed_number()
    assert queue_names(window) == ["a"]

    window._catalog_list.setCurrentRow(1)
    window.queue_selected()
    assert queue_names(window) == ["a", "b"]


@needs_soundfont
def test_open_pdf_uses_the_generated_file(tmp_path, window, monkeypatch):
    import player

    opened = []
    monkeypatch.setattr(player.QDesktopServices, "openUrl", lambda url: opened.append(url.toLocalFile()))
    window.open_song_list_pdf()
    assert opened == []
    assert "pasta" in window._song_message.text()

    window.set_song_folder(str(song_folder(tmp_path, ["a.kar"])))
    window.open_song_list_pdf()
    assert opened == [str(window.song_list_pdf_path())]


@needs_soundfont
def test_damaged_database_reports_an_error_without_touching_it(tmp_path, window, monkeypatch):
    from player import LIBRARY_FILE, config_directory

    messages = []
    monkeypatch.setattr(window, "_show_error", messages.append)
    config_directory().mkdir(parents=True, exist_ok=True)
    database = config_directory() / LIBRARY_FILE
    database.write_bytes(b"isto nao e um banco sqlite" * 100)
    damaged = database.read_bytes()

    window.set_song_folder(str(song_folder(tmp_path, ["a.kar"])))

    assert messages
    assert window._catalog == []
    assert database.read_bytes() == damaged



def sung_syllables(count=40, beat=TPB, duration=TPB // 2):
    return [SyncEvent(4 * beat + index * beat, 0, 1, duration, SYNC_SYLLABLE) for index in range(count)]


def simulate(voice, syllables=None, step=TPB // 10, end=None):
    """Feeds the scorer one frame per step; voice(tick) says if someone is singing then."""
    from score import RhythmScorer

    syllables = syllables if syllables is not None else sung_syllables()
    scorer = RhythmScorer(syllables, TPB)
    last = end if end is not None else syllables[-1].tick + 8 * TPB
    for tick in range(0, last, step):
        scorer.add_frame(tick, voice(tick))
    return scorer.result()


def during_syllables(syllables, offset=0):
    windows = [(s.tick + offset, s.tick + offset + s.duration) for s in syllables]
    return lambda tick: any(start <= tick < end for start, end in windows)


def test_singing_on_time_scores_high():
    syllables = sung_syllables()
    assert simulate(during_syllables(syllables), syllables) == 100


def test_silence_scores_zero():
    assert simulate(lambda tick: False) == 0


def test_singing_half_of_the_song_scores_lower_than_all_of_it():
    syllables = sung_syllables()
    half = during_syllables(syllables[:20])
    full = simulate(during_syllables(syllables), syllables)
    partial = simulate(half, syllables)
    assert 50 < partial < 75
    assert partial < full


def test_being_late_lowers_the_score_gradually():
    syllables = [
        SyncEvent(tick, 0, 1, TPB // 2, SYNC_SYLLABLE)
        for tick in range(4 * TPB, 200 * TPB, 8 * TPB)
    ]
    on_time = simulate(during_syllables(syllables), syllables)
    slightly = simulate(during_syllables(syllables, TPB // 4), syllables)
    very_late = simulate(during_syllables(syllables, 4 * TPB), syllables)
    assert on_time >= slightly > very_late
    assert very_late < 30


def test_singing_nonstop_scores_less_than_singing_on_time():
    syllables = [
        SyncEvent(tick, 0, 1, TPB // 2, SYNC_SYLLABLE)
        for phrase in range(0, 400 * TPB, 40 * TPB)
        for tick in range(phrase + 8 * TPB, phrase + 12 * TPB, TPB)
    ]
    nonstop = simulate(lambda tick: True, syllables)
    assert nonstop < 50
    assert simulate(during_syllables(syllables), syllables) > 90


def test_no_score_when_most_of_the_song_was_skipped_or_there_is_no_lyric():
    syllables = sung_syllables()
    assert simulate(lambda tick: True, syllables, end=4 * TPB) is None
    assert simulate(lambda tick: True, [], end=10 * TPB) is None
    from score import RhythmScorer

    assert RhythmScorer(syllables, TPB).result() is None


def test_skipped_part_does_not_count_against_the_singer():
    from score import RhythmScorer

    syllables = sung_syllables()
    sing = during_syllables(syllables)
    scorer = RhythmScorer(syllables, TPB)
    start = syllables[15].tick  # The first 15 syllables were jumped over.
    for tick in range(start, syllables[-1].tick + TPB, TPB // 10):
        scorer.add_frame(tick, sing(tick))
    assert scorer.result() == 100


def feed_levels(syllables, sung, background, voice=-20.0):
    from score import RhythmScorer

    scorer = RhythmScorer(syllables, TPB)
    for tick in range(0, syllables[-1].tick + 40 * TPB, TPB // 10):
        scorer.add_level(tick, voice if sung(tick) else background)
    return scorer.result()


def dense_phrases():
    """Back-to-back syllables with no pause, then a long instrumental break."""
    return [
        SyncEvent(tick, 0, 1, TPB, SYNC_SYLLABLE)
        for start in range(20 * TPB, 400 * TPB, 80 * TPB)
        for tick in range(start, start + 40 * TPB, TPB)
    ]


@pytest.mark.parametrize("background", [-70.0, -34.0])
def test_dense_lyrics_sung_well_score_high_in_quiet_and_with_loud_speakers(background):
    syllables = dense_phrases()
    assert feed_levels(syllables, during_syllables(syllables), background) >= 95


@pytest.mark.parametrize("background", [-70.0, -34.0])
def test_silent_singer_scores_zero_even_with_speakers_in_the_microphone(background):
    assert feed_levels(dense_phrases(), lambda tick: False, background) == 0


def test_voice_is_not_mistaken_for_background_when_music_is_only_a_bit_quieter():
    syllables = dense_phrases()
    # 5 dB above the background is not enough to tell a voice from the music.
    assert feed_levels(syllables, during_syllables(syllables), -34.0, voice=-29.0) == 0


def test_microphone_meter_splits_audio_into_levels(qapp):
    from score import MicrophoneMeter, level_db

    assert level_db(array.array("h", [0, 0])) == -120.0
    assert level_db(array.array("h", [32767, -32767])) == pytest.approx(0, abs=0.01)

    meter = MicrophoneMeter()
    levels = []
    meter.level_measured.connect(levels.append)
    frame = array.array("h", [3276] * 800).tobytes()
    meter.consume(frame[:500])
    assert levels == []
    meter.consume(frame[500:] + frame)
    assert len(levels) == 2
    assert levels[0] == pytest.approx(-20, abs=0.1)


def envelope_scorer(syllables):
    """Music gets 20 dB louder during the sung phrases, as many songs do."""
    from score import RhythmScorer

    scorer = RhythmScorer(syllables, TPB)
    ticks = list(range(0, syllables[-1].tick + 40 * TPB, TPB // 10))
    loud = during_syllables([SyncEvent(s.tick - TPB, 0, 1, s.duration + 2 * TPB, SYNC_SYLLABLE) for s in syllables])
    music = [-10.0 if loud(tick) else -30.0 for tick in ticks]
    scorer.set_music_envelope(ticks, music)
    return scorer, ticks, music


def test_loud_verses_from_the_speakers_are_not_mistaken_for_a_voice():
    from score import RhythmScorer

    syllables = dense_phrases()
    # Without knowing the music, its rise during the verses looks like someone singing.
    naive = RhythmScorer(syllables, TPB)
    scorer, ticks, music = envelope_scorer(syllables)
    for tick, level in zip(ticks, music):
        naive.add_level(tick, level - 25)
        scorer.add_level(tick, level - 25)
    assert naive.result() > 50
    assert scorer.result() == 0


def test_voice_above_the_known_music_scores_high():
    syllables = dense_phrases()
    scorer, ticks, music = envelope_scorer(syllables)
    sung = during_syllables(syllables)
    for tick, level in zip(ticks, music):
        scorer.add_level(tick, level - 25 + (10 if sung(tick) else 0))
    assert scorer.result() >= 95


@needs_soundfont
def test_music_envelope_follows_the_songs_real_level():
    from score import render_music_envelope

    midi = mido.MidiFile(type=1, ticks_per_beat=TPB)
    track = mido.MidiTrack()
    for velocity in (10, 127):
        for _ in range(8):
            track.append(mido.Message("note_on", note=60, velocity=velocity, time=0))
            track.append(mido.Message("note_off", note=60, velocity=0, time=TPB))
    midi.tracks.append(track)

    ticks, levels = render_music_envelope(midi_bytes(midi), soundfont, 16 * TPB)

    assert ticks == sorted(ticks)
    quiet = [level for tick, level in zip(ticks, levels) if TPB < tick < 7 * TPB]
    loud = [level for tick, level in zip(ticks, levels) if 9 * TPB < tick < 15 * TPB]
    assert sum(loud) / len(loud) > sum(quiet) / len(quiet) + 10


@needs_soundfont
def test_music_envelope_can_be_cancelled():
    import threading

    from score import render_music_envelope

    cancelled = threading.Event()
    cancelled.set()
    assert render_music_envelope(midi_bytes(kar_midi(["\\la", "la"])), soundfont, 8 * TPB, cancelled) == ([], [])


@needs_soundfont
def test_window_gives_the_scorer_the_music_level_in_the_background(tmp_path, qapp, monkeypatch):
    import player
    from player import PlayerWindow

    path = write(kar_midi(["\\Can", "tar"], notes=16), tmp_path / "x.kar")
    # The window behaves as in real use, but needs no sound card: build machines have none.
    real_synth = player.Synth
    monkeypatch.setattr(player, "Synth", lambda font, realtime, gain: real_synth(font, realtime=False, gain=gain))
    window = PlayerWindow(soundfont, realtime=True, gain=0.0)
    window._microphone.start = lambda: True
    try:
        assert window.open_song(str(path))
        scorer = window._scorer
        worker = window._envelope_worker
        worker.join(timeout=20)
        assert scorer._music_ticks
        assert not worker.is_alive()

        assert window.open_song(str(path))
        assert window._scorer is not scorer
        assert worker is not window._envelope_worker
    finally:
        window.close()


def test_microphone_meter_reads_the_formats_windows_may_offer(qapp):
    import numpy as np
    from PySide6.QtMultimedia import QAudioFormat
    from score import FRAME_MS, MicrophoneMeter

    rate = 48000
    samples = rate * FRAME_MS // 1000
    # A stereo signal at a tenth of full scale is -20 dB, whatever its encoding.
    signals = {
        QAudioFormat.SampleFormat.Float: np.full(samples * 2, 0.1, dtype="<f4"),
        QAudioFormat.SampleFormat.Int32: np.full(samples * 2, 214748364, dtype="<i4"),
        QAudioFormat.SampleFormat.Int16: np.full(samples * 2, 3277, dtype="<i2"),
    }
    for sample_format, signal in signals.items():
        meter = MicrophoneMeter()
        levels = []
        meter.level_measured.connect(levels.append)
        meter._configure(sample_format, rate, 2)
        meter.consume(signal.tobytes())
        assert levels == [pytest.approx(-20, abs=0.1)]


def test_microphone_meter_discards_the_burst_after_opening(qapp):
    from score import MicrophoneMeter

    meter = MicrophoneMeter()
    levels = []
    meter.level_measured.connect(levels.append)
    meter._frames_to_skip = 2
    frame = array.array("h", [32000] * 800).tobytes()
    meter.consume(frame * 2)
    assert levels == []
    meter.consume(frame)
    assert len(levels) == 1


def finish_with_singing(window):
    """Plays the current song to its end, with a voice only where singing is due."""
    scorer = window._scorer
    for syllable in window._song.timeline.syllables:
        for offset in range(0, 40):
            scorer.add_frame(syllable.tick + offset, True)
    for frame in range(30):  # Quiet background well after the singing.
        scorer.add_frame(100_000 + frame, False)
    advance(window._synth, 8.0)
    window._refresh()


@needs_soundfont
def test_score_stays_8_seconds_then_the_next_song_starts(tmp_path, window):
    window.set_song_folder(str(song_folder(tmp_path, ["a.kar", "b.kar"])))
    window.add_to_queue(window._catalog[0])
    window.add_to_queue(window._catalog[1])
    window.play_queue()

    finish_with_singing(window)

    assert window._lyrics.score() == 100
    assert window._score_timer.isActive()
    assert window._score_timer.interval() == 8000
    assert window._song.path.stem == "a"
    assert not window._active
    assert queue_names(window) == ["b"]

    window._score_timer.stop()
    window._score_timer.timeout.emit()
    assert window._lyrics.score() is None
    assert window._song.path.stem == "b"
    assert window._active


@needs_soundfont
def test_score_ends_quietly_when_the_queue_is_empty(tmp_path, window):
    window.set_song_folder(str(song_folder(tmp_path, ["a.kar"])))
    window.add_to_queue(window._catalog[0])
    window.play_queue()
    finish_with_singing(window)
    assert window._lyrics.score() == 100

    window._score_timer.stop()
    window._score_timer.timeout.emit()
    assert window._lyrics.score() is None
    assert not window._active


@needs_soundfont
@pytest.mark.parametrize("action", ["toggle_play", "stop", "play_next"])
def test_score_can_be_dismissed_early(tmp_path, window, action):
    window.set_song_folder(str(song_folder(tmp_path, ["a.kar"])))
    window.add_to_queue(window._catalog[0])
    window.play_queue()
    finish_with_singing(window)

    getattr(window, action)()

    assert window._lyrics.score() is None
    assert not window._score_timer.isActive()


@needs_soundfont
def test_no_score_without_microphone_data_moves_on_at_once(tmp_path, window):
    window.set_song_folder(str(song_folder(tmp_path, ["a.kar", "b.kar"])))
    window.add_to_queue(window._catalog[0])
    window.add_to_queue(window._catalog[1])
    window.play_queue()
    advance(window._synth, 8.0)
    window._refresh()
    assert window._lyrics.score() is None
    assert window._song.path.stem == "b"


@needs_soundfont
def test_stopping_a_song_gives_no_score(tmp_path, window):
    window.set_song_folder(str(song_folder(tmp_path, ["a.kar"])))
    window.add_to_queue(window._catalog[0])
    window.play_queue()
    window._scorer.add_frame(0, True)
    window.stop()
    assert window._lyrics.score() is None


@needs_soundfont
def test_microphone_levels_are_ignored_while_paused(tmp_path, window):
    window.set_song_folder(str(song_folder(tmp_path, ["a.kar"])))
    window.add_to_queue(window._catalog[0])
    window.play_queue()
    window.on_microphone_level(-20.0)
    assert len(window._scorer._frames) == 1
    window.toggle_play()
    window.on_microphone_level(-20.0)
    assert len(window._scorer._frames) == 1


@needs_soundfont
def test_score_option_is_saved_and_turns_scoring_off(tmp_path, qapp):
    from player import PlayerWindow, open_settings

    first = PlayerWindow(soundfont, realtime=False)
    assert first._score_action.isChecked()
    first.set_song_folder(str(song_folder(tmp_path, ["a.kar"])))
    first.add_to_queue(first._catalog[0])
    first.play_queue()
    first._score_action.setChecked(False)
    assert first._scorer is None
    first.close()
    assert open_settings().value("score_enabled") == "false"

    second = PlayerWindow(soundfont, realtime=False)
    try:
        assert not second._score_action.isChecked()
    finally:
        second.close()


@needs_soundfont
def test_screen_shows_the_score(tmp_path, window, qapp):
    window.show()
    window._lyrics.show_lines(LineState_for("texto"), LineState_for(""))
    window._lyrics.set_score(87)
    assert not window._lyrics.grab().isNull()


def LineState_for(text):
    from song import LineState

    return LineState(text, 0.0)


def test_synth_gain_changes_the_loudness_while_playing():
    synth = Synth(soundfont, realtime=False, gain=0.2)
    try:
        synth.load(midi_bytes(kar_midi(["\\la", "la"], notes=32)))
        synth.play()
        quiet = peak(advance(synth, 2.0))
        synth.set_gain(0.6)
        loud = peak(advance(synth, 2.0))
        assert loud > 2 * quiet
    finally:
        synth.close()


@needs_soundfont
def test_volume_starts_at_default_is_saved_and_reaches_the_synth(tmp_path, qapp):
    from player import DEFAULT_VOLUME, PlayerWindow

    first = PlayerWindow(soundfont, realtime=False)
    assert first._volume_slider.value() == DEFAULT_VOLUME
    assert first._volume_label.text() == f"{DEFAULT_VOLUME}%"
    first.set_song_folder(str(song_folder(tmp_path, ["a.kar"])))
    first.add_to_queue(first._catalog[0])
    first.play_queue()
    calls = []
    real = first._synth.set_gain
    first._synth.set_gain = lambda gain: (calls.append(gain), real(gain))
    first._volume_slider.setValue(80)
    assert calls == [0.8]
    first.close()

    second = PlayerWindow(soundfont, realtime=False)
    try:
        assert second._volume_slider.value() == 80
    finally:
        second.close()


@needs_soundfont
def test_volume_keys_and_limits(window, qapp):
    from PySide6.QtCore import Qt

    window.show()
    window.activateWindow()
    qapp.processEvents()
    press(window, Qt.Key.Key_Up)
    assert window._volume_slider.value() == 55
    press(window, Qt.Key.Key_Down, Qt.Key.Key_Down)
    assert window._volume_slider.value() == 45
    window.change_volume(1000)
    assert window._volume_label.text() == "100%"
    window.change_volume(-1000)
    assert window._volume_label.text() == "0%"


@needs_soundfont
def test_explicit_gain_is_not_overridden_by_the_volume_control(tmp_path, qapp):
    from player import PlayerWindow

    window = PlayerWindow(soundfont, realtime=False, gain=0.0)
    try:
        window.set_song_folder(str(song_folder(tmp_path, ["a.kar"])))
        window.add_to_queue(window._catalog[0])
        window.play_queue()
        window._volume_slider.setValue(90)
        assert peak(advance(window._synth, 0.5)) <= 1  # Only rounding noise.
    finally:
        window.close()


@needs_soundfont
def test_raising_volume_mid_song_does_not_look_like_a_voice(tmp_path, window):
    window.set_song_folder(str(song_folder(tmp_path, ["a.kar"])))
    window.add_to_queue(window._catalog[0])
    window.play_queue()
    window.on_microphone_level(-30.0)
    window._volume_slider.setValue(100)  # Twice the gain: 6 dB more from the speakers.
    window.on_microphone_level(-24.0)
    levels = [level for _, level in window._scorer._frames]
    assert levels[1] == pytest.approx(levels[0], abs=0.1)


def test_every_theme_style_sheet_and_chevron_exists(qapp):
    from theme import ICON_DIRECTORY, THEMES, style_sheet

    for theme in THEMES.values():
        assert (ICON_DIRECTORY / theme.chevron).is_file()
        sheet = style_sheet(theme, 12)
        assert sheet.count("{") == sheet.count("}")
        assert theme.chevron in sheet


@pytest.mark.parametrize("kind", ["play", "pause", "stop", "next"])
def test_media_icons_are_drawn_in_the_requested_colour(qapp, kind):
    from theme import ICON_SIZE, media_icon

    image = media_icon(kind, "#ff0000").pixmap(ICON_SIZE, ICON_SIZE).toImage()
    colours = {
        image.pixelColor(x, y).name()
        for x in range(0, ICON_SIZE, 2)
        for y in range(0, ICON_SIZE, 2)
        if image.pixelColor(x, y).alpha() == 255
    }
    assert colours == {"#ff0000"}


def test_unknown_media_icon_is_rejected(qapp):
    from theme import media_icon

    with pytest.raises(ValueError):
        media_icon("rewind", "#ff0000")


@needs_soundfont
def test_theme_defaults_to_dark_toggles_and_is_saved(qapp):
    from PySide6.QtWidgets import QApplication
    from player import PlayerWindow

    first = PlayerWindow(soundfont, realtime=False)
    assert first._theme_name == "dark"
    assert first._theme_button.text() == "Modo claro"
    assert "#212529" in QApplication.instance().styleSheet()

    first.toggle_theme()
    assert first._theme_name == "light"
    assert first._theme_button.text() == "Modo escuro"
    assert "#ffffff" in QApplication.instance().styleSheet()
    first.close()

    second = PlayerWindow(soundfont, realtime=False)
    try:
        assert second._theme_name == "light"
        second.toggle_theme()
        assert second._theme_name == "dark"
    finally:
        second.close()


@needs_soundfont
def test_unknown_saved_theme_falls_back_to_dark(qapp):
    from player import PlayerWindow, open_settings

    settings = open_settings()
    settings.setValue("theme", "roxo")
    settings.sync()
    window = PlayerWindow(soundfont, realtime=False)
    try:
        assert window._theme_name == "dark"
    finally:
        window.close()


def test_lyrics_darken_a_bright_background_around_the_letters(qapp):
    from PySide6.QtGui import QColor, QImage
    from player import LyricWidget
    from song import LineState

    bright = QImage(800, 400, QImage.Format.Format_RGB32)
    bright.fill(QColor("white"))
    widget = LyricWidget()
    widget.resize(800, 400)
    widget.set_background(bright)
    widget.show_lines(LineState("Texto de teste", 3.0), LineState())

    image = widget.grab().toImage()
    baseline, _ = widget.line_baselines()
    # Just outside the outline, the background must be visibly darker than plain white.
    row = round(baseline) + 4
    darkest = min(image.pixelColor(x, row).lightness() for x in range(0, 800, 2))
    assert darkest < 170
    assert image.pixelColor(2, 2).lightness() == 255  # Far from the text, the image is intact.


def test_wipe_reuses_prepared_lines_and_shows_both_colours(qapp):
    from player import MAX_CACHED_SPRITES, SUNG, UNSUNG, LyricWidget
    from song import LineState

    widget = LyricWidget()
    widget.resize(800, 300)
    for progress in (2.0, 5.0, 9.0):
        widget.show_lines(LineState("Texto de teste", progress), LineState())
        image = widget.grab().toImage()
    assert len(widget._sprites) == 1

    colours = {image.pixelColor(x, y).name() for x in range(800) for y in range(0, 300, 2)}
    assert SUNG.name() in colours
    assert UNSUNG.name() in colours

    # The sung part ends where the wipe says: compare the colour on each side of it.
    columns_sung = [
        x for x in range(800)
        if any(image.pixelColor(x, y).name() == SUNG.name() for y in range(0, 300, 2))
    ]
    columns_unsung = [
        x for x in range(800)
        if any(image.pixelColor(x, y).name() == UNSUNG.name() for y in range(0, 300, 2))
    ]
    assert max(columns_sung) < min(columns_unsung) + 3

    for index in range(MAX_CACHED_SPRITES * 2):
        widget.show_lines(LineState(f"Linha {index}", 0.0), LineState())
        widget.grab()
    assert len(widget._sprites) <= MAX_CACHED_SPRITES


def test_changing_font_prepares_the_line_again(qapp):
    from PySide6.QtGui import QFont
    from player import LyricWidget
    from song import LineState

    widget = LyricWidget()
    widget.resize(800, 300)
    widget.show_lines(LineState("Texto", 1.0), LineState())
    widget.grab()
    widget.set_lyric_font(QFont("Liberation Sans", 30))
    widget.grab()
    assert len(widget._sprites) == 2


def score_xml(notes, *, composer="Compositor", repeat=False):
    barline = '<barline location="right"><repeat direction="backward"/></barline>' if repeat else ""
    credit = f'<identification><creator type="composer">{composer}</creator></identification>' if composer else ""
    return (
        '<?xml version="1.0" encoding="UTF-8"?><score-partwise version="3.1">'
        f"<work><work-title>Canção</work-title></work>{credit}"
        '<part-list><score-part id="P1"><part-name>Voz</part-name></score-part>'
        '<score-part id="P2"><part-name>Piano</part-name></score-part></part-list>'
        '<part id="P1"><measure number="1"><attributes><divisions>1</divisions>'
        f"<time><beats>4</beats><beat-type>4</beat-type></time></attributes>{notes}{barline}</measure></part>"
        '<part id="P2"><measure number="1"><attributes><divisions>1</divisions>'
        '<time><beats>4</beats><beat-type>4</beat-type></time></attributes>'
        '<note><pitch><step>C</step><octave>3</octave></pitch><duration>4</duration><type>whole</type></note>'
        f"{barline}</measure></part></score-partwise>"
    )


def sung_note(step, syllabic, text):
    return (
        f"<note><pitch><step>{step}</step><octave>4</octave></pitch><duration>1</duration><type>quarter</type>"
        f'<lyric number="1"><syllabic>{syllabic}</syllabic><text>{text}</text></lyric></note>'
    )


SUNG_NOTES = sung_note("C", "begin", "Can") + sung_note("D", "end", "ção") + sung_note("E", "single", "de") + sung_note("F", "single", "amor")
PLAIN_NOTE = '<note><pitch><step>C</step><octave>4</octave></pitch><duration>4</duration><type>whole</type></note>'


def write_mxl(path, xml):
    import zipfile

    container = (
        '<?xml version="1.0"?><container><rootfiles><rootfile full-path="score.musicxml"/></rootfiles></container>'
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("score.musicxml", xml)
    return path


@pytest.mark.parametrize("suffix", [".musicxml", ".mxl", ".xml"])
def test_score_loads_directly_with_title_singer_lyrics_and_all_parts(tmp_path, suffix):
    path = tmp_path / f"song{suffix}"
    if suffix == ".mxl":
        write_mxl(path, score_xml(SUNG_NOTES))
    else:
        path.write_text(score_xml(SUNG_NOTES), encoding="utf-8")

    song = load_song(path)

    assert (song.title, song.singer) == ("Canção", "Compositor")
    tpb = song.ticks_per_beat
    assert [s.tick for s in song.timeline.syllables] == [0, tpb, 2 * tpb, 3 * tpb]
    upper, _ = song.timeline.lines_at(tpb + tpb // 2)
    assert upper.text == "Canção de amor"
    assert upper.sung_chars == pytest.approx(4.5)
    played = mido.MidiFile(file=io.BytesIO(song.midi_bytes))
    assert sum(m.type == "note_on" and m.velocity > 0 for t in played.tracks for m in t) == 5  # Voice and piano.


def test_score_timing_matches_the_converted_st3(tmp_path):
    from conversor import convert_file

    path = tmp_path / "song.musicxml"
    path.write_text(score_xml(SUNG_NOTES * 6), encoding="utf-8")
    converted = tmp_path / "song.st3"
    converted.write_bytes(convert_file(path)[0])

    direct = load_song(path)
    via_st3 = load_song(converted)

    assert direct.total_ticks == via_st3.total_ticks
    assert direct.timeline.lyrics == via_st3.timeline.lyrics
    assert [(s.tick, s.position, s.length, s.duration) for s in direct.timeline.syllables] == [
        (s.tick, s.position, s.length, s.duration) for s in via_st3.timeline.syllables
    ]


def test_score_without_lyrics_plays_without_a_lyric_line(tmp_path):
    path = tmp_path / "instrumental.musicxml"
    path.write_text(score_xml(PLAIN_NOTE), encoding="utf-8")
    song = load_song(path)
    assert not song.timeline.has_lyrics
    assert song.total_ticks > 0


@pytest.mark.parametrize("content", ["isto nao e xml", "<html><body>nada</body></html>"])
def test_broken_score_reports_an_error(tmp_path, content):
    path = tmp_path / "ruim.musicxml"
    path.write_text(content)
    with pytest.raises(SongError):
        load_song(path)


def test_damaged_mxl_reports_an_error(tmp_path):
    path = tmp_path / "ruim.mxl"
    path.write_bytes(b"PK isto nao e um zip")
    with pytest.raises(SongError):
        load_song(path)


def test_catalog_lists_scores_with_singer_and_ignores_other_xml(tmp_path):
    from catalog import find_songs, read_singer

    folder = tmp_path / "m"
    folder.mkdir()
    (folder / "a.musicxml").write_text(score_xml(SUNG_NOTES, composer="Tom &amp; Vinicius"), encoding="utf-8")
    write_mxl(folder / "b.mxl", score_xml(SUNG_NOTES, composer="Elis"))
    (folder / "c.xml").write_text(score_xml(SUNG_NOTES, composer=""), encoding="utf-8")
    (folder / "config.xml").write_text("<config><valor>1</valor></config>")
    (folder / "ruim.mxl").write_bytes(b"nao e zip")

    assert sorted(path.name for path in find_songs(folder)) == ["a.musicxml", "b.mxl", "c.xml", "ruim.mxl"]
    assert read_singer(folder / "a.musicxml") == "Tom & Vinicius"
    assert read_singer(folder / "b.mxl") == "Elis"
    assert read_singer(folder / "c.xml") == ""
    assert read_singer(folder / "ruim.mxl") == ""


@needs_soundfont
def test_score_plays_from_the_queue_and_gets_a_score(tmp_path, window):
    folder = tmp_path / "m"
    folder.mkdir()
    (folder / "a.musicxml").write_text(score_xml(SUNG_NOTES), encoding="utf-8")
    window.set_song_folder(str(folder))
    assert window._catalog_list.item(0).text() == "0001  a — Compositor"
    window.add_to_queue(window._catalog[0])
    window.play_queue()
    assert window._synth.is_playing

    finish_with_singing(window)
    assert window._lyrics.score() == 100


@needs_soundfont
def test_unreadable_score_in_queue_is_skipped(tmp_path, window):
    folder = song_folder(tmp_path, ["b.kar"])
    (folder / "a.musicxml").write_text("isto nao e xml")
    window.set_song_folder(str(folder))
    for entry in window._catalog:
        window.add_to_queue(entry)
    window.play_queue()
    assert window._song.path.stem == "b"


def played_notes(song):
    notes = []
    for track in mido.MidiFile(file=io.BytesIO(song.midi_bytes)).tracks:
        tick = 0
        for message in track:
            tick += message.time
            if message.type in ("note_on", "note_off"):
                notes.append((tick, message.type, message.channel, message.note, message.velocity))
    return sorted(notes)


def test_score_is_interpreted_once_and_again_only_if_it_changes(tmp_path, monkeypatch):
    import kar

    calls = []
    real = kar.read_musicxml
    monkeypatch.setattr(kar, "read_musicxml", lambda path: (calls.append(path), real(path))[1])
    path = tmp_path / "song.musicxml"
    path.write_text(score_xml(SUNG_NOTES), encoding="utf-8")

    first = load_song(path)
    second = load_song(path)

    assert len(calls) == 1
    assert played_notes(second) == played_notes(first)
    assert second.total_ticks == first.total_ticks
    assert (second.title, second.singer) == (first.title, first.singer)
    assert second.timeline.lyrics == first.timeline.lyrics
    assert [(s.tick, s.duration) for s in second.timeline.syllables] == [(s.tick, s.duration) for s in first.timeline.syllables]

    path.write_text(score_xml(SUNG_NOTES * 2), encoding="utf-8")
    assert len(load_song(path).timeline.syllables) == 8
    assert len(calls) == 2


def test_damaged_prepared_copy_is_redone(tmp_path):
    from song import is_prepared_score, score_cache_path

    path = tmp_path / "song.musicxml"
    path.write_text(score_xml(SUNG_NOTES), encoding="utf-8")
    assert not is_prepared_score(path)
    load_song(path)
    assert is_prepared_score(path)

    score_cache_path(path).write_text("{ quebrado")
    assert len(load_song(path).timeline.syllables) == 4


def test_score_plays_even_if_the_cache_cannot_be_written(tmp_path, monkeypatch):
    blocker = tmp_path / "arquivo"
    blocker.write_text("x")
    monkeypatch.setenv("XDG_CACHE_HOME", str(blocker))
    path = tmp_path / "song.musicxml"
    path.write_text(score_xml(SUNG_NOTES), encoding="utf-8")
    assert len(load_song(path).timeline.syllables) == 4


@needs_soundfont
def test_window_announces_only_the_first_preparation(tmp_path, window):
    path = tmp_path / "song.musicxml"
    path.write_text(score_xml(SUNG_NOTES), encoding="utf-8")
    assert window.open_song(str(path), autoplay=False)
    assert "Preparando" in window._lyrics.notice()

    window._clear_notice()
    assert window.open_song(str(path), autoplay=False)
    assert window._lyrics.notice() == ""


def repeated_score_xml(ending_measures, *, second_repeat_end=False):
    """Measure 1 and 2 repeat; measures 2 and 3 are endings, one marking per measure."""
    attributes = "<attributes><divisions>1</divisions><time><beats>2</beats><beat-type>4</beat-type></time></attributes>"

    def measure(number, word, extra=""):
        return (
            f'<measure number="{number}">' + (attributes if number == 1 else "")
            + sung_note("C", "single", word) + sung_note("D", "single", word) + extra + "</measure>"
        )

    def ending(number, kind="discontinue"):
        return (
            f'<barline location="left"><ending number="{number}" type="start"/></barline>',
            f'<barline location="right"><ending number="{number}" type="{kind}"/></barline>',
        )

    start, stop = ending(1)
    first_end, last_stop = ending(1, "stop")
    measures = [
        measure(1, "um"),
        measure(2, "dois", start + stop),
        measure(3, "tres", first_end + last_stop + '<barline location="right"><repeat direction="backward"/></barline>') if ending_measures == 2
        else measure(3, "tres", '<barline location="right"><repeat direction="backward"/></barline>'),
        measure(4, "quatro", '<barline location="left"><ending number="2" type="start"/></barline><barline location="right"><ending number="2" type="stop"/></barline>'),
    ]
    return (
        '<?xml version="1.0" encoding="UTF-8"?><score-partwise version="3.1"><work><work-title>Rep</work-title></work>'
        '<part-list><score-part id="P1"><part-name>Voz</part-name></score-part></part-list>'
        f'<part id="P1">{"".join(measures)}</part></score-partwise>'
    )


def beats_played(song):
    return song.total_ticks / song.ticks_per_beat


def test_ending_split_into_one_marking_per_measure_repeats_as_written(tmp_path):
    path = tmp_path / "song.musicxml"
    path.write_text(repeated_score_xml(2), encoding="utf-8")

    song = load_song(path)

    # Measures 1 2 3, back to 1, then the second ending, 4: five measures of 2 beats.
    assert beats_played(song) == pytest.approx(10, abs=0.5)
    words = [song.timeline.lyrics[s.position : s.position + s.length].strip() for s in song.timeline.syllables]
    assert words == ["um", "um", "dois", "dois", "tres", "tres", "um", "um", "quatro", "quatro"]


def test_repeats_that_cannot_be_followed_play_straight_through(tmp_path, monkeypatch):
    import music21

    real = music21.stream.Score.expandRepeats
    calls = []

    def refuse_once(self, *args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise music21.repeat.ExpanderException("cannot expand")
        return real(self, *args, **kwargs)

    monkeypatch.setattr(music21.stream.Score, "expandRepeats", refuse_once)
    path = tmp_path / "song.musicxml"
    path.write_text(repeated_score_xml(2), encoding="utf-8")

    song = load_song(path)

    assert beats_played(song) == pytest.approx(8, abs=0.5)
    assert len(song.timeline.syllables) == 8


def test_failure_preparing_the_music_becomes_a_clear_error(tmp_path, monkeypatch):
    import music21

    def explode(self, *args, **kwargs):
        raise RuntimeError("erro interno")

    monkeypatch.setattr(music21.stream.Score, "write", explode)
    path = tmp_path / "song.musicxml"
    path.write_text(score_xml(SUNG_NOTES), encoding="utf-8")
    with pytest.raises(SongError):
        load_song(path)


def run_self_test(tmp_path, *arguments):
    """Through the program's real command line, as the Windows build gets checked."""
    result = tmp_path / "resultado.txt"
    project = Path(__file__).resolve().parent.parent
    environment = {**os.environ, "XDG_CONFIG_HOME": str(tmp_path / "config"), "QT_QPA_PLATFORM": "offscreen"}
    process = subprocess.run(
        [sys.executable, str(project / "player.py"), "--self-test", str(result), *arguments],
        env=environment,
        capture_output=True,
        timeout=120,
    )
    return process.returncode, result.read_text(encoding="utf-8")


def test_self_test_passes_and_leaves_the_users_settings_alone(tmp_path):
    video = str(Path(__file__).parent / "data" / "video-teste.mp4")
    code, text = run_self_test(tmp_path, video)
    assert code == 0, text
    assert "RESULTADO: tudo certo" in text
    assert "FALHA" not in text
    assert not (tmp_path / "config" / "freekaraoke").exists()


def test_self_test_reports_a_failure_instead_of_stopping(tmp_path):
    code, text = run_self_test(tmp_path)
    assert code == 1
    assert "FALHA vídeo de fundo" in text
    assert "ok    som sintetizado" in text
    assert "RESULTADO: 1 falhas" in text


@needs_soundfont
def test_interface_font_follows_the_system_until_one_is_chosen(qapp):
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication
    from player import PlayerWindow

    original = QApplication.font()
    system = QFont(original)
    system.setPointSize(15)
    QApplication.setFont(system)
    first = PlayerWindow(soundfont, realtime=False)
    try:
        assert first.interface_font_size() == 15
        assert "font-size: 15pt" in QApplication.instance().styleSheet()
        assert "font-size: 24pt" in QApplication.instance().styleSheet()  # The song title scales too.

        first.set_interface_font_size(20)
        assert "font-size: 20pt" in QApplication.instance().styleSheet()
        first.toggle_theme()
        assert "font-size: 20pt" in QApplication.instance().styleSheet()  # Survives changing the theme.
        first.close()

        second = PlayerWindow(soundfont, realtime=False)
        assert second.interface_font_size() == 20
        second.set_interface_font_size(None)
        assert second.interface_font_size() == 15
        assert "font-size: 15pt" in QApplication.instance().styleSheet()
        second.close()
    finally:
        QApplication.setFont(original)


@needs_soundfont
@pytest.mark.parametrize("saved, expected", [(2, 8), (500, 32), ("abc", 15)])
def test_bad_saved_interface_font_size_is_handled(qapp, saved, expected):
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication
    from player import PlayerWindow, open_settings

    original = QApplication.font()
    system = QFont(original)
    system.setPointSize(15)
    QApplication.setFont(system)
    settings = open_settings()
    settings.setValue("interface_font_size", saved)
    settings.sync()
    window = PlayerWindow(soundfont, realtime=False)
    try:
        assert window.interface_font_size() == expected
    finally:
        window.close()
        QApplication.setFont(original)


@needs_soundfont
def test_interface_font_dialog_applies_only_when_accepted(window, monkeypatch):
    import player

    monkeypatch.setattr(player.QInputDialog, "getInt", lambda *args: (18, False))
    before = window.interface_font_size()
    window.choose_interface_font_size()
    assert window.interface_font_size() == before

    monkeypatch.setattr(player.QInputDialog, "getInt", lambda *args: (18, True))
    window.choose_interface_font_size()
    assert window.interface_font_size() == 18


@needs_soundfont
def test_large_interface_letters_move_controls_to_their_own_row(window, qapp):
    width = window.screen().availableGeometry().width()
    fits = max(8, int(width * 0.9 / 80))
    window.set_interface_font_size(8)
    if 8 * 80 <= width * 0.95:
        assert window._settings_row.count() == 0
    window.set_interface_font_size(32)
    assert window._settings_row.count() == len(window._settings_widgets)
    window.show()
    qapp.processEvents()
    assert window.minimumSizeHint().width() < 32 * 80
    window.set_interface_font_size(fits)
    assert window._settings_row.count() == 0


PROJECT = Path(__file__).resolve().parent.parent
TRANSLATED_MODULES = [
    "player.py", "song.py", "synth.py", "st3.py", "kar.py", "song_list_pdf.py",
    "conversor.py", "st3_writer.py", "tolerant_midi.py", "app.py",
]


def texts_given_to_tr():
    import ast
    import re

    found = set()
    for name in TRANSLATED_MODULES:
        for node in ast.walk(ast.parse((PROJECT / name).read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "tr":
                assert isinstance(node.args[0], ast.Constant), f"{name}:{node.lineno}: tr needs a literal"
                found.add(node.args[0].value)
    template = (PROJECT / "templates" / "index.html").read_text(encoding="utf-8")
    found.update(re.findall(r'tr\("([^"]+)"\)', template))
    return found


def test_every_text_has_an_english_translation_and_none_is_left_over():
    from i18n import TRANSLATIONS

    used = texts_given_to_tr()
    assert sorted(used - set(TRANSLATIONS)) == []
    assert sorted(set(TRANSLATIONS) - used) == []


def test_translations_keep_the_same_placeholders_and_filters():
    import re

    from i18n import TRANSLATIONS

    for portuguese, english in TRANSLATIONS.items():
        assert sorted(re.findall(r"\{\d\}", portuguese)) == sorted(re.findall(r"\{\d\}", english)), portuguese
        assert portuguese.count("&") == english.count("&"), portuguese
        assert re.findall(r"\*\.\w+", portuguese) == re.findall(r"\*\.\w+", english), portuguese


def test_no_portuguese_message_escapes_translation():
    import ast

    # Accents in anything but tr() would show up untranslated in English.
    problems = []
    for name in TRANSLATED_MODULES:
        tree = ast.parse((PROJECT / name).read_text(encoding="utf-8"))
        given = {
            id(argument)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "tr"
            for argument in node.args
        }
        documentation = {
            id(node.body[0].value)
            for node in ast.walk(tree)
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef))
            and node.body and isinstance(node.body[0], ast.Expr)
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if id(node) not in given and id(node) not in documentation:
                    if any(character in "áàâãéêíóôõúçÁÉÍÓÚÇ" for character in node.value):
                        problems.append((name, node.lineno, node.value[:40]))
    assert problems == []


@pytest.mark.parametrize(
    "environment, expected",
    [
        ({"LANG": "pt_BR.UTF-8"}, "pt"),
        ({"LANG": "pt_PT.UTF-8"}, "pt"),
        ({"LANG": "en_US.UTF-8"}, "en"),
        ({"LANG": "de_DE.UTF-8"}, "en"),
        ({"LANG": "C"}, "en"),
        ({}, "en"),
        ({"LANG": "pt_BR.UTF-8", "LANGUAGE": "fr"}, "en"),
        ({"LANG": "en_US", "LC_ALL": "pt_BR"}, "pt"),
        ({"LANG": "pt_BR", "FREEKARAOKE_LANG": "en"}, "en"),
        ({"LANG": "en_US", "FREEKARAOKE_LANG": "pt"}, "pt"),
    ],
)
def test_language_follows_the_system(monkeypatch, environment, expected):
    from i18n import system_language

    for variable in ("FREEKARAOKE_LANG", "LANGUAGE", "LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(variable, raising=False)
    for variable, value in environment.items():
        monkeypatch.setenv(variable, value)
    assert system_language() == expected


def english_run(tmp_path, code):
    """Language is fixed when the program loads, so English needs a process of its own."""
    environment = {
        **os.environ,
        "FREEKARAOKE_LANG": "en",
        "QT_QPA_PLATFORM": "offscreen",
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
    }
    process = subprocess.run(
        [sys.executable, "-c", code], cwd=PROJECT, env=environment, capture_output=True, text=True, timeout=90
    )
    assert process.returncode == 0, process.stderr
    return process.stdout


@needs_soundfont
def test_whole_window_shows_english_without_portuguese_left(tmp_path):
    from i18n import TRANSLATIONS

    code = """
from PySide6.QtWidgets import QApplication, QAbstractButton, QLabel, QLineEdit
from PySide6.QtGui import QAction
from player import PlayerWindow
application = QApplication([])
window = PlayerWindow(realtime=False)
window.show()
widgets = [window, window._song_window]
texts = [window.windowTitle(), window._song_window.windowTitle()]
for root in widgets:
    for widget in root.findChildren(QAbstractButton) + root.findChildren(QLabel):
        texts.append(widget.text())
    for widget in root.findChildren(QLineEdit):
        texts.append(widget.placeholderText())
for action in window.findChildren(QAction):
    texts.append(action.text())
for menu_action in window.menuBar().actions():
    texts.append(menu_action.text())
texts.append(window._singer_filter.itemText(0))
window.close()
print("\\n".join(texts))
"""
    shown = set(english_run(tmp_path, code).splitlines())
    assert "All singers" in shown
    assert "&File" in shown
    assert "Song number" in shown
    assert "Light mode" in shown
    leftover = shown & (set(TRANSLATIONS) - {k for k, v in TRANSLATIONS.items() if k == v})
    assert leftover == set()


def test_converter_messages_and_options_in_english(tmp_path):
    source = tmp_path / "song.musicxml"
    source.write_text(score_xml(SUNG_NOTES), encoding="utf-8")
    code = f"""
import conversor
print(conversor.main([{str(source)!r}, "--format", "kar"]))
print(conversor.main([{str(source)!r}, "--format", "kar"]))
"""
    environment_output = english_run(tmp_path, code.replace("print(conversor", "import contextlib,sys\nwith contextlib.redirect_stderr(sys.stdout):\n    print(conversor"))
    assert "Created:" in environment_output
    assert "Error: The output already exists" in environment_output
    assert "--overwrite" in environment_output


def test_pdf_is_in_english_and_regenerated_when_the_language_changes(tmp_path):
    from library import Library

    code = """
from PySide6.QtWidgets import QApplication
from catalog import CatalogEntry
from pathlib import Path
from player import load_bundled_fonts
from song_list_pdf import export_song_list
application = QApplication([])
load_bundled_fonts()
export_song_list([CatalogEntry(1, Path("a.kar"))], {path!r})
""".format(path=str(tmp_path / "list.pdf"))
    english_run(tmp_path, code)
    pages = pdf_pages(tmp_path / "list.pdf")
    assert "Songlist" in pages[0]
    assert "Singer" in pages[0]
    assert "Others" in pages[0]

    folder = tmp_path / "m"
    touch_song(folder, "a.kar")
    library = Library(tmp_path / "n.sqlite")
    library.sync(folder)
    portuguese = library.signature
    library.close()
    change = f"""
from library import Library
library = Library({str(tmp_path / "n.sqlite")!r})
library.sync({str(folder)!r})
print(library.signature)
"""
    assert english_run(tmp_path, change).strip() != portuguese


def guide_song_midi(notes=24):
    """A verse with a melody following the lyrics and an accompaniment that does not."""
    midi = mido.MidiFile(type=1, ticks_per_beat=TPB, charset="latin1")
    words = mido.MidiTrack([mido.MetaMessage("text", text="@KMIDI KARAOKE FILE", time=0)])
    for index in range(notes):
        words.append(mido.MetaMessage("text", text=f" la{index}", time=0 if index == 0 else TPB))
    melody = mido.MidiTrack()
    for _ in range(notes):
        melody.append(mido.Message("note_on", channel=3, note=72, velocity=100, time=0))
        melody.append(mido.Message("note_off", channel=3, note=72, velocity=0, time=TPB))
    accompaniment = mido.MidiTrack()
    accompaniment.append(mido.Message("note_on", channel=0, note=40, velocity=100, time=TPB // 2))
    accompaniment.append(mido.Message("note_off", channel=0, note=40, velocity=0, time=TPB))
    for _ in range(notes // 2):
        accompaniment.append(mido.Message("note_on", channel=0, note=40, velocity=100, time=TPB))
        accompaniment.append(mido.Message("note_off", channel=0, note=40, velocity=0, time=TPB))
    midi.tracks.extend([words, accompaniment, melody])
    return midi


def channels_playing(midi_data):
    channels = set()
    for track in mido.MidiFile(file=io.BytesIO(midi_data)).tracks:
        channels.update(m.channel for m in track if m.type == "note_on" and m.velocity)
    return channels


def test_guide_voice_is_found_by_following_the_lyrics_and_removed(tmp_path):
    song = load_song(write(guide_song_midi(), tmp_path / "x.kar"))

    assert song.has_guide
    assert channels_playing(song.playback_bytes(True)) == {0, 3}
    assert channels_playing(song.playback_bytes(False)) == {0}


def test_removing_the_guide_keeps_the_timing_of_everything_else(tmp_path):
    song = load_song(write(guide_song_midi(), tmp_path / "x.kar"))

    def accompaniment(data):
        notes = []
        for track in mido.MidiFile(file=io.BytesIO(data)).tracks:
            tick = 0
            for message in track:
                tick += message.time
                if message.type in ("note_on", "note_off") and message.channel == 0:
                    notes.append((tick, message.type))
        return notes

    assert accompaniment(song.playback_bytes(False)) == accompaniment(song.playback_bytes(True))


def test_no_guide_when_no_part_follows_the_lyrics(tmp_path):
    midi = guide_song_midi()
    midi.tracks.pop()  # Only the accompaniment remains.
    song = load_song(write(midi, tmp_path / "x.kar"))
    assert not song.has_guide
    assert song.playback_bytes(False) == song.midi_bytes


def test_named_guide_track_needs_less_agreement_than_an_unnamed_one(tmp_path):
    from song import find_guide_part

    def drifting(name):
        midi = guide_song_midi()
        melody = midi.tracks[2]
        if name:
            melody.insert(0, mido.MetaMessage("track_name", name=name, time=0))
        # Every third note lands away from its syllable, leaving the others where they were.
        messages = [m for m in melody if m.type in ("note_on", "note_off")]
        for note_on, note_off in list(zip(messages[0::2], messages[1::2]))[1::3]:
            note_on.time += TPB // 2
            note_off.time -= TPB // 2
        return midi

    def syllables(midi):
        from song import parse_kar_lyrics

        return [e for e in parse_kar_lyrics(midi)[3] if e.kind == SYNC_SYLLABLE]

    unnamed = drifting("")
    named = drifting("Melody")
    assert find_guide_part(unnamed, syllables(unnamed)) is None
    assert find_guide_part(named, syllables(named)) == (2, 3)


def test_drums_are_never_taken_for_the_guide(tmp_path):
    midi = guide_song_midi()
    for message in midi.tracks[2]:
        if message.type in ("note_on", "note_off"):
            message.channel = 9
    assert not load_song(write(midi, tmp_path / "x.kar")).has_guide


def test_score_voice_part_is_recognised_as_the_guide(tmp_path):
    path = tmp_path / "song.musicxml"
    path.write_text(score_xml(SUNG_NOTES * 4), encoding="utf-8")
    assert load_song(path).has_guide


@needs_soundfont
def test_synth_seek_right_after_loading_waits_for_the_file_and_stays_silent_until_then():
    synth = Synth(soundfont, realtime=False, gain=0.5)
    try:
        synth.load(midi_bytes(kar_midi(["\\la", "la"], notes=64)))
        synth.play()
        synth.seek(20 * TPB)
        assert synth.tick == 20 * TPB
        advance(synth, 0.1)
        assert synth.tick == 20 * TPB  # This reading applies the jump.
        advance(synth, 0.5)
        assert synth.tick == pytest.approx(21 * TPB, abs=TPB)
        assert peak(advance(synth, 0.5)) > 100
    finally:
        synth.close()


@needs_soundfont
def test_guide_option_changes_the_music_while_playing_and_keeps_the_position(tmp_path, window):
    write(guide_song_midi(64), tmp_path / "x.kar")
    assert window.open_song(str(tmp_path / "x.kar"))
    assert window._guide_action.isEnabled()
    advance(window._synth, 1.0)
    before = window._synth.tick

    loads = []
    real_load = window._synth.load
    window._synth.load = lambda data: (loads.append(data), real_load(data))
    window._guide_action.setChecked(False)
    advance(window._synth, 0.1)

    assert loads == [window._song.midi_bytes_without_guide]
    assert window._synth.is_playing
    assert window._synth.tick == pytest.approx(before, abs=TPB)
    assert window._settings.value("guide_enabled") == "false"


@needs_soundfont
def test_guide_choice_applies_to_the_next_song_and_is_disabled_without_a_guide(tmp_path, window):
    write(guide_song_midi(), tmp_path / "a.kar")
    midi = guide_song_midi()
    midi.tracks.pop()
    write(midi, tmp_path / "b.kar")
    window._guide_action.setChecked(False)
    loads = []
    assert window.open_song(str(tmp_path / "a.kar"), autoplay=False)
    assert window._guide_action.isEnabled()
    assert channels_playing(window._song.playback_bytes(window._guide_action.isChecked())) == {0}
    assert window.open_song(str(tmp_path / "b.kar"), autoplay=False)
    assert not window._guide_action.isEnabled()
    assert not loads


def voice_block(seconds=0.05, rate=48000, amplitude=0.2):
    import numpy as np

    count = int(seconds * rate)
    return (amplitude * np.sin(2 * np.pi * 440 * np.arange(count) / rate)).astype(np.float32)


def read_audio(path):
    import soundfile

    return soundfile.read(path, always_2d=True)


@needs_soundfont
def test_recording_mixes_voice_and_music_into_opus(tmp_path):
    import numpy as np
    import soundfile
    from recorder import OUTPUT_RATE, Recording

    song = load_song(write(kar_midi(["\\la", "la"], notes=16), tmp_path / "x.kar"))
    take = Recording("Teste", song.ticks_per_beat, soundfont)
    block = voice_block()
    tick_step = round(0.05 * 2 * TPB)
    for index in range(100):  # Five seconds.
        take.add(block, OUTPUT_RATE, index * tick_step, 0.5, 1.0, song.midi_bytes)
    target = tmp_path / "saida.opus"
    take.save(target)

    audio, rate = read_audio(target)
    info = soundfile.info(target)
    assert (rate, info.channels, info.subtype, info.format) == (48000, 2, "OPUS", "OGG")
    assert len(audio) == pytest.approx(5 * 48000, abs=4800)
    spectrum = np.abs(np.fft.rfft(audio[48000:144000, 0]))
    frequencies = np.fft.rfftfreq(96000, 1 / 48000)
    # The voice's 440 Hz and the music's middle C (262 Hz) are both there.
    assert spectrum[np.argmin(np.abs(frequencies - 440))] > spectrum.mean() * 20
    assert spectrum[(frequencies > 255) & (frequencies < 268)].max() > spectrum.mean() * 5
    assert np.abs(audio).max() <= 1.0


@needs_soundfont
def test_recording_of_voice_only_when_music_is_silent_starts_at_the_right_moment(tmp_path):
    import numpy as np
    from recorder import OUTPUT_RATE, VOICE_LEAD_SECONDS, Recording

    song = load_song(write(kar_midi(["\\la", "la"], notes=4), tmp_path / "x.kar"))
    take = Recording("Teste", song.ticks_per_beat, soundfont)
    silence = np.zeros(2400, dtype=np.float32)
    for index in range(60):
        take.add(voice_block(amplitude=0.5) if index >= 30 else silence, OUTPUT_RATE, 0, 0.0, 1.0, song.midi_bytes)
    target = tmp_path / "saida.opus"
    take.save(target)

    audio, rate = read_audio(target)
    energy = np.abs(audio[:, 0])
    first = np.argmax(energy > 0.1) / rate
    assert first == pytest.approx(1.5 - VOICE_LEAD_SECONDS, abs=0.1)


@needs_soundfont
def test_recording_accepts_a_microphone_at_another_rate(tmp_path):
    from recorder import Recording

    song = load_song(write(kar_midi(["\\la", "la"], notes=8), tmp_path / "x.kar"))
    take = Recording("Teste", song.ticks_per_beat, soundfont)
    block = voice_block(rate=44100)
    for index in range(40):
        take.add(block, 44100, index * 10, 0.3, 1.0, song.midi_bytes)
    target = tmp_path / "saida.opus"
    take.save(target)
    audio, rate = read_audio(target)
    assert rate == 48000
    assert len(audio) == pytest.approx(2 * 48000, abs=4800)


def test_recording_limiter_keeps_peaks_in_range():
    import numpy as np
    from recorder import _limit

    loud = np.array([[0.5, -0.5], [0.95, -1.8], [3.0, 0.1]], dtype=np.float32)
    limited = _limit(loud)
    assert limited[0].tolist() == [0.5, -0.5]
    assert np.abs(limited).max() <= 1.0
    assert limited[1, 1] < 0 and limited[2, 0] > limited[1, 0]


def test_recording_names_are_valid_and_never_replace_a_file(tmp_path):
    import datetime

    from recorder import recording_path

    moment = datetime.datetime(2026, 11, 2, 20, 30, 15)
    first = recording_path(tmp_path, 'Canção: "a/b"?', moment)
    assert first.name == "Canção a b 2026-11-02 20-30-15.opus"
    first.write_bytes(b"x")
    assert recording_path(tmp_path, 'Canção: "a/b"?', moment).name == "Canção a b 2026-11-02 20-30-15 2.opus"
    assert recording_path(tmp_path, "...", moment).name.startswith("FreeKaraoke ")


def test_too_short_recording_is_refused(tmp_path):
    from recorder import Recording

    take = Recording("Teste", TPB, "nao-usado.sf2")
    with pytest.raises(ValueError):
        take.save(tmp_path / "x.opus")
    assert not (tmp_path / "x.opus").exists()


@pytest.fixture
def recordings(tmp_path, monkeypatch):
    import player

    folder = tmp_path / "gravacoes"
    monkeypatch.setattr(player, "recordings_folder", lambda: folder)
    # Answering the question with the name the program suggests.
    monkeypatch.setattr(player.QFileDialog, "getSaveFileName", lambda _parent, _title, name, _filter: (name, ""))
    return folder


def wait_for_saved(window, qapp):
    assert wait_until(qapp, lambda: not any(saver.is_alive() for saver in window._savers), 40)
    qapp.processEvents()


def feed_voice(window, blocks=40):
    for _ in range(blocks):
        window.on_microphone_samples(voice_block(), 48000)
        advance(window._synth, 0.05)


@needs_soundfont
def test_record_button_saves_each_song_to_its_own_file_with_a_visible_mark(tmp_path, window, qapp, recordings):
    write(kar_midi(["\\la", "la"], notes=64), tmp_path / "a.kar")
    write(kar_midi(["\\la", "la"], notes=64), tmp_path / "b.kar")

    window._record_button.setChecked(True)
    assert not window._lyrics.is_showing_recording()
    assert "começa" in window._song_message.text()

    window.open_song(str(tmp_path / "a.kar"))
    assert window._lyrics.is_showing_recording()
    feed_voice(window)
    window.open_song(str(tmp_path / "b.kar"))
    assert window._lyrics.is_showing_recording()
    feed_voice(window)
    window.stop()
    assert not window._lyrics.is_showing_recording()
    wait_for_saved(window, qapp)

    files = sorted(recordings.glob("*.opus"))
    assert len(files) == 2
    assert "Gravação salva" in window._song_message.text()
    assert all(len(read_audio(path)[0]) > 48000 for path in files)
    assert not list(recordings.glob(".*"))


@needs_soundfont
def test_turning_recording_off_mid_song_keeps_what_was_sung_and_song_keeps_playing(tmp_path, window, qapp, recordings):
    write(kar_midi(["\\la", "la"], notes=64), tmp_path / "a.kar")
    window.open_song(str(tmp_path / "a.kar"))
    window._record_button.setChecked(True)
    assert window._lyrics.is_showing_recording()
    feed_voice(window)
    window._record_button.setChecked(False)
    assert not window._lyrics.is_showing_recording()
    assert window._synth.is_playing
    wait_for_saved(window, qapp)
    assert len(list(recordings.glob("*.opus"))) == 1

    window.on_microphone_samples(voice_block(), 48000)  # Off means nothing more is kept.
    assert window._recording is None


@needs_soundfont
def test_nothing_is_recorded_unless_the_button_is_on(tmp_path, window, qapp, recordings):
    write(kar_midi(["\\la", "la"], notes=16), tmp_path / "a.kar")
    window.open_song(str(tmp_path / "a.kar"))
    feed_voice(window, 30)
    window.stop()
    assert window._recording is None
    assert not recordings.exists()


@needs_soundfont
def test_recording_too_short_leaves_no_file_and_paused_audio_is_not_kept(tmp_path, window, qapp, recordings):
    write(kar_midi(["\\la", "la"], notes=16), tmp_path / "a.kar")
    window._record_button.setChecked(True)
    window.open_song(str(tmp_path / "a.kar"))
    window.toggle_play()
    window.on_microphone_samples(voice_block(), 48000)
    assert window._recording.seconds == 0
    window.stop()
    wait_for_saved(window, qapp)
    assert not recordings.exists()


@needs_soundfont
def test_closing_the_program_finishes_saving_the_recording(tmp_path, qapp, recordings):
    from player import PlayerWindow

    write(kar_midi(["\\la", "la"], notes=64), tmp_path / "a.kar")
    window = PlayerWindow(soundfont, realtime=False)
    window._record_button.setChecked(True)
    window.open_song(str(tmp_path / "a.kar"))
    feed_voice(window)
    window.close()
    assert len(list(recordings.glob("*.opus"))) == 1


def test_microphone_meter_hands_over_mono_audio_scaled_to_one(qapp):
    import numpy as np
    from PySide6.QtMultimedia import QAudioFormat
    from score import FRAME_MS, MicrophoneMeter

    meter = MicrophoneMeter()
    captured = []
    meter.samples_captured.connect(lambda samples, rate: captured.append((samples, rate)))
    meter._configure(QAudioFormat.SampleFormat.Int16, 48000, 2)
    stereo = np.tile(np.array([16384, 0], dtype="<i2"), 48000 * FRAME_MS // 1000)
    meter.consume(stereo.tobytes())
    samples, rate = captured[0]
    assert rate == 48000
    assert len(samples) == 48000 * FRAME_MS // 1000
    assert samples[0] == pytest.approx(0.25, abs=0.001)


def test_microphone_already_listening_for_the_score_is_not_opened_again(qapp):
    from PySide6.QtMultimedia import QAudio
    from score import MicrophoneMeter

    class RunningSource:
        def state(self):
            return QAudio.State.ActiveState

        def start(self):
            raise AssertionError("Qt refuses to start a source twice")

    meter = MicrophoneMeter()
    meter._source = RunningSource()
    meter._device = object()
    assert meter.start() is True
    meter._device = None
    meter._source = None


def test_record_button_is_a_red_circle_with_only_an_icon(window, qapp):
    from PySide6.QtGui import QColor

    button = window._record_button
    assert button.text() == ""
    image = button.icon().pixmap(32, 32).toImage()
    assert image.pixelColor(16, 16) == QColor("#dc3545")
    assert image.pixelColor(1, 1).alpha() == 0
    assert "Gravar" in button.toolTip()

    button.setChecked(True)
    assert button.icon().pixmap(32, 32).toImage().pixelColor(16, 16) == QColor("#ffffff")
    assert "Parar" in button.toolTip()


@needs_soundfont
def test_pressing_record_while_a_song_plays_starts_recording_with_the_microphone_on(tmp_path, window, qapp, recordings, monkeypatch):
    write(kar_midi(["\\la", "la"], notes=64), tmp_path / "a.kar")
    window._realtime = True  # Only the microphone behaves as in real use here.
    started = []
    monkeypatch.setattr(window._microphone, "start", lambda: (started.append(1), True)[1])
    window.open_song(str(tmp_path / "a.kar"))
    window._record_button.setChecked(True)
    assert window._record_button.isChecked()
    assert window._lyrics.is_showing_recording()
    assert "microfone" not in window.statusBar().currentMessage()
    window._realtime = False
    window._detach_recording().discard()


def record_a_song(window, tmp_path):
    write(kar_midi(["\\la", "la"], notes=64), tmp_path / "a.kar")
    window._record_button.setChecked(True)
    window.open_song(str(tmp_path / "a.kar"))
    feed_voice(window)


@needs_soundfont
def test_asks_the_file_name_when_the_song_stops_and_uses_it(tmp_path, window, qapp, recordings, monkeypatch):
    import player

    asked = []
    chosen = tmp_path / "escolhida"
    chosen.mkdir()

    def answer(_parent, title, suggested, file_filter):
        asked.append((title, suggested, file_filter))
        return str(chosen / "minha voz"), ""

    monkeypatch.setattr(player.QFileDialog, "getSaveFileName", answer)
    record_a_song(window, tmp_path)
    assert asked == []  # Not before the end.
    window.stop()
    wait_for_saved(window, qapp)

    title, suggested, file_filter = asked[0]
    assert title == "Salvar a gravação"
    assert suggested.endswith(".opus") and "Minha canção" in suggested
    assert "*.opus" in file_filter
    assert (chosen / "minha voz.opus").is_file()  # The extension is added when left out.
    assert window._settings.value("recordings_folder") == str(chosen)
    assert not recordings.exists()

    assert str(window._recordings_folder()) == str(chosen)


@needs_soundfont
def test_cancelling_the_name_confirms_before_discarding(tmp_path, window, qapp, recordings, monkeypatch):
    import player
    from PySide6.QtWidgets import QMessageBox

    # Cancelling twice, declining to discard each time, then choosing a name.
    names = iter([("", ""), ("", ""), (str(tmp_path / "salva.opus"), "")])
    monkeypatch.setattr(player.QFileDialog, "getSaveFileName", lambda *_args: next(names))
    monkeypatch.setattr(player.QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.Cancel)
    record_a_song(window, tmp_path)
    window.stop()
    wait_for_saved(window, qapp)
    assert (tmp_path / "salva.opus").is_file()


@needs_soundfont
def test_confirmed_discard_saves_nothing(tmp_path, window, qapp, recordings, monkeypatch):
    import player
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(player.QFileDialog, "getSaveFileName", lambda *_args: ("", ""))
    monkeypatch.setattr(player.QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.Discard)
    record_a_song(window, tmp_path)
    window.stop()
    assert window._savers == []
    assert not recordings.exists()


@needs_soundfont
def test_at_the_end_of_a_song_the_score_and_next_song_wait_for_the_answer(tmp_path, window, qapp, recordings, monkeypatch):
    import player

    folder = song_folder(tmp_path, ["a.kar", "b.kar"])
    window.set_song_folder(str(folder))
    window.add_to_queue(window._catalog[0])
    window.add_to_queue(window._catalog[1])
    window._record_button.setChecked(True)
    window.play_queue()
    feed_voice(window, 30)

    moments = []

    def answer(_parent, _title, suggested, _filter):
        moments.append((window._lyrics.score(), window._score_timer.isActive(), window._song.path.stem))
        return suggested, ""

    monkeypatch.setattr(player.QFileDialog, "getSaveFileName", answer)
    for syllable in window._song.timeline.syllables:
        for offset in range(0, 40):
            window._scorer.add_frame(syllable.tick + offset, True)
    for frame in range(30):
        window._scorer.add_frame(100_000 + frame, False)
    advance(window._synth, 8.0)
    window._refresh()

    assert moments == [(100, False, "a")]  # Shown behind the question, its time not yet running.
    assert window._score_timer.isActive()
    assert window._song.path.stem == "a"
    wait_for_saved(window, qapp)
    window._score_timer.stop()
    window._score_timer.timeout.emit()
    assert window._song.path.stem == "b"
    window._record_button.setChecked(False)
    window.stop()


@needs_soundfont
def test_next_song_waits_for_the_answer_about_the_last_recording(tmp_path, window, qapp, recordings, monkeypatch):
    import player

    write(kar_midi(["\\b", "la"], notes=8), tmp_path / "b.kar")
    record_a_song(window, tmp_path)
    moments = []

    def answer(_parent, _title, suggested, _filter):
        moments.append((window._song.path.stem, window._synth.is_playing))
        return suggested, ""

    monkeypatch.setattr(player.QFileDialog, "getSaveFileName", answer)
    window.open_song(str(tmp_path / "b.kar"), autoplay=False)
    assert moments == [("a", False)]
    assert window._song.path.stem == "b"
    wait_for_saved(window, qapp)
    window._record_button.setChecked(False)
