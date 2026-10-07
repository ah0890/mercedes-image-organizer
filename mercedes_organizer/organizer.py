"""Copy every source file into ``<output>/`` - exactly once.

Placement plan:

* ``<Category>/``                       images matched to an entry of that class
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


def category_dir(output_root: Path, category: str) -> Path:
    return output_root / safe_folder_name(category)


def build_plan(results: dict[str, list[MatchResult]], catalog: Catalog, output_root: Path,
               owner: dict[str, str | None]) -> list[Placement]:
    plan: list[Placement] = []
    placed_hashes: dict[str, str] = {}          # sha256 -> where its first copy went

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
            placed_hashes.setdefault(f.sha256, f"{category}/{f.name}")
            plan.append(Placement(f, dest_dir / f.name, category, "matched"))

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
            placed_hashes[f.sha256] = f"{REMAINING_UNMATCHED.as_posix()}/{f.name}"
            plan.append(Placement(f, output_root / REMAINING_UNMATCHED / f.rel_path,
                                  REMAINING_UNMATCHED.as_posix(), "no Markdown entry matched this file"))
    return plan


def _check_paths(source: Path, output: Path) -> None:
    s, o = source.resolve(), output.resolve()
    if s == o or s in o.parents or o in s.parents:
        raise ValueError("Output folder and source folder must not contain each other")


def organize(results: dict[str, list[MatchResult]], catalog: Catalog, output_root: Path,
             owner: dict[str, str | None], dry_run: bool, clean_stale: bool = True) -> CopyStats:
    log = get_logger()
    _check_paths(catalog.root, output_root)
    stats = CopyStats()
    plan = build_plan(results, catalog, output_root, owner)
    stats.placements = plan
    stats.source_files = len(catalog.files)
    stats.placed_files = len({p.file.rel_path for p in plan})
    categories = set(results)

    # A file that a follow-up tool moved one level down inside its own folder - Not Matched/<Class>/
    # (segregate_not_matched.py) or <Class>/<View>/ (segregate_views.py) - counts as in place,
    # so a normal run neither re-copies it flat nor removes it.
    subfolder_index: dict[str, dict[str, list[Path]]] = {}

    def in_subfolders(folder: Path) -> dict[str, list[Path]]:
        key = os.path.normcase(str(folder))
        if key not in subfolder_index:
            index: dict[str, list[Path]] = {}
            if os.path.isdir(fs_path(folder)):
                for sub in os.listdir(fs_path(folder)):
                    if os.path.isdir(fs_path(folder / sub)):
                        for name in os.listdir(fs_path(folder / sub)):
                            index.setdefault(name, []).append(folder / sub / name)
            subfolder_index[key] = index
        return subfolder_index[key]

    created: set[str] = set()
    for p in plan:
        stats.per_bucket[p.bucket] += 1
        stats.category_files += p.bucket in categories
        if dry_run:
            stats.planned += 1
            continue
        if not os.path.exists(fs_path(p.dest)):
            segregated = next((c for c in in_subfolders(p.dest.parent).get(p.dest.name, [])
                               if sha256_of(c) == p.file.sha256), None)
            if segregated is not None:
                p.dest = segregated
                stats.skipped_identical += 1
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
