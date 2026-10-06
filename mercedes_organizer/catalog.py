"""Index the source folder once: every file, normalised name, context, SHA-256.

Matching never rescans the disk - it searches these in-memory records.

Normalisation (both filenames and identifiers):
  * lower case, accents stripped ('coupé' -> 'coupe'), en/em dashes -> '-'
  * extension removed (.webp / .WEBP)
  * separators between a letter and a digit removed, so
    'A 180', 'A-180', 'A_180' -> 'a180'   and   'W 176', 'W-176', 'W_176' -> 'w176'

Context: a file "belongs" to every Markdown category whose name appears in it
as a whole word ('a-class', 'gla', 'cl', 'amg gt'). A Mercedes file that names
no category at all (e.g. 'Mercedes_W246.webp') is open to every category.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .utils import fs_path, get_logger, strip_accents

WEBP_EXT = ".webp"
# Words that identify a file as a Mercedes image even when it names no category.
BRAND_WORDS = {"mercedes", "benz", "amg"}

_LETTER_SEP_DIGIT = re.compile(r"(?<=[a-z])[\s_\-]+(?=\d)")
_CHASSIS_TOKEN = re.compile(r"^(?P<letters>[a-z]{1,2})\d{3}[a-z]?$")
# AMG models carry a 2-digit badge (c63, a45, gla35, gt55); regular badges are 3-digit.
# The bare word "amg" is not used: it also appears in colours ("amg green hell magno")
# and trim packages ("amg line").
# Mercedes names its model families "<letters>-Class"; a file naming such a family that is
# not in the Markdown (e.g. "clc-class") belongs to that family, not to every category.
_ANY_CLASS = re.compile(r"(?<![a-z0-9])[a-z]{1,4} class(?![a-z0-9])")
_AMG_BADGE = re.compile(r"^[a-z]{1,3}\d{2}[a-z]?$")
# Camera-angle suffix ("-45-angle-front-view", "-back-view"). Removed before identifier
# extraction, otherwise "...-sl-45-angle-..." would read as the badge "SL45".
_VIEW_SUFFIX = re.compile(r"[-_ ]((?:\d+[-_ ]angle[-_ ])?(?:front|back|rear|side|flat[-_ ]side|top)(?:[-_ ][a-z]+)*[-_ ]view)$",
                          re.I)


def base_normalize(text: str) -> str:
    text = strip_accents(text).lower().replace("–", "-").replace("—", "-")
    text = re.sub(r"\.webp$", "", text.strip())
    return re.sub(r"\s+", " ", text)


def compact(text: str) -> str:
    """Normalised form used for identifier search: 'A-180' -> 'a180'."""
    return _LETTER_SEP_DIGIT.sub("", base_normalize(text))


def spaced(text: str) -> str:
    """Normalised form used for word search: hyphens/underscores -> spaces."""
    return re.sub(r"[\s_\-]+", " ", base_normalize(text)).strip()


def word_pattern(word: str) -> re.Pattern:
    return re.compile(r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])")


@dataclass
class SourceFile:
    path: Path                    # absolute path
    rel_path: str                 # relative to the source root
    is_webp: bool
    sha256: str = ""
    compact: str = ""
    spaced: str = ""
    tokens: tuple[str, ...] = ()
    contexts: frozenset[str] = frozenset()   # category names this file mentions
    primary_context: str | None = None       # the category named first in the filename
    foreign_class: str | None = None         # an "x-class" name that is NOT a Markdown category
    is_mercedes: bool = False
    is_amg: bool = False
    duplicate_of: str | None = None          # rel_path of the identical canonical file
    duplicates: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return Path(self.rel_path).name

    def chassis_tokens(self, own_prefixes: frozenset[str]) -> set[str]:
        """Chassis-looking tokens ('w176', 'c209'), ignoring the page's own model tokens ('a200')."""
        out = set()
        for token in self.tokens:
            m = _CHASSIS_TOKEN.match(token)
            if m and m["letters"] not in own_prefixes:
                out.add(token)
        return out


@dataclass
class Catalog:
    root: Path
    files: list[SourceFile]

    @property
    def webp(self) -> list[SourceFile]:
        return [f for f in self.files if f.is_webp]

    @property
    def canonical_webp(self) -> list[SourceFile]:
        return [f for f in self.webp if f.duplicate_of is None]

    @property
    def duplicate_webp(self) -> list[SourceFile]:
        return [f for f in self.webp if f.duplicate_of is not None]

    @property
    def other_files(self) -> list[SourceFile]:
        return [f for f in self.files if not f.is_webp]


def sha256_of(path: Path, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(fs_path(path), "rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_catalog(root: Path, category_names: list[str]) -> Catalog:
    log = get_logger()
    log.info("Scanning source folder recursively: %s", root)
    context_patterns = {name: word_pattern(spaced(name)) for name in category_names}
    brand_patterns = [word_pattern(w) for w in BRAND_WORDS]

    files: list[SourceFile] = []
    root_resolved = root.resolve()
    for dirpath, _dirs, filenames in os.walk(fs_path(root)):
        for name in sorted(filenames):
            full = Path(dirpath.removeprefix("\\\\?\\")) / name
            rel = os.path.relpath(full, root_resolved)
            is_webp = os.path.splitext(name)[1].lower() == WEBP_EXT
            record = SourceFile(full, rel, is_webp, sha256_of(full))
            if is_webp:
                stem = _VIEW_SUFFIX.sub("", name[: -len(WEBP_EXT)])
                record.compact = compact(stem)
                record.spaced = spaced(stem)
                record.tokens = tuple(t for t in re.split(r"[^a-z0-9.]+", record.compact) if t)
                positions = {}
                for cat_name, pattern in context_patterns.items():
                    m = pattern.search(record.spaced)
                    if m:
                        positions[cat_name] = m.start()
                record.contexts = frozenset(positions)
                record.primary_context = min(positions, key=lambda n: positions[n]) if positions else None
                if not positions:
                    other = _ANY_CLASS.search(record.spaced)
                    record.foreign_class = other.group(0) if other else None
                record.is_mercedes = bool(record.contexts) or any(p.search(record.spaced) for p in brand_patterns)
                record.is_amg = any(_AMG_BADGE.match(t) for t in record.tokens)
            files.append(record)
    files.sort(key=lambda f: f.rel_path)

    # Exact-content duplicates (SHA-256). The first file by path is canonical.
    first_by_hash: dict[str, SourceFile] = {}
    for f in files:
        if not f.is_webp:
            continue
        canonical = first_by_hash.setdefault(f.sha256, f)
        if canonical is not f:
            f.duplicate_of = canonical.rel_path
            canonical.duplicates.append(f.rel_path)
            log.info("Duplicate (identical SHA-256): %s == %s", f.rel_path, canonical.rel_path)

    catalog = Catalog(root, files)
    log.info(
        "Files: %d total, %d WebP (%d unique, %d exact duplicates), %d other",
        len(files), len(catalog.webp), len(catalog.canonical_webp),
        len(catalog.duplicate_webp), len(catalog.other_files),
    )
    for f in catalog.other_files:
        log.info("Non-WebP file (not matched): %s", f.rel_path)
    for f in catalog.canonical_webp:
        if not f.is_mercedes:
            log.info("WebP without any Mercedes/category word (unrelated): %s", f.rel_path)
    return catalog
