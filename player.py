"""Karaoke player: numbered song list, queue, colour-wipe lyrics and rotating backgrounds."""

from __future__ import annotations

from i18n import LANGUAGE, PORTUGUESE, tr

import math
import os
import shutil
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import (
    QLibraryInfo,
    QLocale,
    QMargins,
    QPoint,
    QPointF,
    QRectF,
    QSettings,
    Qt,
    QTimer,
    QTranslator,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QColor,
    QDesktopServices,
    QFont,
    QFontDatabase,
    QFontMetricsF,
    QIcon,
    QImage,
    QKeySequence,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QCompleter,
    QDialog,
    QFileDialog,
    QFontDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from background import BackgroundRotator
from catalog import OTHERS, CatalogEntry, singer_key, singer_names
from library import Library
from score import MicrophoneMeter, MusicEnvelopeWorker, RhythmScorer
from song import MUSICXML_SUFFIXES, LineState, Song, SongError, is_prepared_score, load_song
from song_list_pdf import export_song_list
from synth import Synth, SynthError, find_soundfont
from theme import DARK, LIGHT, THEMES, media_icon, style_sheet

REFRESH_MS = 16
MESSAGE_MS = 5000
TYPED_NUMBER_MS = 10000
SCORE_DISPLAY_MS = 8000
INTERFACE_FONT_MIN = 8
INTERFACE_FONT_MAX = 32
FALLBACK_INTERFACE_FONT = 10
CONTROLS_WIDTH_PER_POINT = 80  # Measured: all controls in one row need about this per point.
DEFAULT_VOLUME = 50
VOLUME_STEP = 5
FULL_VOLUME_GAIN = 1.0
SCORE_FONT_SIZE = 110
MAX_TYPED_DIGITS = 6
SEEK_STEP_SECONDS = 5
TEMPO_STEP = 0.05
TEMPO_MIN = 0.5
TEMPO_MAX = 1.5
FILE_FILTER = (
    tr("Músicas de karaokê (*.st3 *.ST3 *.St3 *.kar *.KAR *.mid *.MID *.midi *.musicxml *.mxl *.xml);;Todos os arquivos (*)")
)

BACKGROUND = QColor("#10131a")
UNSUNG = QColor("#ffffff")
SUNG = QColor("#ffd400")
OUTLINE = QColor("#000000")
# Keep lyrics readable over any image: a soft dark halo, then a drop shadow.
HALO_LAYERS = ((0.26, 45), (0.16, 70))  # (width relative to the font size, opacity)
SHADOW_OFFSET = 0.05
SHADOW_OPACITY = 190
SPRITE_MARGIN = 0.25  # Room around the text for its shadow, relative to the font size.
MAX_CACHED_SPRITES = 12
NOTICE_BACKGROUND = QColor(0, 0, 0, 190)
NOTICE_FONT_SIZE = 20

FONT_DIRECTORY = Path(__file__).resolve().parent / "fonts"
DEFAULT_FONT_FAMILY = "Liberation Sans"
DEFAULT_FONT_SIZE = 60
MIN_FONT_SIZE = 12
MAX_FONT_SIZE = 200
FONT_SIZE_STEP = 4
LINE_SPACING = 1.1
BOTTOM_MARGIN = 0.05
ALL_SINGERS = tr("Todos os cantores")
ICON_PATH = Path(__file__).resolve().parent / "icons" / "freekaraoke.png"
# Saved to $XDG_CONFIG_HOME/freekaraoke/freekaraoke.ini, normally under ~/.config.
SETTINGS_DIRECTORY = "freekaraoke"
SETTINGS_NAME = "freekaraoke"
LIBRARY_FILE = "freekaraoke.sqlite"
SONG_LIST_PDF = "lista-de-musicas.pdf"


def open_settings() -> QSettings:
    return QSettings(
        QSettings.Format.IniFormat, QSettings.Scope.UserScope, SETTINGS_DIRECTORY, SETTINGS_NAME
    )


def load_bundled_fonts() -> None:
    for path in sorted(FONT_DIRECTORY.glob("*.ttf")):
        QFontDatabase.addApplicationFont(str(path))


def default_lyric_font() -> QFont:
    font = QFont(DEFAULT_FONT_FAMILY, DEFAULT_FONT_SIZE)
    font.setBold(True)
    return font


@dataclass(frozen=True)
class LineSprite:
    """A lyric line drawn once in each colour, with its shadow."""

    sung: QPixmap
    unsung: QPixmap
    margin: float
    ascent: float


class LyricWidget(QWidget):
    """Lyrics held together at the bottom, over the background image or video."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._upper = LineState()
        self._lower = LineState()
        self._font = default_lyric_font()
        self._background: QImage | None = None
        self._notice = ""
        self._score: int | None = None
        self._sprites: dict[tuple, LineSprite] = {}
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumHeight(160)

    def show_lines(self, upper: LineState, lower: LineState) -> None:
        if (upper, lower) != (self._upper, self._lower):
            self._upper, self._lower = upper, lower
            self.update()

    def set_lyric_font(self, font: QFont) -> None:
        self._font = QFont(font)
        self.update()

    def lyric_font(self) -> QFont:
        return QFont(self._font)

    def set_background(self, image: QImage) -> None:
        self._background = image
        self.update()

    def clear_background(self) -> None:
        self._background = None
        self.update()

    def set_score(self, score: int | None) -> None:
        self._score = score
        self.update()

    def score(self) -> int | None:
        return self._score

    def set_notice(self, text: str) -> None:
        self._notice = text
        self.update()

    def notice(self) -> str:
        return self._notice

    def line_baselines(self) -> tuple[float, float]:
        metrics = QFontMetricsF(self._font)
        lower = self.height() * (1 - BOTTOM_MARGIN) - metrics.descent()
        return lower - metrics.height() * LINE_SPACING, lower

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.fillRect(self.rect(), BACKGROUND)
        if self._background is not None:
            self._paint_background(painter)
        if self._score is not None:
            self._paint_score(painter)
        else:
            upper_baseline, lower_baseline = self.line_baselines()
            self._paint_line(painter, self._upper, upper_baseline)
            self._paint_line(painter, self._lower, lower_baseline)
        if self._notice:
            self._paint_notice(painter)

    @staticmethod
    def _paint_shadow(painter: QPainter, path: QPainterPath, font_size: float) -> None:
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for width, opacity in HALO_LAYERS:
            colour = QColor(OUTLINE)
            colour.setAlpha(opacity)
            pen = QPen(colour, font_size * width)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.drawPath(path)
        colour = QColor(OUTLINE)
        colour.setAlpha(SHADOW_OPACITY)
        offset = font_size * SHADOW_OFFSET
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(colour)
        painter.drawPath(path.translated(offset, offset))

    def _paint_score(self, painter: QPainter) -> None:
        center_y = self.height() / 2
        label_font = QFont(DEFAULT_FONT_FAMILY, SCORE_FONT_SIZE // 3)
        label_font.setBold(True)
        number_font = QFont(DEFAULT_FONT_FAMILY, SCORE_FONT_SIZE)
        number_font.setBold(True)
        for text, font, baseline, colour in (
            (tr("Sua nota"), label_font, center_y - QFontMetricsF(number_font).height() / 2, UNSUNG),
            (str(self._score), number_font, center_y + QFontMetricsF(number_font).height() / 2, SUNG),
        ):
            metrics = QFontMetricsF(font)
            path = QPainterPath()
            path.addText((self.width() - metrics.horizontalAdvance(text)) / 2, baseline, font, text)
            self._paint_shadow(painter, path, font.pointSizeF())
            painter.setPen(QPen(OUTLINE, max(2.0, font.pointSizeF() / 18)))
            painter.setBrush(colour)
            painter.drawPath(path)

    def _paint_notice(self, painter: QPainter) -> None:
        font = QFont(DEFAULT_FONT_FAMILY, NOTICE_FONT_SIZE)
        font.setBold(True)
        painter.setFont(font)
        metrics = QFontMetricsF(font)
        padding = metrics.height() / 2
        text = metrics.elidedText(self._notice, Qt.TextElideMode.ElideRight, self.width() - 4 * padding)
        box = QRectF(padding, padding, metrics.horizontalAdvance(text) + 2 * padding, metrics.height() + padding)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(NOTICE_BACKGROUND)
        painter.drawRoundedRect(box, padding / 2, padding / 2)
        painter.setPen(UNSUNG)
        painter.drawText(box, Qt.AlignmentFlag.AlignCenter, text)

    def _paint_background(self, painter: QPainter) -> None:
        # Cover the whole area, cropping what does not fit, without distortion.
        image = self._background
        scale = max(self.width() / image.width(), self.height() / image.height())
        width = self.width() / scale
        height = self.height() / scale
        source = QRectF((image.width() - width) / 2, (image.height() - height) / 2, width, height)
        painter.drawImage(QRectF(self.rect()), image, source)

    def _paint_line(self, painter: QPainter, line: LineState, baseline: float) -> None:
        text = line.text.strip("\r\n")
        if not text:
            return
        font, metrics = self._fitted_font(text)
        sprite = self._line_sprite(text, font, metrics)
        left = (self.width() - metrics.horizontalAdvance(text)) / 2
        origin = QPointF(left - sprite.margin, baseline - sprite.ascent)

        # The wipe only reveals one prepared image over the other, so it costs almost nothing.
        wipe = left + self._advance(metrics, text, line.sung_chars)
        full = self.rect()
        painter.setClipRect(QRectF(full.left(), full.top(), wipe - full.left(), full.height()))
        painter.drawPixmap(origin, sprite.sung)
        painter.setClipRect(QRectF(wipe, full.top(), full.right() - wipe + 1, full.height()))
        painter.drawPixmap(origin, sprite.unsung)
        painter.setClipping(False)

    def _line_sprite(self, text: str, font: QFont, metrics: QFontMetricsF) -> LineSprite:
        ratio = self.devicePixelRatioF()
        key = (text, font.toString(), ratio)
        sprite = self._sprites.get(key)
        if sprite is None:
            if len(self._sprites) >= MAX_CACHED_SPRITES:
                self._sprites.clear()
            sprite = self._render_sprite(text, font, metrics, ratio)
            self._sprites[key] = sprite
        return sprite

    def _render_sprite(self, text: str, font: QFont, metrics: QFontMetricsF, ratio: float) -> LineSprite:
        margin = font.pointSizeF() * SPRITE_MARGIN
        ascent = metrics.ascent() + margin
        path = QPainterPath()
        path.addText(margin, ascent, font, text)
        width = metrics.horizontalAdvance(text) + 2 * margin
        height = metrics.height() + 2 * margin

        images = []
        for colour in (SUNG, UNSUNG):
            pixmap = QPixmap(math.ceil(width * ratio), math.ceil(height * ratio))
            pixmap.setDevicePixelRatio(ratio)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            self._paint_shadow(painter, path, font.pointSizeF())
            painter.setPen(QPen(OUTLINE, max(1.5, font.pointSizeF() / 18)))
            painter.setBrush(colour)
            painter.drawPath(path)
            painter.end()
            images.append(pixmap)
        return LineSprite(images[0], images[1], margin, ascent)

    def _fitted_font(self, text: str) -> tuple[QFont, QFontMetricsF]:
        """Keep the chosen size, shrinking only a line too wide for the screen."""
        font = QFont(self._font)
        metrics = QFontMetricsF(font)
        available = self.width() * 0.94
        width = metrics.horizontalAdvance(text)
        if width > available:
            font.setPointSizeF(max(MIN_FONT_SIZE, font.pointSizeF() * available / width))
            metrics = QFontMetricsF(font)
        return font, metrics

    @staticmethod
    def _advance(metrics: QFontMetricsF, text: str, chars: float) -> float:
        whole = int(chars)
        position = metrics.horizontalAdvance(text[:whole])
        if whole < len(text):
            position += (chars - whole) * metrics.horizontalAdvance(text[whole])
        return position


def config_directory() -> Path:
    return Path(open_settings().fileName()).parent


class QueueList(QListWidget):
    """Accepts songs dragged from the catalog and reorders itself by dragging."""

    catalog_row_dropped = Signal(int, int)  # catalog row, queue position
    row_moved = Signal(int, int)  # from, to

    def __init__(self, catalog_list: QListWidget, parent=None):
        super().__init__(parent)
        self._catalog_list = catalog_list
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.DragDrop)

    def dragEnterEvent(self, event) -> None:
        if event.source() in (self, self._catalog_list):
            event.acceptProposedAction()

    def dragMoveEvent(self, event) -> None:
        if event.source() in (self, self._catalog_list):
            super().dragMoveEvent(event)
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()

    def dropEvent(self, event) -> None:
        self.handle_drop(event.source(), event.position().toPoint())
        # The queue is rearranged here; a move action would make Qt delete the dragged row.
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()

    def handle_drop(self, source, position: QPoint) -> None:
        target = self._insertion_row(position)
        if source is self:
            row = self.currentRow()
            if row >= 0:
                self.row_moved.emit(row, target - 1 if target > row else target)
        elif source is self._catalog_list:
            row = self._catalog_list.currentRow()
            if row >= 0:
                self.catalog_row_dropped.emit(row, target)

    def _insertion_row(self, position: QPoint) -> int:
        item = self.itemAt(position)
        if item is None:
            return self.count()
        row = self.row(item)
        return row + 1 if position.y() > self.visualItemRect(item).center().y() else row


class PlayerWindow(QMainWindow):
    def __init__(self, soundfont: str | None = None, *, realtime: bool = True, gain: float | None = None):
        super().__init__()
        self.setWindowTitle(tr("Videokê"))
        self.setWindowIcon(QIcon(str(ICON_PATH)))
        self.resize(1000, 650)
        self._settings = open_settings()
        self._song: Song | None = None
        self._seeking = False
        self._tempo_factor = 1.0
        self._synth: Synth | None = None
        self._soundfont = soundfont
        self._realtime = realtime
        self._gain = gain
        self._catalog: list[CatalogEntry] = []
        self._entries_by_number: dict[int, CatalogEntry] = {}
        self._library: Library | None = None
        self._song_folder: str | None = None
        self._queue: list[CatalogEntry] = []
        self._playing_entry: CatalogEntry | None = None
        self._active = False
        self._typed_number = ""
        self._scorer: RhythmScorer | None = None
        self._gain_at_song_start = 1.0
        self._theme_name = DARK
        # Read before any style sheet applies: this is the size the desktop asks for.
        system_size = QApplication.font().pointSize()
        self._system_font_size = system_size if system_size > 0 else FALLBACK_INTERFACE_FONT
        self._envelope_worker: MusicEnvelopeWorker | None = None
        self._soundfont_in_use: str | None = None

        load_bundled_fonts()
        self._build_ui()
        self._build_menus()
        self.statusBar().setSizeGripEnabled(False)

        self._background = BackgroundRotator(self)
        self._background.frame_ready.connect(self._lyrics.set_background)
        self._background.cleared.connect(self._lyrics.clear_background)

        self._score_timer = QTimer(self)
        self._score_timer.setSingleShot(True)
        self._score_timer.setInterval(SCORE_DISPLAY_MS)
        self._score_timer.timeout.connect(self._finish_score_display)

        self._microphone = MicrophoneMeter(self)
        self._microphone.level_measured.connect(self.on_microphone_level)

        self._notice_timer = QTimer(self)
        self._notice_timer.setSingleShot(True)
        self._notice_timer.timeout.connect(self._clear_notice)

        self._timer = QTimer(self)
        self._timer.setInterval(REFRESH_MS)
        self._timer.timeout.connect(self._refresh)
        self._restore_settings()

    def _build_ui(self) -> None:
        self._title = QLabel(tr("Digite o número da música e tecle Enter, ou clique em Músicas"))
        self._title.setObjectName("songTitle")
        self._title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lyrics = LyricWidget()

        self._play_button = QPushButton()
        self._play_button.setObjectName("primaryButton")
        self._play_button.clicked.connect(self.toggle_play)
        self._stop_button = QPushButton(tr("Parar"))
        self._stop_button.clicked.connect(self.stop)
        self._next_button = QPushButton(tr("Próxima"))
        self._next_button.clicked.connect(self.play_next)
        self._slider = QSlider(Qt.Orientation.Horizontal)
        self._slider.sliderPressed.connect(self._begin_seek)
        self._slider.sliderReleased.connect(self._end_seek)
        self._slider.sliderMoved.connect(self._preview_seek)
        self._time = QLabel("0:00 / 0:00")
        self._slower_button = QPushButton(tr("Tempo −"))
        self._slower_button.clicked.connect(lambda: self.change_tempo(-TEMPO_STEP))
        self._tempo_label = QLabel("100%")
        self._faster_button = QPushButton(tr("Tempo +"))
        self._faster_button.clicked.connect(lambda: self.change_tempo(TEMPO_STEP))

        self._songs_button = QPushButton(QIcon(str(ICON_PATH)), tr("Músicas"))
        self._songs_button.clicked.connect(self.show_song_window)

        self._volume_slider = QSlider(Qt.Orientation.Horizontal)
        self._volume_slider.setRange(0, 100)
        self._volume_slider.setFixedWidth(140)
        self._volume_slider.setToolTip(tr("Volume (↑ e ↓)"))
        self._volume_slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._volume_label = QLabel()
        self._volume_label.setMinimumWidth(40)
        self._volume_slider.valueChanged.connect(self.set_volume)
        self._theme_button = QPushButton()
        self._theme_button.clicked.connect(self.toggle_theme)
        volume_title = QLabel("Volume")
        volume_title.setObjectName("mutedLabel")

        position_row = QHBoxLayout()
        position_row.addWidget(self._slider, 1)
        position_row.addWidget(self._time)

        button_row = QHBoxLayout()
        for widget in (self._songs_button, self._play_button, self._stop_button, self._next_button):
            button_row.addWidget(widget)
        button_row.addStretch(1)
        self._button_row = button_row
        self._settings_row = QHBoxLayout()
        self._settings_widgets = (
            self._slower_button,
            self._tempo_label,
            self._faster_button,
            volume_title,
            self._volume_slider,
            self._volume_label,
            self._theme_button,
        )

        controls = QVBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.addLayout(position_row)
        controls.addLayout(button_row)
        controls.addLayout(self._settings_row)

        self._controls = QWidget()
        self._controls.setLayout(controls)
        self._song_window = self._build_song_window()

        stage = QVBoxLayout()
        stage.addWidget(self._title)
        stage.addWidget(self._lyrics, 1)
        stage.addWidget(self._controls)

        self._stage_layout = stage
        self._normal_margins = stage.contentsMargins()
        central = QWidget()
        central.setLayout(stage)
        self.setCentralWidget(central)

        QShortcut(QKeySequence(Qt.Key.Key_F2), self, self.show_song_window)
        QShortcut(QKeySequence(Qt.Key.Key_F5), self, self.refresh_song_folder)
        QShortcut(QKeySequence(Qt.Key.Key_F11), self, self.toggle_fullscreen)
        QShortcut(QKeySequence("Ctrl++"), self, lambda: self.change_font_size(FONT_SIZE_STEP))
        QShortcut(QKeySequence("Ctrl+="), self, lambda: self.change_font_size(FONT_SIZE_STEP))
        QShortcut(QKeySequence("Ctrl+-"), self, lambda: self.change_font_size(-FONT_SIZE_STEP))
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, self.leave_fullscreen)
        QShortcut(QKeySequence(Qt.Key.Key_Up), self, lambda: self.change_volume(VOLUME_STEP))
        QShortcut(QKeySequence(Qt.Key.Key_Down), self, lambda: self.change_volume(-VOLUME_STEP))
        QShortcut(QKeySequence(Qt.Key.Key_Space), self, self.toggle_play)
        QShortcut(QKeySequence(Qt.Key.Key_Left), self, lambda: self._jump(-SEEK_STEP_SECONDS))
        QShortcut(QKeySequence(Qt.Key.Key_Right), self, lambda: self._jump(SEEK_STEP_SECONDS))
        QShortcut(QKeySequence(Qt.Key.Key_Minus), self, lambda: self.change_tempo(-TEMPO_STEP))
        QShortcut(QKeySequence(Qt.Key.Key_Plus), self, lambda: self.change_tempo(TEMPO_STEP))
        QShortcut(QKeySequence(Qt.Key.Key_Equal), self, lambda: self.change_tempo(TEMPO_STEP))
        self._update_controls()

    def _build_song_window(self) -> QDialog:
        self._number_input = QLineEdit()
        self._number_input.setPlaceholderText(tr("Número da música"))
        self._number_input.returnPressed.connect(self.queue_typed_number)
        number_button = QPushButton(tr("Adicionar"))
        number_button.clicked.connect(self.queue_typed_number)
        number_row = QHBoxLayout()
        number_row.addWidget(self._number_input, 1)
        number_row.addWidget(number_button)

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText(tr("Buscar pelo nome"))
        self._search_input.textChanged.connect(lambda _text: self._apply_catalog_filter())

        self._singer_filter = QComboBox()
        self._singer_filter.setEditable(True)
        self._singer_filter.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self._singer_filter.completer().setFilterMode(Qt.MatchFlag.MatchContains)
        self._singer_filter.completer().setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self._singer_filter.addItems([ALL_SINGERS, OTHERS])
        self._singer_filter.currentTextChanged.connect(lambda _text: self._apply_catalog_filter())

        self._catalog_list = QListWidget()
        self._catalog_list.itemDoubleClicked.connect(lambda _item: self.queue_selected())
        self._catalog_list.setDragEnabled(True)
        self._catalog_list.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        add_button = QPushButton(tr("Adicionar à fila"))
        add_button.clicked.connect(self.queue_selected)

        self._queue_list = QueueList(self._catalog_list)
        self._queue_list.setMinimumHeight(170)
        self._queue_list.catalog_row_dropped.connect(self._on_catalog_song_dropped)
        self._queue_list.row_moved.connect(self.move_in_queue)
        self._play_queue_button = QPushButton(tr("Tocar a fila"))
        self._play_queue_button.clicked.connect(self.play_queue)
        remove_button = QPushButton(tr("Remover da fila"))
        export_button = QPushButton(tr("Abrir lista em PDF"))
        export_button.clicked.connect(self.open_song_list_pdf)
        remove_button.clicked.connect(self.remove_selected_from_queue)
        QShortcut(
            QKeySequence(Qt.Key.Key_Delete),
            self._queue_list,
            self.remove_selected_from_queue,
            context=Qt.ShortcutContext.WidgetShortcut,
        )

        self._catalog_label = QLabel(tr("Músicas: escolha a pasta em Opções"))
        self._song_message = QLabel()
        self._song_message.setWordWrap(True)
        panel_layout = QVBoxLayout()
        panel_layout.addLayout(number_row)
        panel_layout.addWidget(self._catalog_label)
        panel_layout.addWidget(self._singer_filter)
        panel_layout.addWidget(self._search_input)
        panel_layout.addWidget(self._catalog_list, 3)
        panel_layout.addWidget(add_button)
        panel_layout.addWidget(QLabel(tr("Fila (arraste para mudar a ordem)")))
        panel_layout.addWidget(self._queue_list, 1)
        queue_buttons = QHBoxLayout()
        queue_buttons.addWidget(self._play_queue_button)
        queue_buttons.addWidget(remove_button)
        panel_layout.addLayout(queue_buttons)
        panel_layout.addWidget(export_button)
        panel_layout.addWidget(self._song_message)
        # Enter in the number field must add once, not also press a default button.
        for button in (number_button, add_button, remove_button, self._play_queue_button, export_button):
            button.setAutoDefault(False)
        dialog = QDialog(self)
        dialog.setWindowTitle(tr("Músicas e fila"))
        dialog.setLayout(panel_layout)
        dialog.resize(500, 780)
        return dialog

    def _build_menus(self) -> None:
        menu_bar = self.menuBar()

        file_menu = menu_bar.addMenu(tr("&Arquivo"))
        self._add_action(file_menu, tr("Abrir música..."), self.choose_file, QKeySequence.StandardKey.Open)
        self._add_action(file_menu, tr("Abrir lista de músicas em PDF"), self.open_song_list_pdf)
        self._add_action(file_menu, tr("Salvar uma cópia da lista em PDF..."), self.choose_pdf_file)
        self._add_action(file_menu, tr("Sair"), self.close, QKeySequence.StandardKey.Quit)

        view_menu = menu_bar.addMenu(tr("&Exibir"))
        self._add_action(view_menu, tr("Músicas e fila (F2)"), self.show_song_window)
        self._add_action(view_menu, tr("Tela cheia (F11; Esc para sair)"), self.toggle_fullscreen)
        self._add_action(view_menu, tr("Alternar modo claro e escuro"), self.toggle_theme)
        view_menu.addSeparator()
        self._add_action(view_menu, tr("Tamanho da letra dos menus..."), self.choose_interface_font_size)
        self._add_action(view_menu, tr("Usar o tamanho de letra do sistema"), lambda: self.set_interface_font_size(None))

        lyrics_menu = menu_bar.addMenu(tr("&Letra"))
        self._add_action(lyrics_menu, tr("Fonte e tamanho..."), self.choose_font)
        self._add_action(lyrics_menu, tr("Aumentar fonte (Ctrl +)"), lambda: self.change_font_size(FONT_SIZE_STEP))
        self._add_action(lyrics_menu, tr("Diminuir fonte (Ctrl -)"), lambda: self.change_font_size(-FONT_SIZE_STEP))
        self._add_action(lyrics_menu, tr("Restaurar fonte padrão"), self.reset_font)

        options_menu = menu_bar.addMenu(tr("&Opções"))
        self._add_action(options_menu, tr("Pasta das músicas..."), self.choose_song_folder)
        self._add_action(options_menu, tr("Atualizar lista de músicas (F5)"), self.refresh_song_folder)
        self._add_action(options_menu, tr("Pasta de imagens e vídeos..."), self.choose_background_folder)
        self._add_action(options_menu, tr("Tirar imagens e vídeos"), self.clear_background_folder)
        options_menu.addSeparator()
        self._score_action = QAction(tr("Dar nota pelo microfone"), self)
        self._score_action.setCheckable(True)
        self._score_action.setChecked(self._settings.value("score_enabled", "true") == "true")
        self._score_action.toggled.connect(self._on_score_option_toggled)
        options_menu.addAction(self._score_action)

    def _add_action(self, menu, text, handler, shortcut=None) -> QAction:
        action = QAction(text, self)
        action.triggered.connect(lambda _checked=False: handler())
        if shortcut is not None:
            action.setShortcut(shortcut)
        menu.addAction(action)
        return action

    def _restore_settings(self) -> None:
        self._apply_theme(self._settings.value("theme", DARK))
        self._volume_slider.setValue(int(self._settings.value("volume", DEFAULT_VOLUME)))
        self.set_volume(self._volume_slider.value())
        saved_font = self._settings.value("lyric_font")
        if saved_font:
            font = QFont()
            if font.fromString(saved_font):
                self._lyrics.set_lyric_font(font)
        song_folder = self._settings.value("song_folder")
        if song_folder and Path(song_folder).is_dir():
            self.set_song_folder(song_folder)
        media_folder = self._settings.value("background_folder")
        if media_folder and Path(media_folder).is_dir():
            self.set_background_folder(media_folder)

    # Songs and queue

    def choose_song_folder(self) -> None:
        start = self._settings.value("song_folder", str(Path.home()))
        folder = QFileDialog.getExistingDirectory(self, tr("Pasta das músicas"), start)
        if folder:
            self.set_song_folder(folder)

    def set_song_folder(self, folder: str) -> None:
        self._settings.setValue("song_folder", folder)
        self._song_folder = folder
        try:
            if self._library is None:
                self._library = Library(config_directory() / LIBRARY_FILE)
            self._catalog = self._library.sync(folder)
        except (sqlite3.Error, OSError) as exc:
            self._catalog = []
            self._show_error(tr("Não consegui ler o banco de músicas: {0}").format(exc))
        self._entries_by_number = {entry.number: entry for entry in self._catalog}
        self._catalog_list.clear()
        for entry in self._catalog:
            item = QListWidgetItem(entry.label)
            item.setToolTip(str(entry.path))
            self._catalog_list.addItem(item)
        self._singer_filter.blockSignals(True)
        self._singer_filter.clear()
        self._singer_filter.addItems([ALL_SINGERS, OTHERS, *singer_names(self._catalog)])
        self._singer_filter.setCurrentIndex(0)
        self._singer_filter.blockSignals(False)
        self._apply_catalog_filter()
        self._update_song_list_pdf()

    def refresh_song_folder(self) -> None:
        if self._song_folder:
            self.set_song_folder(self._song_folder)

    def song_list_pdf_path(self) -> Path:
        return config_directory() / SONG_LIST_PDF

    def _update_song_list_pdf(self) -> None:
        """Generate the PDF only when the folder's files changed since the last one."""
        path = self.song_list_pdf_path()
        if not self._catalog or (path.is_file() and self._library.pdf_is_current()):
            return
        temporary = path.with_name(f".{path.name}.tmp")
        try:
            export_song_list(self._catalog, temporary)
            os.replace(temporary, path)
            self._library.mark_pdf_current()
        except (OSError, sqlite3.Error) as exc:
            temporary.unlink(missing_ok=True)
            self._notify(tr("Não consegui gerar a lista em PDF: {0}").format(exc))
            return
        self._notify(tr("Lista de músicas em PDF atualizada."))

    def _apply_catalog_filter(self) -> None:
        wanted_name = self._search_input.text().casefold().strip()
        singer_text = self._singer_filter.currentText().strip()
        wanted_singer = singer_key(singer_text)
        shown = 0
        for row, entry in enumerate(self._catalog):
            if singer_text in ("", ALL_SINGERS):
                singer_matches = True
            elif singer_text.casefold() == OTHERS.casefold():
                singer_matches = not entry.singer
            else:
                singer_matches = wanted_singer in singer_key(entry.singer)
            visible = singer_matches and wanted_name in entry.name.casefold()
            self._catalog_list.setRowHidden(row, not visible)
            shown += visible
        total = len(self._catalog)
        self._catalog_label.setText(tr("Músicas: {0}").format(total) if shown == total else tr("Músicas: {0} de {1}").format(shown, total))

    def show_song_window(self) -> None:
        self._song_window.show()
        self._song_window.raise_()
        self._song_window.activateWindow()
        self._number_input.setFocus()

    def _notify(self, message: str) -> None:
        self.statusBar().showMessage(message, MESSAGE_MS)
        self._song_message.setText(message)
        self._lyrics.set_notice(message)
        self._notice_timer.start(MESSAGE_MS)

    def _clear_notice(self) -> None:
        self._typed_number = ""
        self._lyrics.set_notice("")

    def keyPressEvent(self, event) -> None:
        """Typing a number anywhere on the stage works like a karaoke machine's remote."""
        key = event.key()
        text = event.text()
        if text.isdecimal() and text.isascii() and len(text) == 1:
            if len(self._typed_number) < MAX_TYPED_DIGITS:
                self._typed_number += text
            self._show_typed_number()
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and self._typed_number:
            typed, self._typed_number = self._typed_number, ""
            self._queue_number(typed)
        elif key == Qt.Key.Key_Backspace and self._typed_number:
            self._typed_number = self._typed_number[:-1]
            if self._typed_number:
                self._show_typed_number()
            else:
                self._clear_notice()
        else:
            super().keyPressEvent(event)

    def _show_typed_number(self) -> None:
        self._lyrics.set_notice(tr("Música nº {0}").format(self._typed_number))
        self._notice_timer.start(TYPED_NUMBER_MS)

    def queue_typed_number(self) -> None:
        text = self._number_input.text().strip()
        if text and self._queue_number(text):
            self._number_input.clear()

    def _queue_number(self, text: str) -> bool:
        entry = self._entry_by_number(text)
        if entry is None:
            self._notify(tr("Não existe música com o número {0}.").format(text))
            return False
        self.add_to_queue(entry)
        return True

    def open_song_list_pdf(self) -> None:
        path = self.song_list_pdf_path()
        if not path.is_file():
            self._notify(tr("Escolha antes a pasta das músicas em Opções."))
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def choose_pdf_file(self) -> None:
        if not self.song_list_pdf_path().is_file():
            self._notify(tr("Escolha antes a pasta das músicas em Opções."))
            return
        start = str(Path.home() / SONG_LIST_PDF)
        path, _ = QFileDialog.getSaveFileName(self, tr("Salvar uma cópia da lista"), start, "PDF (*.pdf)")
        if path:
            self.export_pdf(path if path.lower().endswith(".pdf") else f"{path}.pdf")

    def export_pdf(self, path: str) -> None:
        try:
            shutil.copyfile(self.song_list_pdf_path(), path)
        except OSError as exc:
            self._show_error(tr("Não consegui salvar a lista: {0}").format(exc.strerror or exc))
            return
        self._notify(tr("Lista salva: {0}").format(path))

    def _entry_by_number(self, text: str) -> CatalogEntry | None:
        return self._entries_by_number.get(int(text)) if text.isdecimal() else None

    def queue_selected(self) -> None:
        item = self._catalog_list.currentItem()
        if item is None or item.isHidden():
            self._notify(tr("Selecione uma música na lista."))
            return
        self.add_to_queue(self._catalog[self._catalog_list.row(item)])

    def add_to_queue(self, entry: CatalogEntry, position: int | None = None) -> None:
        """Add without playing: songs only start when asked to."""
        if self._active and entry == self._playing_entry:
            self._notify(tr("Já está tocando: {0}").format(entry.name))
            return
        if entry in self._queue:
            self._notify(tr("Já está na fila: {0}").format(entry.name))
            return
        row = len(self._queue) if position is None else position
        self._queue.insert(row, entry)
        self._queue_list.insertItem(row, entry.label)
        self._notify(tr("Na fila: {0}").format(entry.name))
        self._update_controls()

    def _on_catalog_song_dropped(self, catalog_row: int, position: int) -> None:
        self.add_to_queue(self._catalog[catalog_row], position)

    def move_in_queue(self, source: int, target: int) -> None:
        if source == target:
            return
        self._queue.insert(target, self._queue.pop(source))
        self._queue_list.insertItem(target, self._queue_list.takeItem(source))
        self._queue_list.setCurrentRow(target)

    def remove_selected_from_queue(self) -> None:
        row = self._queue_list.currentRow()
        if row >= 0:
            del self._queue[row]
            self._queue_list.takeItem(row)
            self._update_controls()

    def play_queue(self) -> None:
        """Start the queue, unless a song is already going."""
        if self._active:
            if not self._synth.is_playing:
                self.toggle_play()
        elif self._queue:
            self.play_next()
        else:
            self._notify(tr("A fila está vazia."))

    def play_next(self) -> None:
        """Play the next playable song, skipping any that cannot be opened."""
        while self._queue:
            entry = self._queue.pop(0)
            self._queue_list.takeItem(0)
            self._update_controls()
            try:
                if self._open(str(entry.path)):
                    self._playing_entry = entry
                    return
            except SongError as exc:
                self._notify(tr("{0}: {1} Pulando.").format(entry.name, exc))
            except SynthError as exc:
                self._show_error(str(exc))
                return
        self.stop()

    # Playback

    def choose_file(self) -> None:
        start = self._settings.value("last_dir", str(Path.home()))
        path, _ = QFileDialog.getOpenFileName(self, tr("Abrir música"), start, FILE_FILTER)
        if path:
            self._settings.setValue("last_dir", str(Path(path).parent))
            self.open_song(path)

    def open_song(self, path: str, *, autoplay: bool = True) -> bool:
        try:
            return self._open(path, autoplay=autoplay)
        except (SongError, SynthError) as exc:
            self._show_error(str(exc))
            return False

    def _open(self, path: str, *, autoplay: bool = True) -> bool:
        # The first opening of a score takes a moment, unlike the other formats.
        source = Path(path)
        if source.suffix.lower() in MUSICXML_SUFFIXES and not is_prepared_score(source):
            self._notify(tr("Preparando a partitura, só na primeira vez: {0}").format(source.stem))
            self._lyrics.repaint()
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            song = load_song(path)
        finally:
            QApplication.restoreOverrideCursor()
        synth = self._ensure_synth()
        if synth is None:
            return False
        synth.load(song.midi_bytes)

        self._dismiss_score()
        self._song = song
        self._scorer = RhythmScorer(song.timeline.syllables, song.ticks_per_beat)
        self._gain_at_song_start = self._current_gain()
        if self._realtime and self._score_action.isChecked():
            self._start_envelope_worker(song)
        self._tempo_factor = 1.0
        self._tempo_label.setText("100%")
        heading = f"{song.title} — {song.singer}" if song.singer else song.title
        self._title.setText(heading)
        self.setWindowTitle(tr("{0} — Videokê").format(song.title))
        self._slider.setRange(0, song.total_ticks)
        self._slider.setValue(0)
        self._show_position(0)
        if autoplay:
            self._start()
        self._update_controls()
        return True

    def set_volume(self, percent: int) -> None:
        self._volume_label.setText(f"{percent}%")
        self._settings.setValue("volume", percent)
        if self._synth and self._gain is None:
            self._synth.set_gain(self._current_gain())

    def change_volume(self, step: int) -> None:
        self._volume_slider.setValue(self._volume_slider.value() + step)
        if self._lyrics.notice() or self.isFullScreen():
            self._notify(tr("Volume: {0}%").format(self._volume_slider.value()))

    def _current_gain(self) -> float:
        return self._gain if self._gain is not None else self._volume_slider.value() / 100 * FULL_VOLUME_GAIN

    def interface_font_size(self) -> int:
        """The chosen size for menus, buttons and lists, or the desktop's own."""
        try:
            saved = int(self._settings.value("interface_font_size", 0))
        except (TypeError, ValueError):
            saved = 0
        return max(INTERFACE_FONT_MIN, min(INTERFACE_FONT_MAX, saved)) if saved else self._system_font_size

    def _arrange_controls(self) -> None:
        """Keep the window narrower than the screen: with large letters, use a third row."""
        screen = self.screen() or QApplication.primaryScreen()
        needed = CONTROLS_WIDTH_PER_POINT * self.interface_font_size()
        row = self._button_row if needed <= screen.availableGeometry().width() * 0.95 else self._settings_row
        for widget in self._settings_widgets:
            row.addWidget(widget)

    def choose_interface_font_size(self) -> None:
        size, accepted = QInputDialog.getInt(
            self,
            tr("Tamanho da letra dos menus"),
            tr("Tamanho da letra de menus, botões e listas:"),
            self.interface_font_size(),
            INTERFACE_FONT_MIN,
            INTERFACE_FONT_MAX,
        )
        if accepted:
            self.set_interface_font_size(size)

    def set_interface_font_size(self, size: int | None) -> None:
        """Pass None to follow the desktop's size again."""
        if size is None:
            self._settings.remove("interface_font_size")
        else:
            self._settings.setValue("interface_font_size", size)
        self._apply_theme(self._theme_name)

    def toggle_theme(self) -> None:
        self._apply_theme(LIGHT if self._theme_name == DARK else DARK)

    def _apply_theme(self, name: str) -> None:
        self._theme_name = name if name in THEMES else DARK
        self._settings.setValue("theme", self._theme_name)
        theme = THEMES[self._theme_name]
        QApplication.instance().setStyleSheet(style_sheet(theme, self.interface_font_size()))
        self._arrange_controls()
        self._theme_button.setText(tr("Modo claro") if self._theme_name == DARK else tr("Modo escuro"))
        self._stop_button.setIcon(media_icon("stop", theme.text))
        self._next_button.setIcon(media_icon("next", theme.text))
        self._update_controls()

    def _start_envelope_worker(self, song: Song) -> None:
        self._cancel_envelope_worker()
        if song.timeline.has_lyrics:
            self._envelope_worker = MusicEnvelopeWorker(
                self._scorer, song.midi_bytes, self._soundfont_in_use, song.total_ticks
            )
            self._envelope_worker.start()

    def _cancel_envelope_worker(self) -> None:
        if self._envelope_worker is not None:
            self._envelope_worker.cancel()
            self._envelope_worker = None

    def _ensure_synth(self) -> Synth | None:
        if self._synth:
            return self._synth
        soundfont = self._soundfont or self._settings.value("soundfont") or find_soundfont()
        if not soundfont or not Path(soundfont).is_file():
            soundfont, _ = QFileDialog.getOpenFileName(
                self, tr("Escolha um SoundFont"), str(Path.home()), tr("SoundFont (*.sf2 *.sf3);;Todos os arquivos (*)")
            )
            if not soundfont:
                return None
            self._settings.setValue("soundfont", soundfont)
        self._synth = Synth(soundfont, realtime=self._realtime, gain=self._current_gain())
        self._soundfont_in_use = soundfont
        return self._synth

    def _start(self) -> None:
        self._synth.play()
        self._active = True
        self._timer.start()
        self._background.start()
        self._start_microphone()
        self._update_controls()

    def _start_microphone(self) -> None:
        # Offline rendering has no real clock for the microphone to follow.
        if self._realtime and self._score_action.isChecked() and not self._microphone.start():
            self._notify(tr("Nenhum microfone disponível: a música tocará sem nota."))

    def _on_score_option_toggled(self, enabled: bool) -> None:
        self._settings.setValue("score_enabled", "true" if enabled else "false")
        if not enabled:
            self._microphone.stop()
            self._scorer = None
        elif self._synth and self._synth.is_playing:
            self._start_microphone()

    def on_microphone_level(self, level_db: float) -> None:
        if self._scorer is not None and self._synth and self._synth.is_playing:
            self._scorer.add_level(self._synth.tick, level_db - self._volume_change_db())

    def _volume_change_db(self) -> float:
        """Raising the volume mid-song also raises what leaks into the microphone."""
        gain = self._current_gain()
        if gain <= 0 or self._gain_at_song_start <= 0:
            return 0.0
        return 20 * math.log10(gain / self._gain_at_song_start)

    def toggle_play(self) -> None:
        if self._score_timer.isActive():
            self._finish_score_display()
            return
        if not self._active and self._queue:
            self.play_next()
            return
        if not self._song or not self._synth:
            return
        if self._synth.is_playing:
            self._synth.pause()
            self._timer.stop()
            self._background.pause()
            self._microphone.stop()
            self._update_controls()
        else:
            if self._song.total_ticks <= self._synth.tick:
                self._synth.seek(0)
            self._start()

    def stop(self) -> None:
        self._dismiss_score()
        self._active = False
        self._timer.stop()
        self._background.stop()
        self._microphone.stop()
        if not self._song or not self._synth:
            return
        self._synth.pause()
        self._synth.seek(0)
        self._slider.setValue(0)
        self._show_position(0)
        self._update_controls()

    def change_tempo(self, step: float) -> None:
        if not self._song or not self._synth:
            return
        factor = round(self._tempo_factor + step, 2)
        self._tempo_factor = max(TEMPO_MIN, min(TEMPO_MAX, factor))
        self._synth.set_tempo_factor(self._tempo_factor)
        self._tempo_label.setText(f"{round(self._tempo_factor * 100)}%")
        self._show_position(self._synth.tick)

    def _jump(self, seconds: int) -> None:
        if not self._song:
            return
        now = self._song.seconds_at(self._synth.tick)
        self._seek_to(self._song.tick_at_seconds(now + seconds * self._tempo_factor))

    def _begin_seek(self) -> None:
        self._seeking = True

    def _preview_seek(self, tick: int) -> None:
        self._show_position(tick)

    def _end_seek(self) -> None:
        self._seeking = False
        self._seek_to(self._slider.value())

    def _seek_to(self, tick: int) -> None:
        was_playing = self._synth.is_playing
        self._synth.seek(tick)
        self._slider.setValue(tick)
        self._show_position(tick)
        if was_playing:
            self._synth.play()

    def _refresh(self) -> None:
        if not self._song:
            return
        if self._synth.finished:
            self._on_song_finished()
            return
        if self._seeking:
            return
        tick = self._synth.tick
        self._slider.setValue(tick)
        self._show_position(tick)

    def _on_song_finished(self) -> None:
        score = self._scorer.result() if self._scorer is not None else None
        self.stop()
        if score is None:
            if self._queue:
                self.play_next()
            return
        self._lyrics.set_score(score)
        self._score_timer.start()

    def _finish_score_display(self) -> None:
        self._dismiss_score()
        if self._queue:
            self.play_next()

    def _dismiss_score(self) -> None:
        self._score_timer.stop()
        self._lyrics.set_score(None)

    def _show_position(self, tick: int) -> None:
        song = self._song
        upper, lower = song.timeline.lines_at(tick)
        self._lyrics.show_lines(upper, lower)
        elapsed = song.seconds_at(tick) / self._tempo_factor
        total = song.total_seconds / self._tempo_factor
        self._time.setText(f"{_clock(elapsed)} / {_clock(total)}")

    def _update_controls(self) -> None:
        loaded = self._song is not None
        playing = loaded and self._synth is not None and self._synth.is_playing
        self._play_button.setIcon(media_icon("pause" if playing else "play", "#ffffff"))
        self._play_button.setText(tr("Pausar") if playing else tr("Tocar"))
        self._play_button.setEnabled(loaded or bool(self._queue))
        for widget in (self._stop_button, self._slider, self._slower_button, self._faster_button):
            widget.setEnabled(loaded)

    # Full screen

    def toggle_fullscreen(self) -> None:
        if self.isFullScreen():
            self.leave_fullscreen()
        else:
            self._set_chrome_visible(False)
            self.showFullScreen()

    def leave_fullscreen(self) -> None:
        if self._typed_number:
            self._clear_notice()
            return
        if self.isFullScreen():
            self.showNormal()
            self._set_chrome_visible(True)

    def _set_chrome_visible(self, visible: bool) -> None:
        for widget in (self._title, self._controls, self.menuBar(), self.statusBar()):
            widget.setVisible(visible)
        self._stage_layout.setContentsMargins(self._normal_margins if visible else QMargins(0, 0, 0, 0))

    # Lyric font and background

    def choose_font(self) -> None:
        accepted, font = QFontDialog.getFont(self._lyrics.lyric_font(), self, tr("Fonte e tamanho da letra"))
        if accepted:
            self.set_lyric_font(font)

    def set_lyric_font(self, font: QFont) -> None:
        self._lyrics.set_lyric_font(font)
        self._settings.setValue("lyric_font", font.toString())

    def change_font_size(self, step: int) -> None:
        font = self._lyrics.lyric_font()
        size = max(MIN_FONT_SIZE, min(MAX_FONT_SIZE, font.pointSize() + step))
        font.setPointSize(size)
        self.set_lyric_font(font)
        self._notify(tr("Tamanho da letra: {0}").format(size))

    def reset_font(self) -> None:
        self._lyrics.set_lyric_font(default_lyric_font())
        self._settings.remove("lyric_font")

    def choose_background_folder(self) -> None:
        start = self._settings.value("background_folder", str(Path.home()))
        folder = QFileDialog.getExistingDirectory(self, tr("Pasta de imagens e vídeos"), start)
        if folder:
            self.set_background_folder(folder)

    def set_background_folder(self, folder: str) -> None:
        self._settings.setValue("background_folder", folder)
        count = self._background.set_folder(folder)
        if not count:
            self._notify(tr("Nenhuma imagem ou vídeo nessa pasta."))
        elif self._synth and self._synth.is_playing:
            self._background.start()

    def clear_background_folder(self) -> None:
        self._settings.remove("background_folder")
        self._background.set_folder(None)

    def _show_error(self, message: str) -> None:
        QMessageBox.warning(self, tr("Videokê"), message)

    def closeEvent(self, event) -> None:
        self._timer.stop()
        self._score_timer.stop()
        self._microphone.stop()
        self._background.stop()
        self._song_window.close()
        if self._envelope_worker is not None:
            worker = self._envelope_worker
            self._cancel_envelope_worker()
            worker.join(timeout=5)
        if self._synth:
            self._synth.close()
            self._synth = None
        if self._library:
            self._library.close()
            self._library = None
        super().closeEvent(event)


def _clock(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 60}:{total % 60:02d}"


def _load_qt_translation(application: QApplication) -> None:
    """Qt's own buttons, such as Cancel, come in English unless told otherwise."""
    if LANGUAGE != PORTUGUESE:
        return
    translator = QTranslator(application)
    folder = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    for name in (f"qtbase_{QLocale.system().name()}", "qtbase_pt_BR", "qtbase_pt_PT"):
        if translator.load(name, folder):
            application.installTranslator(translator)
            return


def main() -> int:
    if len(sys.argv) > 2 and sys.argv[1] == "--self-test":
        import self_test

        return self_test.run(sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else None)
    application = QApplication(sys.argv)
    _load_qt_translation(application)
    application.setDesktopFileName(SETTINGS_NAME)
    application.setWindowIcon(QIcon(str(ICON_PATH)))
    window = PlayerWindow()
    window.show()
    if len(sys.argv) > 1:
        window.open_song(sys.argv[1])
    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
