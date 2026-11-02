"""Lists every file of the built program for the MSI, keeping its folders.

Usage: make_msi_files.py PROGRAM_FOLDER OUTPUT.wxs
Each file gets its own component, as Windows Installer recommends.
"""

import sys
from pathlib import Path
from xml.sax.saxutils import quoteattr


def describe(program: Path) -> str:
    files = sorted(path for path in program.rglob("*") if path.is_file())
    if not files:
        raise SystemExit(f"Nenhum arquivo em {program}")

    directory_ids = {Path("."): "INSTALLFOLDER"}
    for folder in sorted({parent for path in files for parent in path.relative_to(program).parents}):
        if folder != Path("."):
            directory_ids[folder] = f"dir{len(directory_ids):05d}"

    return "".join(
        [
            '<?xml version="1.0" encoding="UTF-8"?>\n',
            '<Wix xmlns="http://wixtoolset.org/schemas/v4/wxs">\n  <Fragment>\n',
            '    <DirectoryRef Id="INSTALLFOLDER">\n',
            _directories(Path("."), directory_ids, 3),
            "    </DirectoryRef>\n",
            '    <ComponentGroup Id="ProgramFiles">\n',
            *(_component(index, path, program, directory_ids) for index, path in enumerate(files)),
            "    </ComponentGroup>\n  </Fragment>\n</Wix>\n",
        ]
    )


def _directories(parent: Path, directory_ids: dict, depth: int) -> str:
    text = ""
    for folder in sorted(directory_ids):
        if folder != Path(".") and folder.parent == parent:
            indent = "  " * depth
            text += f"{indent}<Directory Id={quoteattr(directory_ids[folder])} Name={quoteattr(folder.name)}>\n"
            text += _directories(folder, directory_ids, depth + 1)
            text += f"{indent}</Directory>\n"
    return text


def _component(index: int, path: Path, program: Path, directory_ids: dict) -> str:
    directory = directory_ids[path.relative_to(program).parent]
    return (
        f"      <Component Id=\"cmp{index:05d}\" Directory={quoteattr(directory)}>"
        f"<File Id=\"fil{index:05d}\" Source={quoteattr(str(path.resolve()))} KeyPath=\"yes\" /></Component>\n"
    )


if __name__ == "__main__":
    Path(sys.argv[2]).parent.mkdir(parents=True, exist_ok=True)
    Path(sys.argv[2]).write_text(describe(Path(sys.argv[1])), encoding="utf-8")
