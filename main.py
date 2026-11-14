"""Start-file shim for hosts that run a fixed file (e.g. `python main.py`)."""
import sys

from mirrorbot.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
