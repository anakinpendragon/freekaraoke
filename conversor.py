"""Converter from MusicXML (or MIDI with lyrics) to .st3 or .kar karaoke files."""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

from kar import MUSICXML_SUFFIXES, ConversionError, build_kar, load_source
from st3_writer import build_st3

FORMATS = ("st3", "kar")
DEFAULT_FORMAT = "st3"


def convert_file(path: Path, output_format: str = DEFAULT_FORMAT, title: str = "", artist: str = "") -> tuple[bytes, str]:
    """Return the converted file's bytes and the title that was used."""
    song = load_source(path)
    final_title = title.strip() or song.title
    final_artist = artist.strip() or song.artist
    if output_format == "st3":
        try:
            return build_st3(song, final_title, final_artist), final_title
        except ValueError as exc:
            raise ConversionError(str(exc)) from exc
    buffer = io.BytesIO()
    build_kar(song, final_title, final_artist).save(file=buffer)
    return buffer.getvalue(), final_title


def main(arguments: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="conversor",
        description="Cria um arquivo de karaokê .st3 ou .kar a partir de uma partitura MusicXML com letra.",
    )
    parser.add_argument("entrada", type=Path, help="arquivo .musicxml, .mxl ou .xml")
    parser.add_argument("saida", type=Path, nargs="?", help="arquivo de saída (padrão: ao lado da entrada)")
    parser.add_argument("--formato", choices=FORMATS, help=f"formato de saída (padrão: {DEFAULT_FORMAT}, ou o da extensão da saída)")
    parser.add_argument("--titulo", default="", help="título da música (padrão: o da partitura)")
    parser.add_argument("--artista", default="", help="artista (padrão: o compositor da partitura)")
    parser.add_argument("-f", "--sobrescrever", action="store_true", help="substitui a saída se ela já existir")
    options = parser.parse_args(arguments)

    source: Path = options.entrada
    output_format = options.formato or _format_from_target(options.saida) or DEFAULT_FORMAT
    target: Path = options.saida or source.with_suffix(f".{output_format}")
    if not source.is_file():
        return _fail(f"Arquivo não encontrado: {source}")
    if source.suffix.lower() not in MUSICXML_SUFFIXES:
        return _fail(f"A entrada precisa ser MusicXML (.musicxml, .mxl ou .xml): {source}")
    if target.exists() and not options.sobrescrever:
        return _fail(f"A saída já existe: {target}. Use --sobrescrever para substituir.")

    try:
        data, title = convert_file(source, output_format, options.titulo, options.artista)
        target.write_bytes(data)
    except ConversionError as exc:
        return _fail(str(exc))
    except OSError as exc:
        return _fail(f"Não consegui gravar {target}: {exc.strerror or exc}")

    print(f"Criado: {target}" + (f" ({title})" if title else ""))
    return 0


def _format_from_target(target: Path | None) -> str | None:
    suffix = target.suffix.lower().lstrip(".") if target else ""
    return suffix if suffix in FORMATS else None


def _fail(message: str) -> int:
    print(f"Erro: {message}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
