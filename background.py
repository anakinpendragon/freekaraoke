"""Rotates images and videos from a folder behind the lyrics. Videos play muted."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, QTimer, QUrl, Signal
from PySide6.QtGui import QImage
from PySide6.QtMultimedia import QMediaPlayer, QVideoSink

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp"}
VIDEO_SUFFIXES = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".mpg", ".mpeg", ".wmv"}
IMAGE_SECONDS = 15
RETRY_MS = 200


def scan_media(folder: str | Path) -> list[Path]:
    root = Path(folder)
    suffixes = IMAGE_SUFFIXES | VIDEO_SUFFIXES
    paths = [path for path in root.rglob("*") if path.suffix.lower() in suffixes and path.is_file()]
    return sorted(paths, key=lambda path: str(path).casefold())


class BackgroundRotator(QObject):
    frame_ready = Signal(QImage)
    cleared = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._files: list[Path] = []
        self._index = -1
        self._failures = 0
        self._running = False
        self._showing_video = False

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._advance)

        # With no QAudioOutput attached, a QMediaPlayer produces no sound at all.
        self._player = QMediaPlayer(self)
        self._sink = QVideoSink(self)
        self._player.setVideoSink(self._sink)
        self._sink.videoFrameChanged.connect(self._on_video_frame)
        self._player.mediaStatusChanged.connect(self._on_media_status)
        self._player.errorOccurred.connect(self._on_error)

    @property
    def has_media(self) -> bool:
        return bool(self._files)

    def set_folder(self, folder: str | Path | None) -> int:
        """Use the folder's media, or none; returns how many files were found."""
        self.stop()
        self._files = scan_media(folder) if folder else []
        self._index = -1
        self.cleared.emit()
        return len(self._files)

    def start(self) -> None:
        if not self._files or self._running:
            return
        self._running = True
        self._failures = 0
        if self._showing_video:
            self._player.play()
        elif self._timer.remainingTime() <= 0:
            self._advance()
        else:
            self._timer.start()

    def pause(self) -> None:
        self._running = False
        self._timer.stop()
        if self._showing_video:
            self._player.pause()

    def stop(self) -> None:
        self._running = False
        self._timer.stop()
        self._player.stop()
        self._showing_video = False

    def _advance(self) -> None:
        if not self._running or not self._files:
            return
        if self._failures >= len(self._files):
            # Nothing in the folder can be shown; do not retry forever.
            self._running = False
            return
        self._index = (self._index + 1) % len(self._files)
        path = self._files[self._index]
        if path.suffix.lower() in VIDEO_SUFFIXES:
            self._show_video(path)
        else:
            self._show_image(path)

    def _show_image(self, path: Path) -> None:
        self._player.stop()
        self._showing_video = False
        image = QImage(str(path))
        if image.isNull():
            self._failures += 1
            self._timer.start(RETRY_MS)
            return
        self._failures = 0
        self.frame_ready.emit(image)
        self._timer.start(IMAGE_SECONDS * 1000)

    def _show_video(self, path: Path) -> None:
        self._showing_video = True
        self._player.setSource(QUrl.fromLocalFile(str(path)))
        self._player.play()

    def _on_video_frame(self, frame) -> None:
        if not self._showing_video:
            return
        image = frame.toImage()
        if not image.isNull():
            self._failures = 0
            self.frame_ready.emit(image)

    def _on_media_status(self, status) -> None:
        if status == QMediaPlayer.MediaStatus.EndOfMedia and self._running:
            self._advance()
        elif status == QMediaPlayer.MediaStatus.InvalidMedia:
            self._skip_failed_video()

    def _on_error(self, error, _message) -> None:
        if error != QMediaPlayer.Error.NoError:
            self._skip_failed_video()

    def _skip_failed_video(self) -> None:
        if self._showing_video and self._running and not self._timer.isActive():
            self._failures += 1
            self._showing_video = False
            self._timer.start(RETRY_MS)
