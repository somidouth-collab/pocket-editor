#!/usr/bin/env python3
"""OS-detected user-local relay setup and launch."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pocket_editor_plugins._team_relay import main

if __name__ == '__main__':
    raise SystemExit(main())
