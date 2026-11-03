#!/usr/bin/env bash
# Builds dist/freekaraoke-x86_64.AppImage. Needs network access and Docker.
#
# FluidSynth comes from Ubuntu 22.04 rather than from the build machine, so that the
# AppImage runs on systems with glibc 2.35 or newer instead of only on recent ones.
set -euo pipefail

PYTHON_RELEASE="python3.12"
PYTHON_IMAGE="python3.12.15-cp312-cp312-manylinux_2_28_x86_64.AppImage"
PYTHON_URL="https://github.com/niess/python-appimage/releases/download/${PYTHON_RELEASE}/${PYTHON_IMAGE}"
TOOL_URL="https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-x86_64.AppImage"
LIBRARY_DISTRO="ubuntu:22.04"

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUILD="$ROOT/build/player"
OUTPUT="$ROOT/dist/freekaraoke-x86_64.AppImage"
mkdir -p "$BUILD" "$ROOT/dist"
cd "$BUILD"

[ -f "$ROOT/soundfonts/FluidR3_GM.sf2" ] || {
    echo "Falta soundfonts/FluidR3_GM.sf2 (veja o README)." >&2
    exit 1
}
[ -f "$PYTHON_IMAGE" ] || curl -fsSL -o "$PYTHON_IMAGE" "$PYTHON_URL"
[ -f appimagetool ] || curl -fsSL -o appimagetool "$TOOL_URL"
chmod +x "$PYTHON_IMAGE" appimagetool

rm -rf squashfs-root
APPIMAGE_EXTRACT_AND_RUN=1 "./$PYTHON_IMAGE" --appimage-extract >/dev/null
APPDIR="$BUILD/squashfs-root"
SITE="$APPDIR/opt/python3.12/lib/python3.12/site-packages"

"$APPDIR/AppRun" -m pip install --quiet --no-warn-script-location \
    "mido>=1.3,<2" packaging "numpy>=2,<3" "PySide6-Essentials>=6.8,<7" "PySide6-Addons>=6.8,<7" \
    chardet joblib jsonpickle more-itertools requests webcolors "soundfile>=0.13,<1"
# music21 reads MusicXML. Without its dependencies, because matplotlib is only for its graphs.
"$APPDIR/AppRun" -m pip install --quiet --no-warn-script-location --no-deps "music21>=9.1,<10"
for data in corpus musicxml/lilypondTestSuite; do
    find "$SITE/music21/$data" -type f ! -name '*.py' -delete
done
find "$SITE/music21/corpus" -type d -empty -delete

# Qt modules the player never uses make up most of the download.
QT="$SITE/PySide6"
UNUSED='WebEngine|WebView|WebChannel|WebSockets|3D|Charts|DataVis|Graphs|Location|Positioning|Sensors|SerialBus|SerialPort|Bluetooth|Nfc|Pdf|RemoteObjects|Scxml|StateMachine|SpatialAudio|TextToSpeech|Designer|Help|UiTools|Test|Sql|Quick|Qml|VirtualKeyboard|Lottie|ShaderTools|Svg[A-Z]|HttpServer|OpenGLWidgets'
find "$QT" -maxdepth 1 -regextype posix-extended -regex ".*/Qt($UNUSED).*" -delete
# Qt's video plugin links to these, although the player has no QML of its own.
NEEDED_BY_VIDEO='Quick\.|Qml\.|QmlModels|QmlMeta|QmlWorkerScript|MultimediaQuick'
find "$QT/Qt/lib" -maxdepth 1 -regextype posix-extended -regex ".*/libQt6($UNUSED).*" \
    ! -regex ".*/libQt6($NEEDED_BY_VIDEO).*" -delete
find "$QT/Qt/translations" -type f ! -name 'qtbase_pt*' -delete
rm -rf "$QT/Qt/qml" "$QT/Qt/resources" "$QT/Qt/metatypes" "$QT/Qt/libexec" \
    "$QT/include" "$QT/typesystems" "$QT/glue" "$QT/scripts" "$QT/examples"
find "$QT" -maxdepth 1 -type f -regextype posix-extended -regex ".*/(assistant|designer|linguist|lrelease|lupdate|qmake|qmlformat|qmlls|qmllint|qmlimportscanner|rcc|uic|balsam|balsamui|androiddeployqt).*" -delete
rm -rf "$QT/Qt/plugins/qmltooling" "$QT/Qt/plugins/qmllint" "$QT/Qt/plugins/sceneparsers" \
    "$QT/Qt/plugins/assetimporters" "$QT/Qt/plugins/designer" "$QT/Qt/plugins/sqldrivers" \
    "$QT/Qt/plugins/position" "$QT/Qt/plugins/geometryloaders" "$QT/Qt/plugins/renderers" \
    "$QT/Qt/plugins/renderplugins" "$QT/Qt/plugins/webview" "$QT/Qt/plugins/texttospeech" \
    "$QT/Qt/plugins/canbus" "$QT/Qt/plugins/sensors" "$QT/Qt/plugins/networkinformation"
find "$APPDIR" -type d -name __pycache__ -prune -exec rm -rf {} +
find "$SITE" -type d \( -name tests -o -name testing \) -path '*numpy*' -prune -exec rm -rf {} +

# FluidSynth and what it needs, each library finding the others next to itself. The C
# and C++ runtimes stay the host's, since they must match the host's other libraries.
mkdir -p "$APPDIR/usr/lib/fluid" "$APPDIR/usr/lib/qt-extra"
docker run --rm -v "$APPDIR/usr/lib:/out" "$LIBRARY_DISTRO" bash -euc '
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y -qq --no-install-recommends libfluidsynth3 libxcb-cursor0 libgssapi-krb5-2 patchelf >/dev/null
    keep_on_host="^(linux-vdso|ld-linux|libc|libm|libdl|librt|libpthread|libresolv|libnsl|libutil|libstdc\+\+|libgcc_s)\."
    library=$(ldconfig -p | grep -m1 "libfluidsynth.so.3 " | sed "s/.*=> //")
    cp -L "$library" /out/fluid/
    for dependency in $(ldd "$library" | awk "/=> \// {print \$3}"); do
        name=$(basename "$dependency")
        if ! [[ "$name" =~ $keep_on_host ]]; then
            cp -L "$dependency" /out/fluid/
        fi
    done
    # Keep the notices of each distributed library, as their licenses require.
    mkdir -p /out/licencas
    for file in /out/fluid/*; do
        package=$(dpkg -S "*/$(basename "$file")" 2>/dev/null | head -1 | cut -d: -f1)
        if [ -z "$package" ] || [ ! -f "/usr/share/doc/$package/copyright" ]; then
            echo "Sem aviso de licença para $(basename "$file")" >&2
            exit 1
        fi
        cp "/usr/share/doc/$package/copyright" "/out/licencas/$package.txt"
    done
    for file in /out/fluid/*; do
        patchelf --set-rpath "\$ORIGIN" "$file"
    done
    cp -L "$(ldconfig -p | grep -m1 "libxcb-cursor.so.0 " | sed "s/.*=> //")" /out/qt-extra/
    # Qt'"'"'s video plugin needs this one, which minimal systems do not install.
    gssapi=$(ldconfig -p | grep -m1 "libgssapi_krb5.so.2 " | sed "s/.*=> //")
    cp -L "$gssapi" /out/qt-extra/
    for dependency in $(ldd "$gssapi" | awk "/=> \// {print \$3}"); do
        name=$(basename "$dependency")
        if ! [[ "$name" =~ $keep_on_host ]]; then
            cp -L "$dependency" /out/qt-extra/
        fi
    done
    chown -R '"$(id -u):$(id -g)"' /out/fluid /out/qt-extra /out/licencas
'

APP="$APPDIR/usr/app"
mkdir -p "$APP/soundfonts"
cp "$ROOT"/{player,background,catalog,conversor,i18n,kar,library,recorder,score,self_test,song,song_list_pdf,st3,st3_writer,synth,theme,tolerant_midi}.py "$APP/"
cp -r "$ROOT/fonts" "$ROOT/icons" "$APP/"
cp "$ROOT/soundfonts/FluidR3_GM.sf2" "$ROOT/soundfonts/FluidR3_GM.copyright.txt" "$APP/soundfonts/"
cp "$ROOT/LICENSE" "$ROOT/README.md" "$APP/"
cp -r "$ROOT/licenses" "$APP/"
mkdir -p "$APPDIR/usr/share/doc"
mv "$APPDIR/usr/lib/licencas" "$APPDIR/usr/share/doc/licencas"

rm -f "$APPDIR"/*.desktop "$APPDIR"/*.png "$APPDIR/.DirIcon"
rm -f "$APPDIR/usr/share/applications"/*.desktop "$APPDIR/usr/share/metainfo"/*
cp "$ROOT/icons/freekaraoke.png" "$APPDIR/freekaraoke.png"
cat > "$APPDIR/freekaraoke.desktop" <<'DESKTOP'
[Desktop Entry]
Type=Application
Name=FreeKaraoke
Comment=Player de karaokê para arquivos .st3, .kar, .mid e MusicXML
Exec=freekaraoke %f
Icon=freekaraoke
Terminal=false
Categories=AudioVideo;Audio;Player;
DESKTOP

mv "$APPDIR/AppRun" "$APPDIR/AppRun.python"
cat > "$APPDIR/AppRun" <<'APPRUN'
#!/bin/sh
HERE="$(dirname "$(readlink -f "$0")")"
export PYTHONDONTWRITEBYTECODE=1
export KARAOKE_FLUIDSYNTH_LIB="$HERE/usr/lib/fluid/libfluidsynth.so.3"
# Libraries that Qt's plugins need and that many systems do not install.
export LD_LIBRARY_PATH="$HERE/usr/lib/qt-extra${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
exec "$HERE/AppRun.python" "$HERE/usr/app/player.py" "$@"
APPRUN
chmod +x "$APPDIR/AppRun"
chmod -R a+rX "$APPDIR"

du -sh "$APPDIR"
ARCH=x86_64 APPIMAGE_EXTRACT_AND_RUN=1 ./appimagetool --no-appstream "$APPDIR" "$OUTPUT"
ls -lh "$OUTPUT"
