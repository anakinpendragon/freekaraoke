#!/bin/bash
# Runs inside a clean distribution: $1 is its package manager. Installs only what any
# desktop already has, then runs the AppImage's own checks.
set -u
case "$1" in
    apt)
        export DEBIAN_FRONTEND=noninteractive
        apt-get update -qq >/dev/null 2>&1
        apt-get install -y -qq --no-install-recommends libegl1 libgl1 libxkbcommon0 libxkbcommon-x11-0 libdbus-1-3 \
            libfontconfig1 libglib2.0-0 libx11-6 libx11-xcb1 libxext6 libxrandr2 libxrender1 libsm6 libice6 \
            libxcb-icccm4 libxcb-image0 libxcb-keysyms1 libxcb-randr0 libxcb-render-util0 libxcb-shape0 \
            libxcb-xfixes0 libxcb-xkb1 libxcb-sync1 libxcb-shm0 libwayland-client0 libwayland-cursor0 \
            libwayland-egl1 libpulse0 >/dev/null 2>&1
        ;;
    dnf)
        dnf install -y -q mesa-libEGL mesa-libGL libxkbcommon libxkbcommon-x11 dbus-libs fontconfig glib2 libX11 \
            libX11-xcb libXext libXrandr libXrender libSM libICE xcb-util-wm xcb-util-image xcb-util-keysyms \
            xcb-util-renderutil libwayland-client libwayland-cursor libwayland-egl pulseaudio-libs >/dev/null 2>&1
        ;;
    pacman)
        pacman -Syu --noconfirm --needed libglvnd libxkbcommon libxkbcommon-x11 dbus fontconfig glib2 libx11 libxext \
            libxrandr libxrender libsm libice xcb-util-wm xcb-util-image xcb-util-keysyms xcb-util-renderutil \
            wayland libpulse >/dev/null 2>&1
        ;;
esac
cd /tmp
APPIMAGE_EXTRACT_AND_RUN=1 HOME=/tmp QT_QPA_PLATFORM=offscreen timeout 240 \
    /dist/freekaraoke-x86_64.AppImage --self-test /tmp/result.txt /video/video-teste.mp4 >/tmp/out.txt 2>&1
code=$?
cat /tmp/result.txt 2>/dev/null || { echo "O programa não chegou a rodar (saída $code):"; tail -8 /tmp/out.txt; }
exit "$code"
