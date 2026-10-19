"""Absolute-path STDIO entry for Codex, independent of working directory."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / 'src'))
from omni.mcp_server import main

if __name__ == '__main__':
    main()
