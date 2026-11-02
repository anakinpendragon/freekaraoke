# PyInstaller recipe for the Windows build. Expects FluidSynth's DLLs in build/fluidsynth/bin.
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent
fluidsynth_dlls = [(str(path), "fluidsynth") for path in (ROOT / "build" / "fluidsynth" / "bin").glob("*.dll")]
if not fluidsynth_dlls:
    raise SystemExit("As DLLs da FluidSynth não estão em build/fluidsynth/bin.")

analysis = Analysis(
    [str(ROOT / "packaging" / "entry.py")],
    pathex=[str(ROOT)],
    binaries=fluidsynth_dlls,
    datas=[
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
)
COLLECT(program, analysis.binaries, analysis.datas, name="FreeKaraoke")
