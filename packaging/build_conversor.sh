#!/usr/bin/env bash
# Builds dist/conversor-x86_64.AppImage. Needs network access on the first run.
set -euo pipefail

PYTHON_RELEASE="python3.12"
PYTHON_IMAGE="python3.12.15-cp312-cp312-manylinux_2_28_x86_64.AppImage"
PYTHON_URL="https://github.com/niess/python-appimage/releases/download/${PYTHON_RELEASE}/${PYTHON_IMAGE}"
TOOL_URL="https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD="$ROOT/build/conversor"
OUTPUT="$ROOT/dist/conversor-x86_64.AppImage"
mkdir -p "$BUILD" "$ROOT/dist"
cd "$BUILD"

[ -f "$PYTHON_IMAGE" ] || curl -fsSL -o "$PYTHON_IMAGE" "$PYTHON_URL"
[ -f appimagetool ] || curl -fsSL -o appimagetool "$TOOL_URL"
chmod +x "$PYTHON_IMAGE" appimagetool

rm -rf squashfs-root
APPIMAGE_EXTRACT_AND_RUN=1 "./$PYTHON_IMAGE" --appimage-extract >/dev/null
APPDIR="$BUILD/squashfs-root"
PYTHON="$APPDIR/AppRun"
SITE="$APPDIR/opt/python3.12/lib/python3.12/site-packages"

# matplotlib is only needed for music21's graphs, which the converter never draws.
"$PYTHON" -m pip install --quiet --no-warn-script-location --no-deps \
    "mido>=1.3,<2" "packaging" "music21>=9.1,<10"
"$PYTHON" -m pip install --quiet --no-warn-script-location \
    chardet joblib jsonpickle more-itertools numpy requests webcolors

# Sample scores take most of music21's size. Their .py files stay: music21 imports them.
for data in corpus musicxml/lilypondTestSuite; do
    find "$SITE/music21/$data" -type f ! -name '*.py' -delete
done
find "$SITE/music21/corpus" -type d -empty -delete
find "$APPDIR" -type d -name __pycache__ -prune -exec rm -rf {} +

mkdir -p "$APPDIR/usr/app"
cp "$ROOT"/{conversor,i18n,kar,st3,st3_writer,tolerant_midi}.py "$ROOT/LICENSE" "$ROOT/README.md" "$APPDIR/usr/app/"
cp -r "$ROOT/licenses" "$APPDIR/usr/app/"

rm -f "$APPDIR"/*.desktop "$APPDIR/.DirIcon"
rm -f "$APPDIR"/*.png
cp "$ROOT/icons/freekaraoke.png" "$APPDIR/conversor.png"
cat > "$APPDIR/conversor.desktop" <<'DESKTOP'
[Desktop Entry]
Type=Application
Name=Conversor de karaoke
Comment=Cria arquivos de karaokê .st3 ou .kar a partir de partituras MusicXML
Exec=conversor
Icon=conversor
Terminal=true
Categories=AudioVideo;Audio;
DESKTOP
rm -f "$APPDIR/usr/share/applications"/*.desktop "$APPDIR/usr/share/metainfo"/*

mv "$APPDIR/AppRun" "$APPDIR/AppRun.python"
cat > "$APPDIR/AppRun" <<'APPRUN'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
export PYTHONDONTWRITEBYTECODE=1
exec "$HERE/AppRun.python" "$HERE/usr/app/conversor.py" "$@"
APPRUN
chmod +x "$APPDIR/AppRun"
chmod -R a+rX "$APPDIR"

ARCH=x86_64 APPIMAGE_EXTRACT_AND_RUN=1 ./appimagetool --no-appstream "$APPDIR" "$OUTPUT"
ls -lh "$OUTPUT"
