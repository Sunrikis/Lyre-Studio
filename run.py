"""Source-checkout launcher; does not require pip installation."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from lyre_studio.web_launcher import main

if __name__ == "__main__":
    raise SystemExit(main())
