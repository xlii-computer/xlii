"""Frozen entry point for the T0 sidecar spike.

Trivial by design: the sidecar IS the xlii CLI. The Tauri handshake mode
(`xlii serve --ws --handshake`, W2) rides the same entry point later.
"""

import sys

from xlii.cli import main

if __name__ == "__main__":
    sys.exit(main())
