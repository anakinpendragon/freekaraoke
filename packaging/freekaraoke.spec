# PyInstaller recipe for the Windows build. Expects FluidSynth's DLLs in build/fluidsynth/bin.
from pathlib import Path

import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo,
    StringFileInfo,
    StringStruct,
    StringTable,
    VarFileInfo,
    VarStruct,
    VSVersionInfo,
)

ROOT = Path(SPECPATH).parent
fluidsynth_dlls = [(str(path), "fluidsynth") for path in (ROOT / "build" / "fluidsynth" / "bin").glob("*.dll")]
if not fluidsynth_dlls:
    raise SystemExit("As DLLs da FluidSynth não estão em build/fluidsynth/bin.")

version = os.environ.get("FREEKARAOKE_VERSION", "0.0.0")
version_numbers = tuple(int(part) for part in version.split(".")) + (0,)
version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=version_numbers, prodvers=version_numbers, fileType=1),
    kids=[
        StringFileInfo(
            [
                StringTable(
                    "040904B0",
                    [
                        StringStruct("CompanyName", "Anakinpendragon"),
                        StringStruct("FileDescription", "FreeKaraoke"),
                        StringStruct("FileVersion", version),
                        StringStruct("InternalName", "FreeKaraoke"),
                        StringStruct("LegalCopyright", "Copyright (C) 2026 Anakinpendragon. GPLv3."),
                        StringStruct("OriginalFilename", "FreeKaraoke.exe"),
                        StringStruct("ProductName", "FreeKaraoke"),
                        StringStruct("ProductVersion", version),
                    ],
                )
            ]
        ),
        VarFileInfo([VarStruct("Translation", [1033, 1200])]),
    ],
)
# Each package's own license files travel with its metadata.
licensed_packages = ["mido", "numpy", "music21", "chardet", "joblib", "jsonpickle", "more-itertools", "requests", "webcolors", "packaging", "PySide6-Essentials", "PySide6-Addons", "shiboken6", "certifi", "charset-normalizer", "idna", "urllib3"]
python_license = Path(sys.base_prefix) / "LICENSE.txt"

analysis = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT)],
    binaries=fluidsynth_dlls,
    datas=[
        (str(ROOT / "LICENSE"), "."),
        (str(ROOT / "licenses"), "licenses"),
        *[(str(python_license), "licenses/python")][: python_license.is_file()],
        *[item for package in licensed_packages for item in copy_metadata(package)],
        (str(ROOT / "README.md"), "."),
        (str(ROOT / "fonts"), "fonts"),
        (str(ROOT / "icons"), "icons"),
        (str(ROOT / "soundfonts" / "FluidR3_GM.sf2"), "soundfonts"),
        (str(ROOT / "soundfonts" / "FluidR3_GM.copyright.txt"), "soundfonts"),
        # music21's sample scores take most of its size and are never used.
        *collect_data_files("music21", excludes=["corpus/**", "musicxml/lilypondTestSuite/**"]),
    ],
    # music21 and these modules are imported only when a score is opened or a check runs.
    hiddenimports=[*collect_submodules("music21"), "player", "self_test", "conversor", "st3_writer", "kar"],
    excludes=["matplotlib", "tkinter", "scipy", "IPython", "pytest", "flask"],
)
archive = PYZ(analysis.pure)
program = EXE(
    archive,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="FreeKaraoke",
    console=False,
    disable_windowed_traceback=True,
    icon=str(ROOT / "packaging" / "freekaraoke.ico"),
    version=version_info,
)
COLLECT(program, analysis.binaries, analysis.datas, name="FreeKaraoke")
