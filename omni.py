"""Development entry point; works without installation and from any cwd."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from omni.cli import main

if __name__ == "__main__":
    main()
