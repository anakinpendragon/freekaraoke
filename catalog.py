"""Song files in a folder and how each one is listed, with its singer."""

from __future__ import annotations

import collections
import html
import re
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path

from song import SUPPORTED_SUFFIXES, clean_singer, singer_from_titles

# Enough to reach the lyric track's @T events in every file of a large collection.
KAR_READ_LIMIT = 32768
ST3_MAGIC = b"STAR DATA"
OTHERS = "Others"
ST3_SINGER_FIELD = (0x84, 0xC4)
_KAR_TITLE = re.compile(rb"\xff\x01([\x02-\x7f])@T", re.DOTALL)
_SCORE_ROOT = re.compile(rb"<score-(partwise|timewise)\b")
_SCORE_COMPOSER = re.compile(rb"<creator\b[^>]*\btype=[\"']composer[\"'][^>]*>([^<]*)<")


@dataclass(frozen=True)
class CatalogEntry:
    number: int
    path: Path
    singer: str = ""

    @property
    def name(self) -> str:
        return self.path.stem

    @property
    def label(self) -> str:
        return f"{self.number:04d}  {self.name} — {self.singer or OTHERS}"


def find_songs(folder: str | Path) -> list[Path]:
    """Find songs in the folder and below it."""
    return [
        path
        for path in Path(folder).rglob("*")
        if path.suffix.lower() in SUPPORTED_SUFFIXES and path.is_file() and _is_song(path)
    ]


def _is_song(path: Path) -> bool:
    """A .xml file can be anything, so only a real score counts as a song."""
    if path.suffix.lower() != ".xml":
        return True
    try:
        with path.open("rb") as file:
            return bool(_SCORE_ROOT.search(file.read(KAR_READ_LIMIT)))
    except OSError:
        return False


def read_singer(path: Path) -> str:
    """Read only what is needed, so listing a large folder stays instant."""
    if path.suffix.lower() == ".mxl":
        return _singer_in_score(_read_compressed_score(path))
    try:
        with path.open("rb") as file:
            data = file.read(KAR_READ_LIMIT)
    except OSError:
        return ""
    if path.suffix.lower() in (".musicxml", ".xml"):
        return _singer_in_score(data)
    if data.startswith(ST3_MAGIC):
        start, end = ST3_SINGER_FIELD
        return clean_singer(_decode(data[start:end].split(b"\0", 1)[0]))
    titles = []
    for match in _KAR_TITLE.finditer(data):
        length = match.group(1)[0]
        titles.append(_decode(data[match.start() + 3 : match.start() + 3 + length])[2:])
    return singer_from_titles(titles)


def _read_compressed_score(path: Path) -> bytes:
    try:
        with zipfile.ZipFile(path) as archive:
            names = [
                name
                for name in archive.namelist()
                if name.lower().endswith((".xml", ".musicxml")) and not name.upper().startswith("META-INF/")
            ]
            return archive.read(names[0])[:KAR_READ_LIMIT] if names else b""
    except (OSError, zipfile.BadZipFile, KeyError, RuntimeError):
        return b""


def _singer_in_score(data: bytes) -> str:
    match = _SCORE_COMPOSER.search(data)
    return clean_singer(html.unescape(_decode(match.group(1)))) if match else ""


def _decode(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", "replace")


def singer_key(singer: str) -> str:
    """Comparison form: ignores case, accents, punctuation and a leading "The"."""
    decomposed = unicodedata.normalize("NFKD", singer)
    plain = "".join(char for char in decomposed if not unicodedata.combining(char)).casefold()
    words = re.sub(r"[^\w&]+", " ", plain).split()
    if len(words) > 1 and words[0] == "the":
        words = words[1:]
    return " ".join(words)


def singer_names(entries: list[CatalogEntry]) -> list[str]:
    """One name per singer, in its most common spelling, sorted alphabetically."""
    spellings: dict[str, collections.Counter] = {}
    for entry in entries:
        if entry.singer:
            spellings.setdefault(singer_key(entry.singer), collections.Counter())[entry.singer] += 1
    return [
        spellings[key].most_common(1)[0][0]
        for key in sorted(spellings)
    ]
