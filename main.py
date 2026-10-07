"""Mercedes WebP Image Organizer - command-line entry point.

    python main.py              # organise images + write reports
    python main.py --dry-run    # analyse and report only, copy nothing
    python main.py --help       # all options
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from mercedes_organizer.catalog import build_catalog
from mercedes_organizer.matcher import Matcher, load_overrides
from mercedes_organizer.organizer import organize
from mercedes_organizer.parser import parse_markdown
from mercedes_organizer.excel_report import write_excel
from mercedes_organizer.reporter import print_summary, second_pass_lines, summarize, write_text_report
from mercedes_organizer.second_pass import folder_counts, run_second_pass
from mercedes_organizer.utils import setup_logging

PROJECT_DIR = Path(__file__).resolve().parent
DEFAULT_MARKDOWN = PROJECT_DIR / "EF_Mercedes_Full_Page_List.md"
DEFAULT_SOURCE = PROJECT_DIR / "Mercedez Split"
DEFAULT_OVERRIDES = PROJECT_DIR / "overrides.csv"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Organise Mercedes WebP images into category folders.")
    ap.add_argument("--dry-run", action="store_true", help="analyse and report, but copy no images")
    ap.add_argument("--markdown", type=Path, default=DEFAULT_MARKDOWN, help="Markdown page list")
    ap.add_argument("--source", type=Path, default=DEFAULT_SOURCE, help="folder with the WebP images (read-only)")
    ap.add_argument("--output", type=Path, default=PROJECT_DIR / "Mercedes_Organized", help="output folder")
    ap.add_argument("--report-dir", type=Path, default=PROJECT_DIR, help="where reports and the log are written")
    ap.add_argument("--overrides", type=Path, default=None,
                    help=f"CSV of manual assignments (default: {DEFAULT_OVERRIDES.name} if it exists)")
    ap.add_argument("--keep-stale", action="store_true",
                    help="do not remove files from earlier runs out of the generated output folder")
    ap.add_argument("--no-second-pass", action="store_true",
                    help="skip re-checking _Duplicates/_Remaining/_Unrelated Files after the first pass")
    ap.add_argument("-v", "--verbose", action="store_true", help="also print detailed log lines to the terminal")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    args = parse_args(argv)
    args.report_dir.mkdir(parents=True, exist_ok=True)
    log_path = args.report_dir / "mercedes_organizer.log"
    excel_path = args.report_dir / "missing_mercedes_images.xlsx"
    report_path = args.report_dir / "processing_report.txt"
    log = setup_logging(log_path, args.verbose)
    started = time.perf_counter()

    log.info("Mercedes WebP Image Organizer started (dry_run=%s)", args.dry_run)
    log.info("Markdown: %s", args.markdown)
    log.info("Source:   %s", args.source)
    log.info("Output:   %s", args.output)

    if not args.markdown.is_file():
        log.error("Markdown file not found: %s", args.markdown)
        print(f"ERROR: Markdown file not found: {args.markdown}", file=sys.stderr)
        return 2
    if not args.source.is_dir():
        log.error("Source image folder not found: %s", args.source)
        print(f"ERROR: Source image folder not found: {args.source}", file=sys.stderr)
        return 2

    try:
        categories = parse_markdown(args.markdown)
        if not categories:
            log.error("No categories found in %s", args.markdown)
            print("ERROR: no '## Category' headings with bullet pages found in the Markdown.", file=sys.stderr)
            return 3
        log.info("Categories discovered: %d (%s)", len(categories), ", ".join(c.name for c in categories))
        log.info("Expected pages: %d", sum(len(c.pages) for c in categories))

        for category in categories:
            for page in category.pages:
                log.info("Identifiers [%s] %s -> %s", category.name, page.raw, page.identifiers.tiers())

        catalog = build_catalog(args.source, [c.name for c in categories])
        overrides_path = args.overrides or (DEFAULT_OVERRIDES if DEFAULT_OVERRIDES.is_file() else None)
        overrides = {}
        if overrides_path:
            log.info("Applying manual overrides from %s", overrides_path)
            overrides = load_overrides(overrides_path)
        matcher = Matcher(catalog, categories)
        results, override_problems = matcher.match_all(overrides)

        stats = organize(results, catalog, args.output, matcher.owner, args.dry_run,
                         clean_stale=not args.keep_stale)

        # Second pass: re-check _Duplicates, _Remaining and _Unrelated Files in the output folder.
        second = None
        found_before = sum(r.status == "FOUND" for rows in results.values() for r in rows)
        if not args.no_second_pass:
            second = run_second_pass(categories, args.output, overrides, dry_run=args.dry_run)
            if second.results and not args.dry_run:
                results = second.results          # matching over the output after recovery
                on_disk = folder_counts(args.output, [c.name for c in categories])
                stats.per_bucket = on_disk
                stats.category_files = sum(on_disk[c.name] for c in categories)

        summary = summarize(results, catalog, stats)
        newly_found = summary.found - found_before
        inputs = {"Markdown": str(args.markdown), "Source folder": str(args.source),
                  "Output folder": str(args.output),
                  "Overrides": str(overrides_path) if overrides_path else "(none)"}
        classes_with_images = {c for f in catalog.webp for c in f.contexts}
        excel_path = write_excel(excel_path, results, summary, stats, args.dry_run, inputs, second,
                                 classes_with_images)
        second_lines = second_pass_lines(second, summary, newly_found) if second else []
        write_text_report(report_path, summary, stats, args.dry_run, args.output, excel_path, override_problems,
                          second_lines)
    except Exception:
        log.exception("Unexpected error")
        print(f"ERROR: unexpected failure - see {log_path}", file=sys.stderr)
        return 1

    log.info(
        "Final: categories=%d expected=%d found=%d missing=%d ambiguous=%d copied=%d up_to_date=%d "
        "planned=%d errors=%d (%.1fs)",
        len(summary.categories), summary.expected, summary.found, summary.missing, summary.ambiguous,
        stats.copied, stats.skipped_identical, stats.planned, len(stats.errors), time.perf_counter() - started,
    )
    print_summary(summary, stats, args.dry_run, args.output, excel_path, report_path, log_path)
    if second_lines:
        print("\n".join(["", *second_lines]))
    return 1 if stats.errors else 0


if __name__ == "__main__":
    sys.exit(main())
