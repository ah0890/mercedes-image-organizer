"""Excel workbook, plain-text processing report and terminal summary."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from pathlib import Path


from .catalog import Catalog
from .matcher import AMBIGUOUS, FOUND, MISSING, MatchResult
from .organizer import DUPLICATES, REMAINING_AMBIGUOUS, REMAINING_UNMATCHED, UNRELATED, CopyStats
from .second_pass import RECOVERED, SecondPassResult
from .utils import get_logger

STATUS_DONE = "DONE - NO MISSING"
STATUS_MISSING = "MISSING IMAGES"
STATUS_REVIEW = "REVIEW REQUIRED"
STATUS_BOTH = "MISSING IMAGES + REVIEW REQUIRED"

@dataclass
class CategorySummary:
    name: str
    expected: int
    found: int
    missing: int
    ambiguous: int
    files: int

    @property
    def status(self) -> str:
        if self.missing and self.ambiguous:
            return STATUS_BOTH
        if self.missing:
            return STATUS_MISSING
        if self.ambiguous:
            return STATUS_REVIEW
        return STATUS_DONE


@dataclass
class RunSummary:
    categories: list[CategorySummary]
    expected: int
    found: int
    missing: int
    ambiguous: int
    webp: int
    matched_files: int
    remaining: int
    unrelated: int
    duplicates: int
    source_files: int
    output_files: int


def summarize(results: dict[str, list[MatchResult]], catalog: Catalog, stats: CopyStats) -> RunSummary:
    cats = [CategorySummary(
        name, len(rows),
        sum(r.status == FOUND for r in rows), sum(r.status == MISSING for r in rows),
        sum(r.status == AMBIGUOUS for r in rows), stats.per_bucket[name],
    ) for name, rows in results.items()]
    b = stats.per_bucket
    return RunSummary(
        cats, sum(c.expected for c in cats), sum(c.found for c in cats), sum(c.missing for c in cats),
        sum(c.ambiguous for c in cats), len(catalog.webp), stats.category_files,
        b[REMAINING_AMBIGUOUS.as_posix()] + b[REMAINING_UNMATCHED.as_posix()],
        b[UNRELATED.as_posix()], b[DUPLICATES.as_posix()], stats.source_files, sum(b.values()),
    )


def reconciliation(summary: RunSummary, stats: CopyStats) -> list[tuple[str, int | str, str]]:
    b = stats.per_bucket
    rows: list[tuple[str, int | str, str]] = [
        ("Class folders", summary.matched_files, "images matched to a Markdown entry"),
        (f"{REMAINING_AMBIGUOUS.as_posix()}/", b[REMAINING_AMBIGUOUS.as_posix()],
         "claimed equally by several classes - needs review"),
        (f"{REMAINING_UNMATCHED.as_posix()}/", b[REMAINING_UNMATCHED.as_posix()],
         "Mercedes images no Markdown entry matched"),
        (f"{DUPLICATES.as_posix()}/", b[DUPLICATES.as_posix()], "exact SHA-256 copies of an image placed elsewhere"),
        (f"{UNRELATED.as_posix()}/", b[UNRELATED.as_posix()], "not Mercedes images / not WebP (.DS_Store)"),
        ("TOTAL in output", summary.output_files, ""),
        ("Files in source folder", summary.source_files, ""),
    ]
    ok = summary.output_files == summary.source_files == stats.placed_files
    rows.append(("Totals match", "", "YES - every source file is in the output exactly once" if ok else "NO - see log"))
    return rows


# --------------------------------------------------------------------------- text

def summary_lines(summary: RunSummary, stats: CopyStats, dry_run: bool) -> list[str]:
    width = max(len(c.name) for c in summary.categories) + 2
    rec = reconciliation(summary, stats)
    rec_width = max(len(r[0]) for r in rec) + 2
    lines = [
        f"Total Classes:               {len(summary.categories)}",
        f"Total Expected Entries:      {summary.expected}",
        f"Total WebP Images:           {summary.webp}",
        f"Entries Found:               {summary.found}",
        f"Matched Images (copied):     {summary.matched_files}" + ("  (dry run - not copied)" if dry_run else ""),
        f"Missing Entries:             {summary.missing}",
        f"Ambiguous Matches:           {summary.ambiguous}",
        f"Unrelated/Remaining Images:  {summary.unrelated + summary.remaining}"
        f"  ({summary.remaining} remaining Mercedes, {summary.unrelated} unrelated)",
        f"Duplicates:                  {summary.duplicates}",
        "", "Category Status:", "",
        *[f"{c.name:<{width}}-> {c.status}" for c in summary.categories],
        "", "Output Folder Reconciliation:", "",
    ]
    for name, count, meaning in rec:
        if name == "TOTAL in output":
            lines.append("-" * (rec_width + 6))
        lines.append(f"{name:<{rec_width}}{count!s:>5}" + (f"   {meaning}" if meaning else ""))
    return lines


def second_pass_lines(second: SecondPassResult, summary: RunSummary, newly_found: int) -> list[str]:
    bar = "=" * 48
    width = max(len(c.name) for c in summary.categories) + 2
    counts = second.status_counts
    return [
        bar, "SECOND-PASS CLASSIFICATION COMPLETED" + (" (DRY RUN - nothing moved)" if second.dry_run else ""), bar,
        "",
        f"Recovered from Duplicates: {second.recovered_from('Duplicates')}",
        f"Recovered from Remaining:  {second.recovered_from('Remaining')}",
        f"Recovered from Unrelated:  {second.recovered_from('Unrelated')}",
        "",
        f"Still in Duplicates: {second.still_in('Duplicates')}",
        f"Still in Remaining:  {second.still_in('Remaining')}",
        f"Still in Unrelated:  {second.still_in('Unrelated')}",
        "",
        "Newly Classified:",
        f"{counts[RECOVERED]} file(s), {newly_found} entr{'y' if newly_found == 1 else 'ies'} newly found",
        "",
        "New Missing Images:",
        f"{summary.missing} entries still missing after the second pass",
        "",
        "Ambiguous:",
        f"{counts[AMBIGUOUS]} file(s), {summary.ambiguous} entries",
        "",
        f"(second-pass statuses: {', '.join(f'{k} {v}' for k, v in sorted(counts.items()))}; "
        f"rescan rounds: {second.rounds})",
        "", bar, "", "Category Status:", "",
        *[f"{c.name:<{width}}-> {c.status}" for c in summary.categories],
        "", bar,
    ]


def write_text_report(path: Path, summary: RunSummary, stats: CopyStats, dry_run: bool, output_root: Path,
                      excel_path: Path, override_problems: list[str], extra: list[str] | None = None) -> None:
    lines = [
        "Mercedes WebP Image Organizer", "=============================", "",
        f"Run: {dt.datetime.now():%Y-%m-%d %H:%M:%S}" + ("   (DRY RUN - no files copied)" if dry_run else ""), "",
        *summary_lines(summary, stats, dry_run), "",
        f"Files copied this run: {stats.copied}, already up to date: {stats.skipped_identical}, "
        f"stale output files removed: {stats.removed_stale}, copy errors: {len(stats.errors)}", "",
        "Output Folder:", str(output_root), "", "Excel Report:", str(excel_path),
    ]
    if extra:
        lines += ["", *extra]
    if stats.errors:
        lines += ["", "Copy Errors", "-----------", *stats.errors]
    if override_problems:
        lines += ["", "Override Problems", "-----------------", *override_problems]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    get_logger().info("Processing report written: %s", path)


def print_summary(summary: RunSummary, stats: CopyStats, dry_run: bool, output_root: Path,
                  excel_path: Path, report_path: Path, log_path: Path) -> None:
    bar = "=" * 48
    print("\n".join([
        "", bar, "Mercedes WebP Image Organizer",
        "DRY RUN COMPLETED" if dry_run else "PROCESSING COMPLETED", bar, "",
        *summary_lines(summary, stats, dry_run), "",
        *([] if dry_run else [f"(this run copied {stats.copied} files, {stats.skipped_identical} already "
                              f"up to date, {stats.removed_stale} stale output files removed)", ""]),
        "Output:", str(output_root), "", "Excel Report:", str(excel_path), "",
        "Processing Report:", str(report_path), "", "Log:", str(log_path), "", bar,
    ]))
