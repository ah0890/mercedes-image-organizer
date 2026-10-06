"""Build missing_mercedes_images.xlsx - written to be read by people, not just audited.

Sheets (front to back = most to least important):

    Summary               key numbers, % complete per class, legend, where every file went
    Missing Images        what is missing, why (plain words) and what to do about it
    Needs Review          entries that could not be decided automatically
    Detailed Matching     every entry: identifiers, how it was matched, which images
    Second Pass Recovery  re-check of the leftover folders
    Run Info              technical details

Image files are shown by a short readable name ("c-class w204 standard saloon
2007–2011") and the 4 camera views of one render are shown as one line.
"""

from __future__ import annotations

import datetime as dt
import re
from collections import Counter, defaultdict
from pathlib import Path

from openpyxl import Workbook
from openpyxl.formatting.rule import DataBarRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from .catalog import _VIEW_SUFFIX
from .matcher import AMBIGUOUS, FOUND, MISSING, MatchResult
from .organizer import CopyStats
from .reporter import STATUS_BOTH, STATUS_DONE, STATUS_MISSING, STATUS_REVIEW, RunSummary, reconciliation
from .second_pass import DUPLICATE, RECOVERED, STILL_UNMATCHED, SecondPassResult
from .utils import get_logger

# ----------------------------------------------------------------------------- style

NAVY = "1F3864"
FONT = "Calibri"
_fill = lambda c: PatternFill("solid", start_color=c)  # noqa: E731
GREEN, GREEN_TXT = _fill("E2F0D9"), "375623"
RED, RED_TXT = _fill("FCE4E4"), "9C0006"
AMBER, AMBER_TXT = _fill("FFF2CC"), "7F6000"
GREY, GREY_TXT = _fill("F2F2F2"), "595959"
BLUE = _fill("DDEBF7")
HEADER = _fill(NAVY)
BAND = _fill("F7F9FC")
_thin = Side(style="thin", color="D9D9D9")
BORDER = Border(left=_thin, right=_thin, top=_thin, bottom=_thin)

STATUS_STYLE = {
    STATUS_DONE: (GREEN, GREEN_TXT), STATUS_MISSING: (RED, RED_TXT), STATUS_BOTH: (RED, RED_TXT),
    STATUS_REVIEW: (AMBER, AMBER_TXT),
    FOUND: (GREEN, GREEN_TXT), MISSING: (RED, RED_TXT), AMBIGUOUS: (AMBER, AMBER_TXT),
    RECOVERED: (GREEN, GREEN_TXT), DUPLICATE: (GREY, GREY_TXT), STILL_UNMATCHED: (GREY, GREY_TXT),
}
RESULT_LABEL = {FOUND: "FOUND", MISSING: "MISSING", AMBIGUOUS: "NEEDS REVIEW"}


def _font(**kw) -> Font:
    return Font(name=FONT, **kw)


def _title(ws: Worksheet, text: str, subtitle: str = "", width_cols: int = 8) -> int:
    ws["A1"] = text
    ws["A1"].font = _font(bold=True, size=16, color=NAVY)
    ws.row_dimensions[1].height = 26
    row = 2
    if subtitle:
        ws["A2"] = subtitle
        ws["A2"].font = _font(italic=True, size=10, color=GREY_TXT)
        ws.merge_cells(start_row=2, start_column=1, end_row=2, end_column=width_cols)
        ws["A2"].alignment = Alignment(wrap_text=True, vertical="top")
        ws.row_dimensions[2].height = 30
        row = 3
    return row + 1


def _section(ws: Worksheet, row: int, text: str) -> int:
    ws.cell(row, 1, text).font = _font(bold=True, size=12, color=NAVY)
    return row + 1


def _table(ws: Worksheet, row: int, headers: list[str], rows: list[list], widths: list[float] | None = None,
           status_col: int | None = None, band_key: int | None = None, filter_: bool = True,
           freeze: bool = False) -> int:
    """Write a header + rows at `row`. Returns the next free row."""
    for c, h in enumerate(headers, start=1):
        cell = ws.cell(row, c, h)
        cell.font = _font(bold=True, color="FFFFFF")
        cell.fill = HEADER
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        cell.border = BORDER
    ws.row_dimensions[row].height = 30
    header_row = row
    last_key, band = object(), False
    for values in rows:
        row += 1
        if band_key is not None and values[band_key] != last_key:
            band, last_key = not band, values[band_key]
        for c, v in enumerate(values, start=1):
            cell = ws.cell(row, c, v)
            cell.font = _font()
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            cell.border = BORDER
            if band_key is not None and band:
                cell.fill = BAND
        if status_col is not None:
            _style_status(ws.cell(row, status_col + 1))
    if widths:
        for c, w in enumerate(widths, start=1):
            ws.column_dimensions[get_column_letter(c)].width = w
    if filter_ and rows:
        ws.auto_filter.ref = f"A{header_row}:{get_column_letter(len(headers))}{row}"
    if freeze:                          # keep the header visible while scrolling
        ws.freeze_panes = f"A{header_row + 1}"
    return row + 2


def _style_status(cell) -> None:
    style = STATUS_STYLE.get(str(cell.value)) or STATUS_STYLE.get(
        next((k for k, v in RESULT_LABEL.items() if v == cell.value), ""), None)
    if style:
        cell.fill = style[0]
        cell.font = _font(bold=True, color=style[1])
        cell.alignment = Alignment(horizontal="center", vertical="top", wrap_text=True)


def _page_setup(ws: Worksheet) -> None:
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.sheet_view.showGridLines = False


# ----------------------------------------------------------------------------- plain-language helpers

_YEARS = re.compile(r"\d{4}\s*[–-]\s*(?:\d{4})?")


def render_key(filename: str) -> str:
    """Filename without extension and camera-view suffix: the 4 views of one render share it."""
    return _VIEW_SUFFIX.sub("", re.sub(r"\.webp$", "", filename, flags=re.I))


def short_name(filename: str) -> str:
    """'mercedes-benz-c-class-w204-standard-saloon-2007–2011-new generation-...' ->
    'C-Class W204 · standard saloon · 2007–2011' style readable label."""
    text = render_key(filename)
    text = re.sub(r"^mercedes[-_ ]benz[-_ ]", "", text, flags=re.I)
    years = ""
    m = _YEARS.search(text)
    if m:
        years = m.group(0).strip()
        text = text[: m.start()]
    # protect "x-class" before turning separators into spaces
    text = re.sub(r"(?i)\b([a-z]{1,4})-class\b", lambda mm: mm.group(1).upper() + "\x00Class", text)
    text = re.sub(r"[-_]+", " ", text).replace("\x00", "-")
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\b([a-z]{1,3}\d{3}[a-z]?)\b", lambda mm: mm.group(1).upper(), text)   # chassis codes
    text = re.sub(r"\b([a-z]{1,4} ?\d{2,3}[a-z]?)\b", lambda mm: mm.group(1).upper(), text)   # badges: cla45, gt 63
    text = re.sub(r"\b(amg|lci)\b", lambda mm: mm.group(1).upper(), text)
    words = text.split(" ")
    for i, w in enumerate(words):              # short family names at the start: cla, gls, sl, gt
        if i > 2 or not w.isalpha() or "-Class" in w:
            break
        words[i] = w.upper() if len(w) <= 3 else w.capitalize()
    return " ".join(words) + (f" ({years})" if years else "")


def images_text(hits) -> tuple[str, int, int]:
    """One line per render ('… (4 views)'), number of renders, number of files."""
    views: dict[str, set[str]] = defaultdict(set)
    for h in hits:
        views[render_key(h.file.name)].add(h.file.name)
    labels: Counter = Counter()
    lines = []
    for key in sorted(views):
        label = short_name(key)
        labels[label] += 1
        suffix = f" #{labels[label]}" if labels[label] > 1 else ""
        n = len(views[key])
        lines.append(f"• {label}{suffix}  ({n} view{'s' if n != 1 else ''})")
    return "\n".join(lines), len(views), sum(len(v) for v in views.values())


def matched_by(r: MatchResult) -> str:
    if r.tier == "manual":
        return "Assigned manually (overrides.csv)"
    ids = sorted({h.identifier.upper() for h in (r.assigned or r.hits)})
    kind = {"model": "Model", "number": "Number", "chassis": "Chassis code"}.get(r.tier, "")
    return f"{kind} {' / '.join(ids)}" if ids else ""


def plain_notes(r: MatchResult) -> str:
    """Notes for a FOUND page, in plain words."""
    out = []
    for part in filter(None, (p.strip() for p in r.notes.split(";"))):
        if part.startswith("Chassis-only match"):
            code = " / ".join(c.upper() for c in r.page.identifiers.chassis)
            out.append(f"Matched by chassis only: the image shows the {code} generation, not this AMG model itself.")
        elif "[[VERIFY CHASSIS CODE]]" in part:
            out.append("The page list marks this chassis code as unverified - check that the generation is right.")
        elif "went to another category" in part:
            out.append(part.replace("other matching file(s) went to another category",
                                    "more matching image file(s) fit another class better and were placed there."))
        elif part.startswith("Assigned manually"):
            out.append("Assigned by hand in overrides.csv.")
        else:
            out.append(part)
    return " ".join(out)


def searched_for(r: MatchResult) -> str:
    ids = r.page.identifiers
    parts = [" or ".join(x.upper() for x in group) for _, group in ids.tiers() if group]
    return ", then ".join(parts)


def _reject_reason(text: str) -> str:
    m = re.search(r"different chassis \(([^)]*)\)", text)
    if m:
        return f"a different generation ({m.group(1)})"
    m = re.search(r"different model \(([^)]*)\)", text)
    if m:
        return f"a different model ({m.group(1)})"
    if "AMG" in text:
        return "an AMG model"
    return "a different vehicle"


def why_missing(r: MatchResult, classes_with_images: set[str]) -> tuple[str, str]:
    """(why, what to do) in plain words."""
    ids = r.page.identifiers
    model = " / ".join(i.upper() for i in ids.models) or "-"
    chassis = " / ".join(i.upper() for i in ids.chassis)
    if r.notes.startswith("Marked MISSING in overrides"):
        return "Marked as missing in overrides.csv.", "Remove the row from overrides.csv to match it again."
    if r.category not in classes_with_images:
        return (f"The image folder has no images for {r.category} at all.",
                f"Add {r.category} images to the image folder.")
    if r.rejected:
        found = sorted({why.split(" found but ")[0] for _, why in r.rejected})
        reasons = sorted({_reject_reason(why) for _, why in r.rejected})
        why = (f"{', '.join(found)} appears in {len({f.name for f, _ in r.rejected})} image(s), "
               f"but they show {' or '.join(reasons)}.")
    elif not chassis:
        why = (f"No image name contains {model} or {' / '.join(ids.numbers)}, and the page list gives "
               "no chassis code to fall back on.")
    else:
        why = f"No image name contains {model}, {' / '.join(ids.numbers)} or {chassis}."
    if r.page.verify_flag:
        why += " The page list also marks this chassis code as unverified."
        todo = "Confirm the chassis code in the page list, or add an image named with " + model + "."
    else:
        todo = f"Add an image whose name includes {model}" + (f" or {chassis}" if chassis else "") + \
               ", or assign one in overrides.csv."
    return why, todo


# ----------------------------------------------------------------------------- sheets

def _summary_sheet(wb: Workbook, results, summary: RunSummary, stats: CopyStats, second, dry_run, inputs) -> None:
    ws = wb.active
    ws.title = "Summary"
    ws.sheet_properties.tabColor = "70AD47"
    done = sum(c.status == STATUS_DONE for c in summary.categories)
    row = _title(ws, "Mercedes Image Organizer – Report",
                 f"Generated {dt.datetime.now():%d %b %Y, %H:%M}"
                 + ("  ·  DRY RUN (nothing copied)" if dry_run else "")
                 + f"  ·  Page list: {Path(inputs['Markdown']).name}  ·  Images: {Path(inputs['Source folder']).name}")

    # Key-figure tiles
    tiles = [
        ("Classes", len(summary.categories), BLUE, NAVY),
        ("Pages expected", summary.expected, BLUE, NAVY),
        ("Pages with images", summary.found, GREEN, GREEN_TXT),
        ("Pages missing images", summary.missing, RED if summary.missing else GREEN,
         RED_TXT if summary.missing else GREEN_TXT),
        ("Need your review", summary.ambiguous, AMBER if summary.ambiguous else GREEN,
         AMBER_TXT if summary.ambiguous else GREEN_TXT),
        ("Classes complete", f"{done} of {len(summary.categories)}", GREEN if done else GREY,
         GREEN_TXT if done else GREY_TXT),
    ]
    for i, (label, value, fill, color) in enumerate(tiles, start=1):
        top, bottom = ws.cell(row, i, label), ws.cell(row + 1, i, value)
        for cell in (top, bottom):
            cell.fill = fill
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            cell.border = BORDER
        top.font = _font(size=10, color=color)
        bottom.font = _font(bold=True, size=20, color=color)
    ws.row_dimensions[row].height = 30
    ws.row_dimensions[row + 1].height = 38
    row += 3

    pct = summary.found / summary.expected if summary.expected else 0
    ws.cell(row, 1, f"{pct:.0%} of all pages have at least one image. "
                    f"{summary.matched_files} image files were copied into the class folders.").font = _font(size=11)
    row += 2

    # How to read
    row = _section(ws, row, "How to read this report")
    for line in [
        "• Each class from the page list has its own row below. Green = complete, red = some pages have no image.",
        "• 'Missing Images' lists every page without an image, why, and what to do about it.",
        "• 'Detailed Matching' shows every page and which images were matched to it.",
        "• An image counts for a page when its file name contains the page's model (e.g. A200), "
        "its number (200) or its chassis code (W176) - in that order.",
    ]:
        ws.cell(row, 1, line).font = _font(color=GREY_TXT)
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=8)
        row += 1
    row += 1

    # Category status
    row = _section(ws, row, "Status per class")
    rows = []
    for c in summary.categories:
        missing = [r.page.raw for r in results[c.name] if r.status == MISSING]
        review = [r.page.raw for r in results[c.name] if r.status == AMBIGUOUS]
        files = f"{c.files}" + (f"\n(+{c.class_name_files} by class name)" if c.class_name_files else "")
        rows.append([c.name, c.expected, c.found, c.missing, c.ambiguous,
                     (c.found / c.expected) if c.expected else 0, c.status, files,
                     ", ".join(missing + [f"{p} (review)" for p in review]) or "—"])
    first = row + 1
    row = _table(ws, row, ["Class", "Pages", "With images", "Missing", "Need review", "Complete",
                           "Status", "Image files\nin folder", "Pages without an image"],
                 rows, [17, 11, 12, 11, 12, 14, 30, 18, 80], status_col=6, filter_=False)
    last = first + len(rows) - 1
    for r_ in range(first, last + 1):
        ws.cell(r_, 6).number_format = "0%"
        for col in (2, 3, 4, 5, 6, 8):
            ws.cell(r_, col).alignment = Alignment(horizontal="center", vertical="top", wrap_text=True)
    ws.conditional_formatting.add(f"F{first}:F{last}", DataBarRule(
        start_type="num", start_value=0, end_type="num", end_value=1, color="70AD47"))
    total = [f"All {len(summary.categories)} classes", summary.expected, summary.found, summary.missing,
             summary.ambiguous, pct, f"{done} complete",
             f"{summary.matched_files}\n(+{summary.class_name_files} by class name)"
             if summary.class_name_files else summary.matched_files, ""]
    for col, v in enumerate(total, start=1):
        cell = ws.cell(last + 1, col, v)
        cell.font = _font(bold=True)
        cell.fill = BLUE
        cell.border = BORDER
        cell.alignment = Alignment(horizontal="center" if 1 < col < 9 else "left", vertical="top", wrap_text=True)
    ws.cell(last + 1, 6).number_format = "0%"
    row = last + 3

    # Legend
    row = _section(ws, row, "What the statuses mean")
    for status, meaning in [
        (STATUS_DONE, "Every page of this class has at least one image."),
        (STATUS_MISSING, "At least one page has no image yet."),
        (STATUS_REVIEW, "An image could belong to more than one class - you decide (see 'Needs Review')."),
    ]:
        cell = ws.cell(row, 1, status)
        cell.value = status
        _style_status(cell)
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=2)
        ws.cell(row, 3, meaning).font = _font()
        row += 1
    row += 1

    # Where every file went
    row = _section(ws, row, "Where every source file went (folder Mercedes_Organized)")
    rec = reconciliation(summary, stats)
    plain = {
        "Class folders - matched to a page": "Images matched to a specific page of the class",
        "Class folders - sorted by class name": "Images whose file name names the class (any spelling) but "
                                                "that match no specific page - e.g. older generations",
        "_Remaining/Ambiguous Candidates/": "Images that fit several classes equally - needs review",
        "_Remaining/Not Matched/": "Mercedes images that no page in the list asks for",
        "_Duplicates/": "Exact copies of an image that is already placed elsewhere",
        "_Unrelated Files/": "Not Mercedes images, or not image files at all",
    }
    rows = [[n, c, plain.get(n, m)] for n, c, m in rec if n not in ("Totals match",)]
    start = row + 1
    row = _table(ws, row, ["Folder", "Files", "What is in it"], rows, filter_=False)
    for r_ in range(start, start + len(rows)):
        ws.cell(r_, 2).alignment = Alignment(horizontal="center", vertical="top")
        if str(ws.cell(r_, 1).value).startswith(("TOTAL", "Files in")):
            for col in range(1, 4):
                ws.cell(r_, col).font = _font(bold=True)
                ws.cell(r_, col).fill = BLUE
    ok = str(rec[-1][2]).startswith("YES")
    cell = ws.cell(row - 1, 1, "✔ Every source file is in the output exactly once - nothing was lost."
                   if ok else "✖ Output and source file counts differ - see the log.")
    cell.font = _font(bold=True, color=GREEN_TXT if ok else RED_TXT)
    if second is not None:
        row += 1
        recovered = second.status_counts[RECOVERED]
        ws.cell(row, 1, f"Second pass: the leftover folders were re-checked - {recovered} file(s) recovered "
                        "(see 'Second Pass Recovery').").font = _font(color=GREY_TXT)
    # The tiles row reuses columns A-F; keep the class table widths.
    _page_setup(ws)


def _missing_sheet(wb: Workbook, results, classes_with_images: set[str]) -> None:
    ws = wb.create_sheet("Missing Images")
    ws.sheet_properties.tabColor = "C00000"
    rows = []
    for cat, rs in results.items():
        for r in rs:
            if r.status == MISSING:
                why, todo = why_missing(r, classes_with_images)
                rows.append([cat, r.page.raw, searched_for(r), why, todo])
    row = _title(ws, f"Missing Images – {len(rows)} page(s) without an image",
                 "Grouped by class. 'Searched for' shows the identifiers tried, strongest first.", 5)
    if not rows:
        cell = ws.cell(row, 1, "NO MISSING IMAGES – every page has at least one image.")
        cell.font = _font(bold=True, size=14, color=GREEN_TXT)
        cell.fill = GREEN
        ws.column_dimensions["A"].width = 70
        return
    per_class = Counter(r[0] for r in rows)
    rows = [[f"{r[0]}  ({per_class[r[0]]})", *r[1:]] for r in rows]
    _table(ws, row, ["Class (missing)", "Page", "Searched for", "Why it is missing", "What you can do"],
           rows, [18, 34, 30, 70, 55], band_key=0, freeze=True)
    _page_setup(ws)


def _review_sheet(wb: Workbook, results) -> None:
    ws = wb.create_sheet("Needs Review")
    ws.sheet_properties.tabColor = "FFC000"
    rows = []
    for cat, rs in results.items():
        for r in rs:
            if r.status == AMBIGUOUS:
                text, _, _ = images_text(r.hits)
                rows.append([cat, r.page.raw, matched_by(r), text,
                             "Choose the right image in overrides.csv (Category, Page, Image)."])
    row = _title(ws, f"Needs Review – {len(rows)} page(s)",
                 "Images that fit pages in more than one class equally well. Nothing was copied for these.", 5)
    if not rows:
        cell = ws.cell(row, 1, "Nothing needs review – every match could be decided automatically.")
        cell.font = _font(bold=True, size=14, color=GREEN_TXT)
        cell.fill = GREEN
        ws.column_dimensions["A"].width = 70
        return
    _table(ws, row, ["Class", "Page", "Matched by", "Candidate images", "What to do"], rows,
           [16, 32, 22, 80, 50], band_key=0, freeze=True)
    _page_setup(ws)


def _detail_sheet(wb: Workbook, results, classes_with_images: set[str]) -> None:
    ws = wb.create_sheet("Detailed Matching")
    ws.sheet_properties.tabColor = "4472C4"
    rows = []
    for cat, rs in results.items():
        for r in rs:
            ids = [" / ".join(i.upper() for i in g) or "—" for _, g in r.page.identifiers.tiers()]
            if r.status == FOUND:
                text, renders, files = images_text(r.assigned)
                notes = plain_notes(r)
            elif r.status == AMBIGUOUS:
                text, renders, files = images_text(r.hits)
                renders = files = 0
                notes = "Fits several classes equally – see 'Needs Review'."
            else:
                text, renders, files = "—", 0, 0
                notes = why_missing(r, classes_with_images)[0]
            rows.append([cat, r.page.raw, *ids, RESULT_LABEL[r.status], matched_by(r) or "—",
                         renders, files, text, notes])
    row = _title(ws, "Detailed Matching – every page in the list",
                 "Identifiers are tried left to right; the first one found in an image name decides. "
                 "Each image line is one render; its views (front, back, side, 45°) are counted, not repeated.", 11)
    _table(ws, row, ["Class", "Page", "Identifier 1\n(model)", "Identifier 2\n(number)", "Identifier 3\n(chassis)",
                     "Result", "Matched by", "Images", "Files", "Matched images", "Notes"],
           rows, [14, 30, 13, 11, 13, 14, 20, 9, 8, 70, 55], status_col=5, band_key=0, freeze=True)
    for r_ in range(row + 1, row + 1 + len(rows)):
        for col in (8, 9):
            ws.cell(r_, col).alignment = Alignment(horizontal="center", vertical="top")
    _page_setup(ws)


def _class_name_sheet(wb: Workbook, results, stats: CopyStats, second) -> None:
    ws = wb.create_sheet("Sorted by Class Name")
    ws.sheet_properties.tabColor = "7030A0"
    class_chassis = {cat: {c for r in rs for c in r.page.identifiers.chassis} for cat, rs in results.items()}
    entries: list[tuple[str, str, str, str]] = []        # (class, file name, spelling found, source)
    for p in stats.placements:
        if p.kind == "class name":
            entries.append((p.bucket, p.dest.name, p.variant, "Not Matched (first pass)"))
    for r in (second.rows if second else []):
        if r.status == RECOVERED and r.identifier.startswith("class name"):
            entries.append((r.category, Path(r.moved_to).name, r.identifier.split("'")[1], "Not Matched (second pass)"))
    groups: dict[tuple, list] = defaultdict(list)
    for cat, name, variant, source in entries:
        groups[(cat, render_key(name), variant, source)].append(name)
    rows = []
    for (cat, key, variant, source), names in sorted(groups.items()):
        chassis = set(re.findall(r"(?<![a-z0-9])([a-z]{1,2}\d{3}[a-z]?)(?![a-z0-9])", key.lower()))
        page_models = {m for r_ in results.get(cat, []) for m in r_.page.identifiers.models}
        other = sorted(c.upper() for c in chassis - class_chassis.get(cat, set()) if c not in page_models)
        prefixes = {p for r_ in results.get(cat, []) for p in r_.page.identifiers.prefixes}
        models = sorted({(p + n).upper() for p, n in re.findall(r"(?<![a-z0-9])([a-z]{1,4}) ?(\d{2,3})(?=[a-z]?\b)",
                                                                key.lower())
                         if p in prefixes and p + n not in page_models})
        if other:
            why = f"Generation {', '.join(other)} is not one of the {cat} pages"
        elif models:
            why = f"Model {', '.join(models)} is not one of the {cat} pages"
        else:
            why = "Name has no model number or chassis code of a specific page"
        rows.append([cat, f"{short_name(key)}  ({len(names)} file{'s' if len(names) != 1 else ''})",
                     f"'{variant}'", why, source])
    total = sum(len(v) for v in groups.values())
    row = _title(ws, f"Sorted by Class Name – {total} file(s) moved out of Not Matched",
                 "These images name a class from the page list (any spelling: A-Class, a class, A-Dash Class, "
                 "G-L-E …) at the start of the file name, but no specific page matched them. They are in the "
                 "class folder; they do not count as images for a missing page.", 5)
    if not rows:
        ws.cell(row, 1, "No files were sorted by class name.").font = _font(bold=True, color=GREY_TXT)
        return
    per_class = Counter(r[0] for r in rows)
    rows = [[f"{r[0]}  ({per_class[r[0]]})", *r[1:]] for r in rows]
    _table(ws, row, ["Class (images)", "Image", "Class name found as", "Why no page matched it", "Moved from"],
           rows, [20, 80, 22, 52, 26], band_key=0, freeze=True)
    _page_setup(ws)


_STATUS_ORDER = {RECOVERED: 0, AMBIGUOUS: 1, DUPLICATE: 2, STILL_UNMATCHED: 3}


def _second_pass_sheet(wb: Workbook, second: SecondPassResult) -> None:
    ws = wb.create_sheet("Second Pass Recovery")
    ws.sheet_properties.tabColor = "ED7D31"
    counts = second.status_counts
    recovered = counts[RECOVERED]
    row = _title(ws, "Second Pass Recovery – leftover folders re-checked",
                 "Every file in _Duplicates, _Remaining and _Unrelated Files was checked again with exactly the "
                 "same rules as the first pass. Matches are moved into their class folder; everything else stays "
                 "where it is.", 7)
    labels = ("Duplicates", "Remaining", "Unrelated")
    got = [second.recovered_from(lb) for lb in labels]
    left = [second.still_in(lb) for lb in labels]
    rows: list[list] = [[lb, g + s_, g, s_] for lb, g, s_ in zip(labels, got, left)]
    rows.append(["All leftover folders", sum(got) + sum(left), sum(got), sum(left)])
    start = row + 1
    row = _table(ws, row, ["Folder", "Files checked", "Recovered", "Still there"], rows,
                 [26, 40, 18, 22, 22, 26, 60], filter_=False)
    for r_ in range(start, start + len(rows)):
        for col in (2, 3, 4):
            ws.cell(r_, col).alignment = Alignment(horizontal="center")
    for col in range(1, 5):
        ws.cell(start + len(rows) - 1, col).font = _font(bold=True)
        ws.cell(start + len(rows) - 1, col).fill = BLUE
    if recovered:
        msg = f"{recovered} file(s) were recovered into class folders (green rows at the top of the list)."
    else:
        msg = ("No files were recovered. The first pass had already checked every one of these files with the "
               "same rules, so this confirms nothing was missed. Files you add to these folders later are "
               "picked up on the next run.")
    cell = ws.cell(row - 1, 1, msg)
    cell.font = _font(italic=True, color=GREY_TXT)
    ws.merge_cells(start_row=row - 1, start_column=1, end_row=row - 1, end_column=7)
    cell.alignment = Alignment(wrap_text=True)
    ws.row_dimensions[row - 1].height = 32
    row += 1

    row = _section(ws, row, "Files checked (one line per render; recovered first)")
    groups: dict[tuple, list] = defaultdict(list)
    for r in second.rows:
        groups[(r.source_folder, r.status, render_key(r.file), r.category, r.entries, r.identifier, r.notes)].append(r)
    rows = []
    for (folder, status, key, cat, entries, ident, notes), members in sorted(
            groups.items(), key=lambda kv: (_STATUS_ORDER.get(kv[0][1], 9), kv[0][0], kv[0][2])):
        views = len(members)
        name = short_name(key) if key.lower().startswith(("mercedes", "amg", "sls")) else key
        rows.append([folder, f"{name}  ({views} file{'s' if views != 1 else ''})", cat or "—",
                     entries or "—", ident or "—", status, notes])
    _table(ws, row, ["Source Folder", "Image/File", "Matched Category", "Matched MD Entry", "Identifier Used",
                     "Status", "Notes"], rows, [16, 70, 16, 24, 18, 18, 60], status_col=5)
    _page_setup(ws)


def _run_info_sheet(wb: Workbook, summary: RunSummary, stats: CopyStats, second, dry_run, inputs) -> None:
    ws = wb.create_sheet("Run Info")
    ws.sheet_properties.tabColor = "A6A6A6"
    row = _title(ws, "Run Info", "Technical details of this run.", 2)
    info = [
        ["Generated", dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")],
        ["Mode", "Dry run (nothing copied)" if dry_run else "Normal"],
        *[[k, v] for k, v in inputs.items()],
        ["WebP images scanned", summary.webp],
        ["Files in source folder", summary.source_files],
        ["Files in output folder", summary.output_files],
        ["Image files in class folders", summary.matched_files],
        ["Remaining (Mercedes, not matched)", summary.remaining],
        ["Unrelated files", summary.unrelated],
        ["Duplicate files", summary.duplicates],
        ["Files copied this run", stats.copied],
        ["Files already up to date", stats.skipped_identical],
        ["Old output files removed", stats.removed_stale],
        ["Copy errors", len(stats.errors)],
        *([[f"Second pass: {k.lower()}", v] for k, v in sorted(second.status_counts.items())] if second else []),
        ["Matching rule", "Model (A200) → number (200) → chassis (W176); first identifier found wins. "
                          "Images showing another generation, another model or an AMG model are rejected."],
    ]
    _table(ws, row, ["Item", "Value"], info, [34, 110], filter_=False)


def write_excel(path: Path, results: dict[str, list[MatchResult]], summary: RunSummary, stats: CopyStats,
                dry_run: bool, inputs: dict[str, str], second: SecondPassResult | None = None,
                classes_with_images: set[str] | None = None) -> Path:
    classes_with_images = classes_with_images if classes_with_images is not None else set(results)
    wb = Workbook()
    _summary_sheet(wb, results, summary, stats, second, dry_run, inputs)
    _missing_sheet(wb, results, classes_with_images)
    _review_sheet(wb, results)
    _detail_sheet(wb, results, classes_with_images)
    _class_name_sheet(wb, results, stats, second)
    if second is not None:
        _second_pass_sheet(wb, second)
    _run_info_sheet(wb, summary, stats, second, dry_run, inputs)
    wb.active = 0
    try:
        wb.save(path)
    except PermissionError:
        alt = path.with_name(f"{path.stem}_{dt.datetime.now():%Y%m%d_%H%M%S}{path.suffix}")
        get_logger().error("%s is locked (open in Excel?). Saved to %s instead.", path, alt)
        wb.save(alt)
        path = alt
    get_logger().info("Excel report written: %s", path)
    return path
