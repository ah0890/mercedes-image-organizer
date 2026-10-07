"""Second pass: re-check the leftover folders of the GENERATED output.

Every file in ``_Duplicates/``, ``_Remaining/`` and ``_Unrelated Files/`` (any
depth) is re-evaluated with exactly the first-pass rules: the same Matcher
(model -> number -> chassis tiers, context conflicts, cross-class claims) runs
over the whole output folder, so tier priority and claims are decided against
the same competing files as in pass 1.

* A leftover file the rules assign to a class is moved into that class folder
  -> RECOVERED, unless identical content (SHA-256) is already there -> DUPLICATE
  (the existing rule: one copy per class, others stay in _Duplicates).
* A file claimed equally by several classes stays where it is -> AMBIGUOUS.
* Anything else stays where it is -> STILL UNMATCHED (or DUPLICATE when it is an
  exact copy of a file elsewhere in the output).

Files only ever move from a leftover folder into a class folder, never back,
and the pass rescans until nothing new is recovered. Nothing is deleted and the
source folder is not touched (only the output folder is read or changed).
"""

from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .catalog import Catalog, SourceFile, build_catalog
from .matcher import MatchResult, Matcher
from .organizer import category_dir
from .parser import Category
from .utils import fs_path, get_logger

LEFTOVER_ROOTS = ["_Duplicates", "_Remaining", "_Unrelated Files"]
LEFTOVER_LABEL = {"_Duplicates": "Duplicates", "_Remaining": "Remaining", "_Unrelated Files": "Unrelated"}
# Reports written into the output by other tools (segregate_not_matched.py) - not images, not counted.
GENERATED_REPORTS = {"class_name_segmentation_report.xlsx"}

RECOVERED = "RECOVERED"
STILL_UNMATCHED = "STILL UNMATCHED"
DUPLICATE = "DUPLICATE"
AMBIGUOUS = "AMBIGUOUS"


@dataclass
class RecoveryRow:
    source_folder: str          # Duplicates / Remaining / Unrelated
    rel_path: str               # path inside the output folder when inspected
    file: str
    status: str
    category: str = ""
    entries: str = ""
    identifier: str = ""
    notes: str = ""
    moved_to: str = ""


@dataclass
class SecondPassResult:
    rows: list[RecoveryRow] = field(default_factory=list)
    results: dict[str, list[MatchResult]] = field(default_factory=dict)   # final matching over the output
    rounds: int = 0
    dry_run: bool = False

    def recovered_from(self, label: str) -> int:
        return sum(r.status == RECOVERED and r.source_folder == label for r in self.rows)

    def still_in(self, label: str) -> int:
        return sum(r.status != RECOVERED and r.source_folder == label for r in self.rows)

    @property
    def status_counts(self) -> Counter:
        return Counter(r.status for r in self.rows)


def _top_folder(rel_path: str) -> str:
    return Path(rel_path).parts[0]


def _hits_for(results: dict[str, list[MatchResult]], category: str, f: SourceFile) -> list[tuple[str, str]]:
    out = []
    for r in results.get(category, []):
        for h in r.hits:
            if h.file.rel_path == f.rel_path:
                out.append((r.page.raw, f"{h.identifier.upper()} ({h.tier})"))
    return out


def _free_name(folder: Path, name: str) -> Path:
    target = folder / name
    stem, suffix = os.path.splitext(name)
    n = 2
    while os.path.exists(fs_path(target)):
        target = folder / f"{stem} ({n}){suffix}"
        n += 1
    return target


def run_second_pass(categories: list[Category], output_root: Path, overrides=None,
                    dry_run: bool = False, max_rounds: int = 5) -> SecondPassResult:
    log = get_logger()
    result = SecondPassResult(dry_run=dry_run)
    if not output_root.is_dir():
        log.warning("Second pass skipped: output folder %s does not exist", output_root)
        return result
    names = [c.name for c in categories]
    known_chassis = {code for c in categories for code in c.coded_chassis}
    final_rows: dict[str, RecoveryRow] = {}

    for round_no in range(1, max_rounds + 1):
        result.rounds = round_no
        catalog: Catalog = build_catalog(output_root, names)
        matcher = Matcher(catalog, categories)
        results, _ = matcher.match_all(overrides or {})
        result.results = results

        class_hashes: dict[str, set[str]] = {
            n: {f.sha256 for f in catalog.files if _top_folder(f.rel_path) == category_dir(Path(), n).name}
            for n in names
        }
        all_hashes = Counter(f.sha256 for f in catalog.files)
        moved = 0
        for f in catalog.files:
            top = _top_folder(f.rel_path)
            if top not in LEFTOVER_ROOTS or f.name in GENERATED_REPORTS:
                continue
            label = LEFTOVER_LABEL[top]
            row = RecoveryRow(label, f.rel_path, f.name, STILL_UNMATCHED)
            claimed = f.rel_path in matcher.owner      # some entry's identifiers matched this file
            owner = matcher.owner.get(f.rel_path)      # None when claimed equally by several classes
            if f.is_webp and owner is not None:
                hits = _hits_for(results, owner, f)
                row.category = owner
                row.entries = "\n".join(sorted({e for e, _ in hits}))
                row.identifier = " / ".join(sorted({i for _, i in hits}))
                if f.sha256 in class_hashes[owner]:
                    row.status = DUPLICATE
                    row.notes = f"Matches {owner}, but the same image is already in the {owner} folder"
                else:
                    row.status = RECOVERED
                    dest = _free_name(category_dir(output_root, owner), f.name)
                    row.moved_to = str(dest.relative_to(output_root))
                    row.notes = f"Moved into the {owner} folder"
                    if not dry_run:
                        os.makedirs(fs_path(dest.parent), exist_ok=True)
                        os.replace(fs_path(output_root / f.rel_path), fs_path(dest))
                    class_hashes[owner].add(f.sha256)
                    moved += 1
                    log.info("RECOVERED [%s] %s -> %s via %s", label, f.rel_path, row.moved_to, row.identifier)
            elif f.is_webp and claimed:
                row.status = AMBIGUOUS
                row.notes = "Fits entries in more than one class equally well - needs your decision"
            elif top == "_Duplicates" and all_hashes[f.sha256] > 1:
                row.status = DUPLICATE
                row.notes = "Exact copy of another image - kept as a duplicate"
            else:
                reasons = []
                if not f.is_webp:
                    reasons.append("Not an image (WebP) file")
                elif not f.is_mercedes:
                    reasons.append("Not a Mercedes image (no brand or class name in the file name)")
                elif f.foreign_class:
                    family = f.foreign_class.split()[0]
                    family = (family.upper() if len(family) <= 3 else family.capitalize()) + "-Class"
                    reasons.append(f"Belongs to the {family}, which is not in the page list")
                elif f.chassis_tokens(frozenset()) - known_chassis:
                    codes = sorted(c.upper() for c in f.chassis_tokens(frozenset()) - known_chassis)
                    reasons.append(f"Generation {', '.join(codes)} is not in the page list")
                else:
                    reasons.append("Name has no model number or chassis code from the page list")
                row.notes = "; ".join(reasons)
            # Keep the first inspection of each file (later rounds see recovered files in class folders).
            final_rows.setdefault(f.rel_path, row)
            if row.status == RECOVERED:
                final_rows[f.rel_path] = row
        log.info("Second pass round %d: %d file(s) recovered", round_no, moved)
        if moved == 0 or dry_run:
            break

    result.rows = sorted(final_rows.values(), key=lambda r: (LEFTOVER_ROOTS.index(
        next(k for k, v in LEFTOVER_LABEL.items() if v == r.source_folder)), r.rel_path))
    counts = result.status_counts
    log.info("Second pass done in %d round(s): %s", result.rounds, dict(counts))
    return result


def folder_counts(output_root: Path, category_names: list[str]) -> Counter:
    """Count files on disk per output folder (class folders and leftover folders)."""
    counts: Counter = Counter()
    if not output_root.is_dir():
        return counts
    for dirpath, _dirs, files in os.walk(fs_path(output_root)):
        rel = Path(dirpath.removeprefix("\\\\?\\")).relative_to(output_root.resolve())
        if not rel.parts:
            continue
        top = rel.parts[0]
        key = "/".join(rel.parts[:2]) if top == "_Remaining" else top
        counts[key] += sum(f not in GENERATED_REPORTS for f in files)
    for name in category_names:
        counts.setdefault(category_dir(Path(), name).name, 0)
    return counts

