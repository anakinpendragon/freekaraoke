"""Draws icons/freekaraoke.png from the U+266B music note. Run with a font file that has it."""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QPainter
from PySide6.QtWidgets import QApplication

SIZE = 256
NOTE = "♫"
OUTPUT = Path(__file__).resolve().parent.parent / "icons" / "freekaraoke.png"
WINDOWS_ICON = Path(__file__).resolve().parent / "freekaraoke.ico"


def main() -> None:
    application = QApplication([])
    font_id = QFontDatabase.addApplicationFont(sys.argv[1])
    if font_id < 0:
        raise SystemExit(f"Não consegui carregar a fonte: {sys.argv[1]}")
    family = QFontDatabase.applicationFontFamilies(font_id)[0]

    image = QImage(SIZE, SIZE, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#10131a"))
    painter.drawRoundedRect(4, 4, SIZE - 8, SIZE - 8, 44, 44)
    font = QFont(family)
    font.setPixelSize(190)
    painter.setFont(font)
    painter.setPen(QColor("#ffd400"))
    painter.drawText(QRect(0, 0, SIZE, SIZE), Qt.AlignmentFlag.AlignCenter, NOTE)
    painter.end()
    image.save(str(OUTPUT))
    image.scaled(64, 64, mode=Qt.TransformationMode.SmoothTransformation).save(str(WINDOWS_ICON))
    del application


if __name__ == "__main__":
    main()
