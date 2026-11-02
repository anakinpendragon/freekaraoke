"""Exports the numbered song list as a printable A4 PDF."""

from __future__ import annotations

import datetime
from pathlib import Path

from PySide6.QtCore import QMarginsF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPageLayout, QPageSize, QPainter, QPdfWriter

from catalog import OTHERS, CatalogEntry

RESOLUTION = 300
MARGIN_MM = 15
FONT_FAMILY = "Liberation Sans"
HEADING_SIZE = 16
BODY_SIZE = 9
ROW_SPACING = 1.35
NUMBER_COLUMN = 0.09
SONG_COLUMN = 0.55
SHADE = QColor("#f0f0f0")


def export_song_list(entries: list[CatalogEntry], path: str | Path) -> None:
    """Write the PDF; raises OSError if the file cannot be created."""
    writer = QPdfWriter(str(path))
    writer.setResolution(RESOLUTION)
    writer.setTitle("Lista de músicas")
    writer.setCreator("FreeKaraoke")
    writer.setPageLayout(
        QPageLayout(
            QPageSize(QPageSize.PageSizeId.A4),
            QPageLayout.Orientation.Portrait,
            QMarginsF(MARGIN_MM, MARGIN_MM, MARGIN_MM, MARGIN_MM),
            QPageLayout.Unit.Millimeter,
        )
    )
    painter = QPainter()
    if not painter.begin(writer):
        raise OSError(f"Não consegui criar o arquivo: {path}")
    try:
        _draw(painter, writer, entries)
    finally:
        painter.end()


def _draw(painter: QPainter, writer: QPdfWriter, entries: list[CatalogEntry]) -> None:
    heading_font = QFont(FONT_FAMILY)
    heading_font.setPointSize(HEADING_SIZE)
    heading_font.setBold(True)
    body_font = QFont(FONT_FAMILY)
    body_font.setPointSize(BODY_SIZE)
    bold_font = QFont(body_font)
    bold_font.setBold(True)

    # Fonts are measured on the PDF device: its resolution differs from the screen's.
    painter.setFont(heading_font)
    heading_height = painter.fontMetrics().height() * 1.4
    painter.setFont(body_font)
    metrics = QFontMetricsF(painter.fontMetrics())
    row_height = metrics.height() * ROW_SPACING

    width = float(writer.width())
    height = float(writer.height())
    first_rows = int((height - heading_height - 2 * row_height) / row_height)
    other_rows = int((height - 2 * row_height) / row_height)
    remaining = max(0, len(entries) - first_rows)
    pages = 1 + remaining // other_rows + (1 if remaining % other_rows else 0)

    number_x = 0.0
    song_x = width * NUMBER_COLUMN
    singer_x = width * (NUMBER_COLUMN + SONG_COLUMN)
    song_width = singer_x - song_x - row_height / 2
    singer_width = width - singer_x

    today = datetime.date.today().strftime("%d/%m/%Y")
    index = 0
    for page in range(1, pages + 1):
        if page > 1:
            writer.newPage()
        y = 0.0
        if page == 1:
            painter.setFont(heading_font)
            painter.drawText(QRectF(0, 0, width, heading_height), Qt.AlignmentFlag.AlignVCenter, "Lista de músicas")
            painter.setFont(body_font)
            summary = f"{len(entries)} músicas — {today}"
            painter.drawText(QRectF(0, 0, width, heading_height), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, summary)
            y = heading_height

        painter.setFont(bold_font)
        for x, text in ((number_x, "Nº"), (song_x, "Música"), (singer_x, "Cantor")):
            painter.drawText(QRectF(x, y, width - x, row_height), Qt.AlignmentFlag.AlignVCenter, text)
        y += row_height
        painter.drawLine(0, round(y), round(width), round(y))

        painter.setFont(body_font)
        rows = first_rows if page == 1 else other_rows
        for row in range(rows):
            if index >= len(entries):
                break
            entry = entries[index]
            if row % 2:
                painter.fillRect(QRectF(0, y, width, row_height), SHADE)
            cells = (
                (number_x, song_x, f"{entry.number:04d}"),
                (song_x, song_width, metrics.elidedText(entry.name, Qt.TextElideMode.ElideRight, song_width)),
                (singer_x, singer_width, metrics.elidedText(entry.singer or OTHERS, Qt.TextElideMode.ElideRight, singer_width)),
            )
            for x, cell_width, text in cells:
                painter.drawText(QRectF(x, y, cell_width, row_height), Qt.AlignmentFlag.AlignVCenter, text)
            y += row_height
            index += 1

        painter.drawText(
            QRectF(0, height - row_height, width, row_height),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
            f"Página {page} de {pages}",
        )
