"""Flask front end for the MusicXML/MIDI to .st3 or .kar converter."""

from __future__ import annotations

from i18n import tr

import io
import re
import tempfile
from pathlib import Path

from flask import Flask, render_template, request, send_file
from werkzeug.utils import secure_filename

from conversor import DEFAULT_FORMAT, FORMATS, convert_file
from kar import SUPPORTED_SUFFIXES, ConversionError

MUSESCORE_URL = re.compile(
    r"^https://(?:www\.)?musescore\.com/(?:[\w.-]+/)*scores/(\d+)(?:[/?#].*)?$"
)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024
app.jinja_env.globals["tr"] = tr


@app.get("/")
def index():
    return _page()


@app.post("/converter")
def converter():
    url = request.form.get("url", "").strip()
    match = MUSESCORE_URL.match(url)
    if url and not match:
        return _page(tr("A URL não parece ser de uma partitura do MuseScore."), 400)

    upload = request.files.get("arquivo")
    if upload is None or not upload.filename:
        return _page(tr("Envie o arquivo MIDI ou MusicXML da partitura."), 400)
    suffix = Path(upload.filename).suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        return _page(tr("Formato não suportado: {0}.").format(suffix or tr('sem extensão')), 400)
    output_format = request.form.get("formato", DEFAULT_FORMAT)
    if output_format not in FORMATS:
        return _page(tr("Formato de saída inválido."), 400)

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"entrada{suffix}"
        upload.save(path)
        try:
            data, title = convert_file(
                path,
                output_format,
                title=request.form.get("titulo", ""),
                artist=request.form.get("artista", ""),
            )
        except ConversionError as exc:
            return _page(str(exc), 422)

    fallback = f"musescore-{match.group(1)}" if match else "karaoke"
    name = secure_filename(title) or fallback
    return send_file(
        io.BytesIO(data),
        mimetype="audio/midi" if output_format == "kar" else "application/octet-stream",
        as_attachment=True,
        download_name=f"{name}.{output_format}",
    )


@app.errorhandler(413)
def too_large(_error):
    return _page(tr("Arquivo muito grande (máximo de 20 MB)."), 413)


def _page(error: str | None = None, status: int = 200):
    return render_template("index.html", error=error), status


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000)
