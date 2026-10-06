"""Copy every source file into ``<output>/`` - exactly once.

Placement plan:

* ``<Category>/``                       images matched to an entry of that class, plus images whose
                                         file name starts with that class name (class-name sort)
* ``_Remaining/Ambiguous Candidates/``  images claimed equally by several classes (needs review)
* ``_Remaining/Not Matched/``           Mercedes images no Markdown entry matched
* ``_Duplicates/``                      exact SHA-256 copies of an image placed elsewhere
* ``_Unrelated Files/``                 non-Mercedes files (no brand/class word, non-WebP, .DS_Store)

Within one class folder identical content is kept once; the other copies go to
``_Duplicates``. The output therefore mirrors the source file-for-file.

The source folder is only ever *read* (``shutil.copy2``); nothing there is
moved, renamed or deleted. Stale files from earlier runs are removed from the
generated output folder only.
"""

from __future__ import annotations

import os
import shutil
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .catalog import Catalog, SourceFile, sha256_of
from .class_names import ClassNameIndex
from .matcher import MatchResult
from .utils import fs_path, get_logger, safe_folder_name

REMAINING_AMBIGUOUS = Path("_Remaining") / "Ambiguous Candidates"
REMAINING_UNMATCHED = Path("_Remaining") / "Not Matched"
DUPLICATES = Path("_Duplicates")
UNRELATED = Path("_Unrelated Files")


@dataclass
class Placement:
    file: SourceFile
    dest: Path
    bucket: str                 # category name or special folder label
    reason: str = ""
    kind: str = ""              # "page" (matched to an entry) or "class name" (sorted by class name only)
    variant: str = ""           # class-name spelling found in the file name


@dataclass
class CopyStats:
    copied: int = 0
    skipped_identical: int = 0
    planned: int = 0
    removed_stale: int = 0
    errors: list[str] = field(default_factory=list)
    per_bucket: Counter = field(default_factory=Counter)
    category_files: int = 0
    source_files: int = 0
    placed_files: int = 0
    placements: list[Placement] = field(default_factory=list)
    class_name_files: Counter = field(default_factory=Counter)   # per class, files sorted by class name


def category_dir(output_root: Path, category: str) -> Path:
    return output_root / safe_folder_name(category)


def build_plan(results: dict[str, list[MatchResult]], catalog: Catalog, output_root: Path,
               owner: dict[str, str | None], class_index: ClassNameIndex | None = None) -> list[Placement]:
    plan: list[Placement] = []
    placed_hashes: dict[str, str] = {}          # sha256 -> where its first copy went
    class_hashes: dict[str, set[str]] = {c: set() for c in results}
    taken: set[str] = set()                     # destination paths already used

    # 1. Class folders, one copy per distinct content.
    for category, rows in results.items():
        files = sorted({h.file.rel_path: h.file for r in rows for h in r.assigned}.values(),
                       key=lambda f: f.rel_path)
        seen_in_category: dict[str, str] = {}
        dest_dir = category_dir(output_root, category)
        for f in files:
            if f.sha256 in seen_in_category:
                plan.append(Placement(f, output_root / DUPLICATES / f.rel_path, DUPLICATES.as_posix(),
                                      f"identical to {seen_in_category[f.sha256]} (in {category}/)"))
                continue
            seen_in_category[f.sha256] = f.rel_path
            class_hashes[category].add(f.sha256)
            placed_hashes.setdefault(f.sha256, f"{category}/{f.name}")
            taken.add(os.path.normcase(str(dest_dir / f.name)))
            plan.append(Placement(f, dest_dir / f.name, category, "matched to a page", "page"))

    # 2. Everything not copied into a class.
    assigned = {p.file.rel_path for p in plan}
    for f in catalog.files:
        if f.rel_path in assigned:
            continue
        if not f.is_webp:
            plan.append(Placement(f, output_root / UNRELATED / f.rel_path, UNRELATED.as_posix(), "not a WebP file"))
        elif f.sha256 in placed_hashes:
            plan.append(Placement(f, output_root / DUPLICATES / f.rel_path, DUPLICATES.as_posix(),
                                  f"identical to {placed_hashes[f.sha256]}"))
        elif f.rel_path in owner and owner[f.rel_path] is None:
            placed_hashes[f.sha256] = f"{REMAINING_AMBIGUOUS.as_posix()}/{f.name}"
            plan.append(Placement(f, output_root / REMAINING_AMBIGUOUS / f.rel_path,
                                  REMAINING_AMBIGUOUS.as_posix(), "claimed equally by several classes"))
        elif not f.is_mercedes:
            placed_hashes[f.sha256] = f"{UNRELATED.as_posix()}/{f.name}"
            plan.append(Placement(f, output_root / UNRELATED / f.rel_path, UNRELATED.as_posix(),
                                  "no Mercedes or class name in the filename"))
        else:
            # 3. No page matched: sort by the class name in the file name, if it is a Markdown class.
            match = class_index.classify(f.name) if class_index else None
            if match and match.category and f.sha256 not in class_hashes[match.category]:
                dest = _free_dest(category_dir(output_root, match.category), f.name, taken)
                class_hashes[match.category].add(f.sha256)
                placed_hashes[f.sha256] = f"{match.category}/{dest.name}"
                plan.append(Placement(f, dest, match.category, f"sorted by class name ('{match.variant}')",
                                      "class name", match.variant))
                continue
            placed_hashes[f.sha256] = f"{REMAINING_UNMATCHED.as_posix()}/{f.name}"
            reason = "no Markdown entry matched this file"
            if match and not match.category:
                reason += f"; {match.how}"
            plan.append(Placement(f, output_root / REMAINING_UNMATCHED / f.rel_path,
                                  REMAINING_UNMATCHED.as_posix(), reason))
    return plan


def _free_dest(folder: Path, name: str, taken: set[str]) -> Path:
    stem, suffix = os.path.splitext(name)
    dest, n = folder / name, 2
    while os.path.normcase(str(dest)) in taken:
        dest, n = folder / f"{stem} ({n}){suffix}", n + 1
    taken.add(os.path.normcase(str(dest)))
    return dest


def _check_paths(source: Path, output: Path) -> None:
    s, o = source.resolve(), output.resolve()
    if s == o or s in o.parents or o in s.parents:
        raise ValueError("Output folder and source folder must not contain each other")


def organize(results: dict[str, list[MatchResult]], catalog: Catalog, output_root: Path,
             owner: dict[str, str | None], dry_run: bool, clean_stale: bool = True,
             class_index: ClassNameIndex | None = None) -> CopyStats:
    log = get_logger()
    _check_paths(catalog.root, output_root)
    stats = CopyStats()
    plan = build_plan(results, catalog, output_root, owner, class_index)
    stats.placements = plan
    stats.source_files = len(catalog.files)
    stats.placed_files = len({p.file.rel_path for p in plan})
    categories = set(results)

    created: set[str] = set()
    for p in plan:
        stats.per_bucket[p.bucket] += 1
        stats.category_files += p.bucket in categories
        if p.kind == "class name":
            stats.class_name_files[p.bucket] += 1
        if dry_run:
            stats.planned += 1
            continue
        try:
            if str(p.dest.parent) not in created:
                os.makedirs(fs_path(p.dest.parent), exist_ok=True)
                created.add(str(p.dest.parent))
            dest = fs_path(p.dest)
            same_size = os.path.exists(dest) and os.path.getsize(dest) == os.path.getsize(fs_path(p.file.path))
            if same_size and sha256_of(p.dest) == p.file.sha256:
                stats.skipped_identical += 1
                continue
            shutil.copy2(fs_path(p.file.path), dest)
            stats.copied += 1
            log.info("Copied [%s] %s -> %s", p.bucket, p.file.rel_path, p.dest)
        except OSError as exc:
            stats.errors.append(f"{p.file.rel_path} -> {p.dest}: {exc}")
            log.error("Copy failed: %s", stats.errors[-1])

    if not dry_run:
        for category in results:          # class folders exist even when nothing matched
            os.makedirs(fs_path(category_dir(output_root, category)), exist_ok=True)
        if clean_stale:
            expected = {os.path.normcase(str(p.dest.resolve())) for p in plan}
            stats.removed_stale = _remove_stale(output_root, expected, {f.sha256 for f in catalog.files})

    log.info("Placement per folder: %s", dict(stats.per_bucket))
    log.info("Source files %d, placed %d, output files %d, stale removed %d",
             stats.source_files, stats.placed_files, len(plan), stats.removed_stale)
    return stats


def _remove_stale(output_root: Path, expected: set[str], source_hashes: set[str]) -> int:
    """Delete copies of SOURCE files that this run did not place there.

    Only files whose content exists in the source folder are removed (this run
    re-creates them in the right place). Files someone added to the output by
    hand are never deleted, so a second-pass recovery survives re-runs.
    """
    log = get_logger()
    removed = 0
    for dirpath, _dirs, files in os.walk(fs_path(output_root), topdown=False):
        for name in files:
            path = Path(dirpath.removeprefix("\\\\?\\")) / name
            if os.path.normcase(str(path.resolve())) in expected:
                continue
            if sha256_of(path) not in source_hashes:
                log.info("Kept file that does not come from the source folder: %s", path)
                continue
            os.remove(fs_path(path))
            removed += 1
            log.info("Removed stale output file: %s", path)
        if dirpath != fs_path(output_root) and not os.listdir(dirpath):
            os.rmdir(dirpath)
    return removed
