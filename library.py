"""SQLite record of song numbers, so a printed list stays valid as the folder changes.

A number belongs to a song for good: new songs get the next free number, and a
removed song's number is never given to another. Rows outlive their files to keep
that promise and to restore the number if the file comes back.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
from pathlib import Path

from catalog import CatalogEntry, find_songs, read_singer

_SCHEMA = """
CREATE TABLE IF NOT EXISTS songs (
    number INTEGER PRIMARY KEY,
    path BLOB NOT NULL UNIQUE,
    size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    singer TEXT NOT NULL,
    present INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""
# Bump when read_singer changes what it returns, to read every file's singer again.
SINGER_RULES_VERSION = "1"
# Bump when the PDF's layout changes, to generate it again.
PDF_LAYOUT_VERSION = "1"


class Library:
    def __init__(self, path: str | Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(path))
        self._connection.executescript(_SCHEMA)
        self.signature = ""

    def close(self) -> None:
        self._connection.close()

    def sync(self, folder: str | Path) -> list[CatalogEntry]:
        """Record what is in the folder now and return its songs, sorted by name."""
        root = Path(folder).absolute()
        files = {}
        for path in find_songs(root):
            try:
                status = path.stat()
            except OSError:
                continue
            files[path] = (status.st_size, status.st_mtime_ns)

        known = {}
        for number, raw_path, size, mtime_ns, present in self._connection.execute(
            "SELECT number, path, size, mtime_ns, present FROM songs"
        ):
            path = Path(os.fsdecode(raw_path))
            if path.is_relative_to(root):
                known[path] = (number, size, mtime_ns, present)

        with self._connection:
            reread_singers = self._meta("singer_rules") != SINGER_RULES_VERSION
            new_paths = self._apply_renames(files, known)
            self._update_known(files, known, reread_singers)
            self._add_new(files, new_paths)
            self._set_meta("singer_rules", SINGER_RULES_VERSION)

        return self._present_entries(root, files)

    def _apply_renames(self, files: dict, known: dict) -> list[Path]:
        """Keep the number of a file that moved, recognised by identical size and date.

        Returns the files that are really new.
        """
        gone: dict[tuple[int, int], list[Path]] = {}
        for path, (_, size, mtime_ns, present) in known.items():
            if present and path not in files:
                gone.setdefault((size, mtime_ns), []).append(path)
        added: dict[tuple[int, int], list[Path]] = {}
        for path, stamp in files.items():
            if path not in known:
                added.setdefault(stamp, []).append(path)

        new_paths = []
        for stamp, paths in added.items():
            if len(paths) == 1 and len(gone.get(stamp, ())) == 1:
                old_path = gone[stamp][0]
                number = known.pop(old_path)[0]
                self._connection.execute(
                    "UPDATE songs SET path = ? WHERE number = ?", (os.fsencode(paths[0]), number)
                )
                known[paths[0]] = (number, *stamp, 1)
            else:
                new_paths.extend(paths)
        return new_paths

    def _update_known(self, files: dict, known: dict, reread_singers: bool) -> None:
        for path, (number, size, mtime_ns, present) in known.items():
            stamp = files.get(path)
            if stamp is None:
                if present:
                    self._connection.execute("UPDATE songs SET present = 0 WHERE number = ?", (number,))
            elif reread_singers or not present or stamp != (size, mtime_ns):
                self._connection.execute(
                    "UPDATE songs SET size = ?, mtime_ns = ?, singer = ?, present = 1 WHERE number = ?",
                    (*stamp, read_singer(path), number),
                )

    def _add_new(self, files: dict, new_paths: list[Path]) -> None:
        number = self._connection.execute("SELECT COALESCE(MAX(number), 0) FROM songs").fetchone()[0]
        for path in sorted(new_paths, key=_name_order):
            number += 1
            self._connection.execute(
                "INSERT INTO songs (number, path, size, mtime_ns, singer, present) VALUES (?, ?, ?, ?, ?, 1)",
                (number, os.fsencode(path), *files[path], read_singer(path)),
            )

    def _present_entries(self, root: Path, files: dict) -> list[CatalogEntry]:
        entries = []
        digest = hashlib.sha256(PDF_LAYOUT_VERSION.encode())
        rows = self._connection.execute(
            "SELECT number, path, singer FROM songs WHERE present = 1 ORDER BY number"
        )
        for number, raw_path, singer in rows:
            path = Path(os.fsdecode(raw_path))
            if path in files:
                entries.append(CatalogEntry(number, path, singer))
                digest.update(repr((number, raw_path, files[path], singer)).encode())
        self.signature = digest.hexdigest()
        entries.sort(key=lambda entry: _name_order(entry.path))
        return entries

    def pdf_is_current(self) -> bool:
        return self._meta("pdf_signature") == self.signature

    def mark_pdf_current(self) -> None:
        with self._connection:
            self._set_meta("pdf_signature", self.signature)

    def _meta(self, key: str) -> str | None:
        row = self._connection.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row[0] if row else None

    def _set_meta(self, key: str, value: str) -> None:
        self._connection.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


def _name_order(path: Path) -> tuple[str, str]:
    return (path.stem.casefold(), str(path).casefold())
