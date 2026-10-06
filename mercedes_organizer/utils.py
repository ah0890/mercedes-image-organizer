"""Shared helpers: text normalisation, safe filesystem paths, hashing, logging."""

from __future__ import annotations

import hashlib
import logging
import os
import re
import unicodedata
from pathlib import Path

LOGGER_NAME = "mercedes_organizer"

# Characters Windows does not allow in folder names.
_INVALID_FS_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def strip_accents(text: str) -> str:
    """'coupé' -> 'coupe', 'Citroën' -> 'Citroen'."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize_text(text: str) -> str:
    """Lower-case, strip accents, unify dashes, collapse whitespace."""
    text = strip_accents(text).lower()
    text = text.replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", text).strip()


def normalize_name(text: str) -> str:
    """Normalise a filename / page identifier for exact-but-tolerant comparison.

    Treats spaces, underscores and hyphens as equivalent, ignores case,
    a trailing ``.webp`` extension and duplicate whitespace.
    """
    text = normalize_text(text)
    text = re.sub(r"\.webp$", "", text)
    text = re.sub(r"[\s_\-]+", " ", text)
    return text.strip()


def safe_folder_name(name: str) -> str:
    """Make a category name usable as a Windows/macOS/Linux folder name."""
    cleaned = _INVALID_FS_CHARS.sub("_", name).strip().rstrip(".")
    return cleaned or "_unnamed"


def fs_path(path: Path) -> str:
    """Return a path string that also works beyond Windows' 260-char MAX_PATH.

    Some source filenames are ~200 characters long, so the destination path
    can exceed MAX_PATH. On Windows the ``\\\\?\\`` prefix lifts that limit.
    """
    resolved = str(Path(path).resolve())
    if os.name == "nt" and not resolved.startswith("\\\\?\\"):
        return "\\\\?\\" + resolved
    return resolved


def file_md5(path: Path, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.md5()
    with open(fs_path(path), "rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def setup_logging(log_file: Path, verbose: bool = False) -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    file_handler = logging.FileHandler(log_file, mode="w", encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s")
    )
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG if verbose else logging.ERROR)
    console_handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    logger.addHandler(console_handler)
    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)
