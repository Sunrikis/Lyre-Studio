"""Portable paths: installed package resources are never modified."""

import os
from pathlib import Path
import shutil

PACKAGE_ROOT = Path(__file__).resolve().parent


def source_root():
    candidate = PACKAGE_ROOT.parent.parent
    return (
        candidate
        if (candidate / "run.py").is_file()
        and (candidate / "src" / "lyre_studio").resolve() == PACKAGE_ROOT
        else None
    )


def song_directory(value=None):
    value = value or os.environ.get("LYRE_STUDIO_SONGS")
    if value:
        return Path(value).expanduser().resolve()
    root = source_root()
    return root / "songs" if root else Path.home() / ".lyre-studio" / "songs"


def initialize_library(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    if not any(directory.glob("*.json")):
        shutil.copyfile(PACKAGE_ROOT / "examples" / "practice.json", directory / "practice.json")
    return directory
