"""Dark and light looks using Bootstrap 5's colours."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPixmap

ICON_DIRECTORY = Path(__file__).resolve().parent / "icons"
ICON_SIZE = 32
TITLE_SCALE = 1.6
DARK = "dark"
LIGHT = "light"


@dataclass(frozen=True)
class Theme:
    name: str
    body: str
    surface: str
    raised: str
    hover: str
    border: str
    text: str
    muted: str
    disabled: str
    chevron: str


PRIMARY = "#0d6efd"
PRIMARY_HOVER = "#0b5ed7"
PRIMARY_PRESSED = "#0a58ca"

THEMES = {
    DARK: Theme(DARK, "#212529", "#2b3035", "#343a40", "#495057", "#495057", "#dee2e6", "#adb5bd", "#6c757d", "chevron-for-dark.svg"),
    LIGHT: Theme(LIGHT, "#ffffff", "#f8f9fa", "#ffffff", "#e9ecef", "#dee2e6", "#212529", "#6c757d", "#adb5bd", "chevron-for-light.svg"),
}


def style_sheet(theme: Theme, font_points: float) -> str:
    chevron = (ICON_DIRECTORY / theme.chevron).as_posix()
    return f"""
    QWidget {{ background-color: {theme.body}; color: {theme.text}; font-size: {font_points:g}pt; }}
    QLabel {{ background: transparent; }}
    QLabel#songTitle {{ font-size: {round(font_points * TITLE_SCALE)}pt; font-weight: bold; }}
    QLabel#mutedLabel {{ color: {theme.muted}; }}

    QPushButton {{
        background-color: {theme.raised}; border: 1px solid {theme.border};
        border-radius: 6px; padding: 6px 12px;
    }}
    QPushButton:hover {{ background-color: {theme.hover}; }}
    QPushButton:pressed {{ background-color: {theme.border}; }}
    QPushButton:focus {{ border-color: {PRIMARY}; }}
    QPushButton:disabled {{ color: {theme.disabled}; background-color: {theme.surface}; }}
    QPushButton#primaryButton {{ background-color: {PRIMARY}; border-color: {PRIMARY}; color: #ffffff; }}
    QPushButton#primaryButton:hover {{ background-color: {PRIMARY_HOVER}; border-color: {PRIMARY_HOVER}; }}
    QPushButton#primaryButton:pressed {{ background-color: {PRIMARY_PRESSED}; }}
    QPushButton#primaryButton:disabled {{
        background-color: {theme.surface}; border-color: {theme.border}; color: {theme.disabled};
    }}

    QLineEdit, QComboBox {{
        background-color: {theme.surface}; border: 1px solid {theme.border};
        border-radius: 6px; padding: 6px 10px; selection-background-color: {PRIMARY}; selection-color: #ffffff;
    }}
    QLineEdit:focus, QComboBox:focus {{ border-color: {PRIMARY}; }}
    QComboBox::drop-down {{ border: none; width: 26px; }}
    QComboBox::down-arrow {{ image: url({chevron}); width: 12px; height: 12px; }}
    QComboBox QAbstractItemView {{
        background-color: {theme.surface}; border: 1px solid {theme.border};
        selection-background-color: {PRIMARY}; selection-color: #ffffff; outline: 0;
    }}

    QListWidget {{
        background-color: {theme.surface}; border: 1px solid {theme.border}; border-radius: 6px; outline: 0;
    }}
    QListWidget::item {{ padding: 3px 6px; background: transparent; }}
    QListWidget::item:hover {{ background-color: {theme.hover}; }}
    QListWidget::item:selected {{ background-color: {PRIMARY}; color: #ffffff; }}

    QSlider {{ background: transparent; }}
    QSlider::groove:horizontal {{ height: 6px; background: {theme.border}; border-radius: 3px; }}
    QSlider::sub-page:horizontal {{ background: {PRIMARY}; border-radius: 3px; }}
    QSlider::handle:horizontal {{
        width: 16px; height: 16px; margin: -5px 0; border-radius: 8px; background: {PRIMARY};
    }}
    QSlider::handle:horizontal:hover {{ background: {PRIMARY_HOVER}; }}
    QSlider::groove:horizontal:disabled {{ background: {theme.surface}; }}
    QSlider::sub-page:horizontal:disabled {{ background: {theme.border}; }}
    QSlider::handle:horizontal:disabled {{ background: {theme.disabled}; }}

    QMenuBar {{ background-color: {theme.surface}; border-bottom: 1px solid {theme.border}; }}
    QMenuBar::item {{ background: transparent; padding: 6px 10px; }}
    QMenuBar::item:selected {{ background-color: {theme.hover}; border-radius: 4px; }}
    QMenu {{ background-color: {theme.surface}; border: 1px solid {theme.border}; padding: 4px; }}
    QMenu::item {{ padding: 6px 24px; background: transparent; border-radius: 4px; }}
    QMenu::item:selected {{ background-color: {PRIMARY}; color: #ffffff; }}
    QMenu::separator {{ height: 1px; background: {theme.border}; margin: 4px 8px; }}

    QStatusBar {{ background-color: {theme.surface}; color: {theme.muted}; border-top: 1px solid {theme.border}; }}
    QStatusBar::item {{ border: none; }}
    QToolTip {{ background-color: {theme.raised}; color: {theme.text}; border: 1px solid {theme.border}; }}

    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
    QScrollBar::handle {{ background: {theme.disabled}; border-radius: 3px; min-height: 24px; min-width: 24px; }}
    QScrollBar::handle:hover {{ background: {theme.muted}; }}
    QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{
        background: none; width: 0; height: 0;
    }}
    """


def media_icon(kind: str, colour: str) -> QIcon:
    """Drawn rather than taken from the system, so it suits both themes everywhere."""
    pixmap = QPixmap(ICON_SIZE, ICON_SIZE)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(colour))
    if kind == "play":
        painter.drawPath(_triangle(8, 24))
    elif kind == "pause":
        painter.drawRoundedRect(QRectF(8, 6, 6, 20), 1, 1)
        painter.drawRoundedRect(QRectF(18, 6, 6, 20), 1, 1)
    elif kind == "stop":
        painter.drawRoundedRect(QRectF(8, 8, 16, 16), 2, 2)
    elif kind == "next":
        painter.drawPath(_triangle(6, 21))
        painter.drawRoundedRect(QRectF(22, 6, 4, 20), 1, 1)
    else:
        painter.end()
        raise ValueError(kind)
    painter.end()
    return QIcon(pixmap)


def _triangle(left: float, right: float) -> QPainterPath:
    path = QPainterPath(QPointF(left, 6))
    path.lineTo(right, 16)
    path.lineTo(left, 26)
    path.closeSubpath()
    return path
