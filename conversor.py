"""Converter from MusicXML (or MIDI with lyrics) to .st3 or .kar karaoke files."""

from __future__ import annotations

from i18n import tr

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
        description=tr("Cria um arquivo de karaokê .st3 ou .kar a partir de uma partitura MusicXML com letra."),
    )
    parser.add_argument("entrada", metavar=tr("entrada"), type=Path, help=tr("arquivo .musicxml, .mxl ou .xml"))
    parser.add_argument("saida", metavar=tr("saida"), type=Path, nargs="?", help=tr("arquivo de saída (padrão: ao lado da entrada)"))
    parser.add_argument("--formato", "--format", dest="formato", choices=FORMATS, help=tr("formato de saída (padrão: {0}, ou o da extensão da saída)").format(DEFAULT_FORMAT))
    parser.add_argument("--titulo", "--title", dest="titulo", default="", help=tr("título da música (padrão: o da partitura)"))
    parser.add_argument("--artista", "--artist", dest="artista", default="", help=tr("artista (padrão: o compositor da partitura)"))
    parser.add_argument("-f", "--sobrescrever", "--overwrite", dest="sobrescrever", action="store_true", help=tr("substitui a saída se ela já existir"))
    options = parser.parse_args(arguments)

    source: Path = options.entrada
    output_format = options.formato or _format_from_target(options.saida) or DEFAULT_FORMAT
    target: Path = options.saida or source.with_suffix(f".{output_format}")
    if not source.is_file():
        return _fail(tr("Arquivo não encontrado: {0}").format(source))
    if source.suffix.lower() not in MUSICXML_SUFFIXES:
        return _fail(tr("A entrada precisa ser MusicXML (.musicxml, .mxl ou .xml): {0}").format(source))
    if target.exists() and not options.sobrescrever:
        return _fail(tr("A saída já existe: {0}. Use --sobrescrever para substituir.").format(target))

    try:
        data, title = convert_file(source, output_format, options.titulo, options.artista)
        target.write_bytes(data)
    except ConversionError as exc:
        return _fail(str(exc))
    except OSError as exc:
        return _fail(tr("Não consegui gravar {0}: {1}").format(target, exc.strerror or exc))

    print(tr("Criado: {0}").format(target) + (f" ({title})" if title else ""))
    return 0


def _format_from_target(target: Path | None) -> str | None:
    suffix = target.suffix.lower().lstrip(".") if target else ""
    return suffix if suffix in FORMATS else None


def _fail(message: str) -> int:
    print(tr("Erro: {0}").format(message), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
