"""Entry point of the Windows build, whose window has no console to show errors in."""

import os
import sys
import tempfile
import traceback

CRASH_LOG = os.path.join(tempfile.gettempdir(), "freekaraoke-erro.txt")


def main() -> int:
    # With no console these are None, and any library that prints would crash the program.
    if sys.stdout is None:
        sys.stdout = open(os.devnull, "w", encoding="utf-8")
    if sys.stderr is None:
        sys.stderr = open(os.devnull, "w", encoding="utf-8")
    try:
        import player

        return player.main()
    except Exception:
        with open(CRASH_LOG, "w", encoding="utf-8") as log:
            traceback.print_exc(file=log)
        return 1


if __name__ == "__main__":
    sys.exit(main())
