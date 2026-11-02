import io
import sys
from pathlib import Path

import mido
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import app  # noqa: E402
from kar import (  # noqa: E402
    ConversionError,
    Syllable,
    _karaoke_events,
    _words_track,
    convert,
)


def note(step, lyrics="", duration=4):
    """lyrics: (verse, syllabic, text) tuples."""
    body = "".join(
        f'<lyric number="{verse}"><syllabic>{syllabic}</syllabic><text>{text}</text></lyric>'
        for verse, syllabic, text in lyrics
    )
    return (
        f"<note><pitch><step>{step}</step><octave>4</octave></pitch>"
        f"<duration>{duration}</duration><type>quarter</type>{body}</note>"
    )


def musicxml(measures):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<score-partwise version="3.1"><work><work-title>Canção</work-title></work>'
        '<part-list><score-part id="P1"><part-name>Voz</part-name></score-part></part-list>'
        f'<part id="P1">{"".join(measures)}</part></score-partwise>'
    )


ATTRIBUTES = (
    "<attributes><divisions>4</divisions><time><beats>2</beats>"
    "<beat-type>4</beat-type></time></attributes>"
)


def lyric_events(midi):
    words = next(t for t in midi.tracks if t.name == "Words")
    tick = 0
    events = []
    for message in words:
        tick += message.time
        if message.type == "text" and not message.text.startswith("@"):
            events.append((tick, message.text))
    return events


def note_on_ticks(midi):
    ticks = []
    for track in midi.tracks:
        tick = 0
        for message in track:
            tick += message.time
            if message.type == "note_on" and message.velocity:
                ticks.append(tick)
    return sorted(ticks)


def reload(midi):
    buffer = io.BytesIO()
    midi.save(file=buffer)
    buffer.seek(0)
    return mido.MidiFile(file=buffer, charset="latin1")


def test_musicxml_syllables_words_and_accents(tmp_path):
    path = tmp_path / "song.musicxml"
    path.write_text(
        musicxml(
            [
                f'<measure number="1">{ATTRIBUTES}'
                + note("C", [(1, "begin", "Can")])
                + note("D", [(1, "end", "ção")])
                + "</measure>",
                '<measure number="2">'
                + note("E", [(1, "single", "de")])
                + note("F", [(1, "single", "amor")])
                + "</measure>",
            ]
        ),
        encoding="utf-8",
    )

    midi, title = convert(path)
    midi = reload(midi)

    assert title == "Canção"
    tpb = midi.ticks_per_beat
    assert lyric_events(midi) == [
        (0, "\\Can"),
        (tpb, "ção"),
        (2 * tpb, " de"),
        (3 * tpb, " amor"),
    ]
    assert note_on_ticks(midi) == [0, tpb, 2 * tpb, 3 * tpb]
    all_text = [m.text for t in midi.tracks for m in t if m.type == "text"]
    assert "@KMIDI KARAOKE FILE" in all_text
    assert "@TCanção" in all_text


def test_repeats_sing_each_verse_in_time_with_notes(tmp_path):
    path = tmp_path / "song.musicxml"
    path.write_text(
        musicxml(
            [
                f'<measure number="1">{ATTRIBUTES}'
                + note("C", [(1, "single", "um"), (2, "single", "dois")])
                + note("D", [(1, "single", "tres"), (2, "single", "quatro")])
                + '<barline location="right"><repeat direction="backward"/></barline>'
                + "</measure>",
                '<measure number="2">'
                + note("E", [(1, "single", "fim")])
                + note("F")
                + "</measure>",
            ]
        ),
        encoding="utf-8",
    )

    midi, _ = convert(path)
    midi = reload(midi)

    events = lyric_events(midi)
    tpb = midi.ticks_per_beat
    assert [text.strip("\\ /") for _, text in events] == ["um", "tres", "dois", "quatro", "fim"]
    assert [tick for tick, _ in events] == [0, tpb, 2 * tpb, 3 * tpb, 4 * tpb]
    assert note_on_ticks(midi) == [i * tpb for i in range(6)]


def test_pickup_measure_stays_aligned(tmp_path):
    path = tmp_path / "song.musicxml"
    path.write_text(
        musicxml(
            [
                f'<measure number="0" implicit="yes">{ATTRIBUTES}'
                + note("C", [(1, "single", "a")])
                + "</measure>",
                '<measure number="1">'
                + note("D", [(1, "single", "b")])
                + note("E", [(1, "single", "c")])
                + "</measure>",
            ]
        ),
        encoding="utf-8",
    )

    midi, _ = convert(path)
    midi = reload(midi)

    assert [tick for tick, _ in lyric_events(midi)] == note_on_ticks(midi)


def test_musicxml_output_uses_a_resolution_old_players_handle(tmp_path):
    path = tmp_path / "song.musicxml"
    path.write_text(
        musicxml(
            [
                f'<measure number="1">{ATTRIBUTES}'
                + note("C", [(1, "single", "um")])
                + note("D", [(1, "single", "dois")])
                + "</measure>"
            ]
        ),
        encoding="utf-8",
    )

    midi, _ = convert(path)

    assert reload(midi).ticks_per_beat == 480


def test_cli_creates_kar_next_to_input(tmp_path, capsys):
    from conversor import main

    path = tmp_path / "song.musicxml"
    path.write_text(
        musicxml(
            [
                f'<measure number="1">{ATTRIBUTES}'
                + note("C", [(1, "single", "um")])
                + note("D", [(1, "single", "dois")])
                + "</measure>"
            ]
        ),
        encoding="utf-8",
    )

    assert main([str(path), "--formato", "kar"]) == 0

    output = tmp_path / "song.kar"
    assert lyric_events(mido.MidiFile(str(output), charset="latin1"))[0][1] == "\\um"
    assert "song.kar" in capsys.readouterr().out


def test_cli_does_not_overwrite_without_permission(tmp_path, capsys):
    from conversor import main

    path = tmp_path / "song.musicxml"
    path.write_text(
        musicxml([f'<measure number="1">{ATTRIBUTES}' + note("C", [(1, "single", "um")]) + "</measure>"]),
        encoding="utf-8",
    )
    existing = tmp_path / "song.st3"
    existing.write_bytes(b"meu arquivo")

    assert main([str(path)]) == 1
    assert existing.read_bytes() == b"meu arquivo"
    assert "sobrescrever" in capsys.readouterr().err

    assert main([str(path), "--sobrescrever"]) == 0
    assert existing.read_bytes().startswith(b"STAR DATA V3.00")


@pytest.mark.parametrize("name", ["falta.musicxml", "partitura.pdf"])
def test_cli_rejects_missing_or_wrong_input(tmp_path, name, capsys):
    from conversor import main

    path = tmp_path / name
    if name.endswith(".pdf"):
        path.write_bytes(b"x")
    assert main([str(path)]) == 1
    assert "Erro:" in capsys.readouterr().err


def midi_with_lyrics(texts):
    midi = mido.MidiFile(type=1, ticks_per_beat=480)
    track = mido.MidiTrack()
    midi.tracks.append(track)
    for text in texts:
        track.append(mido.MetaMessage("lyrics", text=text, time=0))
        track.append(mido.Message("note_on", note=60, velocity=80, time=0))
        track.append(mido.Message("note_off", note=60, velocity=0, time=480))
    return midi


def test_midi_lyrics_with_hyphens(tmp_path):
    path = tmp_path / "song.mid"
    midi_with_lyrics(["Can-", "tar", "bem"]).save(str(path))

    midi, _ = convert(path)
    midi = reload(midi)

    assert lyric_events(midi) == [(0, "\\Can"), (480, "tar"), (960, " bem")]
    assert not any(m.type == "lyrics" for t in midi.tracks for m in t)


def test_typographic_apostrophe_survives_latin1():
    syllable = Syllable(tick=0, end=480, text="d’água")
    assert _karaoke_events([syllable], 480) == [(0, "\\d'água")]


def test_artist_without_title_keeps_title_slot():
    track = _words_track([Syllable(tick=0, end=480, text="la")], "", "Autor", 480)
    texts = [m.text for m in track if m.type == "text"]
    assert texts[:3] == ["@LPORT", "@T", "@TAutor"]


def test_midi_without_lyrics_is_rejected(tmp_path):
    path = tmp_path / "song.mid"
    midi_with_lyrics([]).save(str(path))
    with pytest.raises(ConversionError):
        convert(path)


def test_long_text_breaks_lines_and_screens(tmp_path):
    path = tmp_path / "song.mid"
    midi_with_lyrics(["palavra-grande "] * 30).save(str(path))

    midi, _ = convert(path)
    texts = [text for _, text in lyric_events(reload(midi))]

    assert any(t.startswith("/") for t in texts)
    assert sum(t.startswith("\\") for t in texts) > 1


@pytest.fixture
def client():
    return app.test_client()


def test_route_returns_kar_named_after_score_id(client, tmp_path):
    path = tmp_path / "song.mid"
    midi_with_lyrics(["la "]).save(str(path))

    response = client.post(
        "/converter",
        data={
            "url": "https://musescore.com/user/32136986/scores/8263751",
            "formato": "kar",
            "arquivo": (path.open("rb"), "song.mid"),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    assert "musescore-8263751.kar" in response.headers["Content-Disposition"]
    assert mido.MidiFile(file=io.BytesIO(response.data)).type == 1


@pytest.mark.parametrize(
    "url",
    ["https://evil.example/scores/1", "http://musescore.com/user/1/scores/2"],
)
def test_route_rejects_other_urls(client, url, tmp_path):
    path = tmp_path / "song.mid"
    midi_with_lyrics(["la "]).save(str(path))
    response = client.post(
        "/converter",
        data={"url": url, "arquivo": (path.open("rb"), "song.mid")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 400


def test_route_rejects_unsupported_extension(client):
    response = client.post(
        "/converter",
        data={"arquivo": (io.BytesIO(b"x"), "song.pdf")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 400


def test_route_reports_missing_lyrics(client, tmp_path):
    path = tmp_path / "song.mid"
    midi_with_lyrics([]).save(str(path))
    response = client.post(
        "/converter",
        data={"arquivo": (path.open("rb"), "song.mid")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 422


def test_route_creates_st3_by_default(client, tmp_path):
    path = tmp_path / "song.mid"
    midi_with_lyrics(["la ", "la "]).save(str(path))
    response = client.post(
        "/converter",
        data={"arquivo": (path.open("rb"), "song.mid")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 200
    assert ".st3" in response.headers["Content-Disposition"]
    assert response.data.startswith(b"STAR DATA V3.00")


def test_route_rejects_unknown_output_format(client, tmp_path):
    path = tmp_path / "song.mid"
    midi_with_lyrics(["la "]).save(str(path))
    response = client.post(
        "/converter",
        data={"formato": "pdf", "arquivo": (path.open("rb"), "song.mid")},
        content_type="multipart/form-data",
    )
    assert response.status_code == 400
