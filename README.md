# Mercedes WebP Image Organizer

Python tool that reads the Engine Finders Mercedes page list (Markdown), finds the matching WebP renders in the image folder, copies them into one folder per category and produces an Excel report. The report answers one question:

> **Which Mercedes category is complete, and which category has which image missing?**

A category with nothing missing and nothing ambiguous is marked **`DONE - NO MISSING`**.

---

## Inputs

| Input | Default location | Role |
| --- | --- | --- |
| Page list | `EF_Mercedes_Full_Page_List.md` | Authoritative for **what should exist**: categories and their pages |
| Image folder | `Mercedez Split/` (searched recursively) | Authoritative for **what actually exists**. Treated as read-only |

### How the Markdown defines categories and pages

```markdown
## C-Class — 13 pages          <- category  (the declared count is checked against the bullets)
- C 200 (W204)                 <- page: engine badge + chassis code
- C 220 CDI / C 220 d          <- page: alternative badge names, no chassis code
- CLK 200 Kompressor (C209/A209)
- GLA 180 [[VERIFY CHASSIS CODE]]   <- chassis code still a placeholder in the source workbook
```

* Every `##` heading followed by bullet items is a **category**. The folder name is the heading text before the "— N pages" suffix.
* Every bullet under it is a **page**, meaning one expected image.
* Nothing is hard-coded. Categories, page counts, badges and chassis codes all come from the file. The current file has 29 categories and 207 pages.

### How the images are named

```text
mercedes-benz-c-class-w204-standard-saloon-2007–2011-new generation-hyacinth red metallic-c-class w204-front-view.webp
```

Most filenames contain the class (`c-class`) and the chassis code (`w204`). Only some contain a model identifier such as `c63`, `slk 200` or `gls600`. Each render exists in 4 views; every view is matched and copied on its own.

## How matching works

The whole Markdown entry never has to equal a filename. Each entry is broken into **identifiers**, and the program asks whether **any** of them appears in a WebP filename.

| Markdown entry | Identifier 1 (model) | Identifier 2 (number) | Identifier 3 (chassis) |
| --- | --- | --- | --- |
| `A 180` | `A180` | `180` | none |
| `A 200 (W176)` | `A200` | `200` | `W176` |
| `A 220 d (W177)` | `A220` | `220` | `W177` |
| `CLK 200 Kompressor (C209/A209)` | `CLK200` | `200` | `C209`, `A209` |
| `AMG GT 63` | `AMGGT63`, `GT63` | `63` | none |

### Priority

The tiers are tried in order. The **first tier that finds a usable file wins**, and the program records which identifier produced the match.

1. **Model**: `A200`.
2. **Number**: `200`. It must stand alone, so it never matches inside `2005` or `B200`.
3. **Chassis**: `W176`. `W463` does not match `W463A`.

### Normalisation

Filenames and identifiers are normalised the same way:

* Matching ignores case, accents and the `.webp` / `.WEBP` extension.
* A separator between a letter and a digit is ignored, so `A 180`, `A-180`, `A_180` and `A180` are the same, and so are `W 176`, `W-176` and `W176`.
* The camera-angle suffix is removed first (`-45-angle-front-view`, `-back-view`, …). Otherwise `…-sl-45-angle…` would read as "SL45".

### Context (rule 8: generic numbers)

Numbers like `200` and chassis codes like `W166` exist in several classes. A match is therefore only accepted when the context agrees:

* **Scope.** A file is searched only for the classes whose name appears in it (`a-class`, `gla`, `cl`, `amg gt`). A file that names **no** class (`Mercedes_W246.webp`, `A200.webp`) can be found by every class. A file that names a class **not** in the Markdown (for example `clc-class`) belongs to that class and is not offered to the others.
* **Conflicts.** A file is rejected for an entry if it carries a *different* identifier of the same kind:
  * A different chassis: the `a 200 turbo` image from the **W169** is not used for `A 200 (W176)`.
  * A different model number: the `c63` image is not used for `C 200 (W204)`.
  * An AMG model (2-digit badge such as `gla45`) for a non-AMG entry.
* **Claims.** If entries of different classes match the same file, the stronger tier wins. On a tie, the class named first in the filename wins. If the filename names neither class, nobody gets the file and the entries are **AMBIGUOUS**. One generic `Mercedes_200.webp` is never copied into every class.

### Multiple matches and duplicates

* All matching images at the winning tier are kept. Example: both the standard and the facelift W204 for `C 200 (W204)`.
* Exact duplicates are detected by **SHA-256** only. Similar names are never treated as duplicates.
* Within a class folder each image is kept once; the other copies go to `_Duplicates/`.
* An entry is **MISSING** when no identifier matches any usable file. The notes say whether identifiers were found but rejected by context, and why.

## Outputs

Every source file is copied exactly once, so the output always contains the same number of files as the source folder.

| Folder in `Mercedes_Organized/` | Contents |
| --- | --- |
| `<Class>/` (one per Markdown class) | Images matched to an entry of that class. Original filenames are kept |
| `_Remaining/Ambiguous Candidates/` | Images claimed equally by several classes (needs review) |
| `_Remaining/Not Matched/` | Mercedes images that no Markdown entry matched |
| `_Duplicates/` | Exact SHA-256 copies of an image placed elsewhere |
| `_Unrelated Files/` | Files that aren't Mercedes images: no brand or class word, not WebP, `.DS_Store` |

Reports are written next to `main.py`:

| File | Contents |
| --- | --- |
| `missing_mercedes_images.xlsx` | Excel report (sheets below) |
| `processing_report.txt` | Totals, the status of each class and the folder reconciliation |
| `mercedes_organizer.log` | Every identifier, match decision, rejection, claim, copy and error |

### Excel sheets

The workbook reads front to back, from overview to technical detail. Image files appear under short readable names (`C-Class W204 standard saloon (2007–2011)`). The 4 camera views of one render are shown as one line.

| Sheet | What it shows |
| --- | --- |
| **Summary** | Key numbers as tiles; one row per class with a % complete bar, a colour-coded status and the pages without an image; a status legend; where every source file went |
| **Missing Images** | Every page without an image: what was searched for, **why** it is missing in plain words, and **what you can do**. Grouped by class |
| **Needs Review** | Pages whose images fit more than one class equally (empty when there are none) |
| **Detailed Matching** | Every page: identifiers 1–3, result, what it was matched by, and the matched images |
| **Sorted by Class Name** | Images placed in a class folder by their class name only, with the spelling found and why no page matched them |
| **Second Pass Recovery** | Totals per leftover folder, then each re-checked render with its status (recovered first) |
| **Run Info** | Technical details: paths, file counts, copy statistics |

### Category status values

| Status | Meaning |
| --- | --- |
| `DONE - NO MISSING` | Every entry has at least one matched image |
| `MISSING IMAGES` | At least one entry has no image |
| `REVIEW REQUIRED` | At least one entry is ambiguous |
| `MISSING IMAGES + REVIEW REQUIRED` | Both of the above |

## Class-name sort: emptying `Not Matched`

Many images name a class from the page list but match no specific *page*: older generations (A-Class W168), coupés and estates (C-Class C205, S205), variants without a page (C43 AMG). Instead of leaving them in `_Remaining/Not Matched/`, the program puts them in their **class folder** by the class name in the file name:

* **Patterns come from the Markdown class names. Nothing is hard-coded.** They are case-insensitive and ignore separators:
  * `A-Class` also matches `a class`, `A_CLASS`, `aclass`, `A-Dash Class`, `a-dash-class` and `A DASH CLASS`.
  * Class names of 3–4 letters may be spelled out: `GLE` also matches `G-L-E`, `G L E`, `G, L, E` and `g-dash-l-dash-e`.
  * `AMG GT` also matches `amg-gt` and `AMGGT`.
* **The class at the start of the file name decides.** `mercedes-benz-slc-r170 slk …` is SLC even though it mentions SLK.
* **Unknown families stay put.** If the name starts with a family that is not in the page list (`sls amg`, `glk`, `clc-class`, `vaneo`, `vario`), the file stays in `Not Matched`. A later mention such as "sls amg **gt**" does not count.
* **Free-form names** (`photo of my a-dash-class.webp`) are sorted only when exactly one class is named.
* **Pages are not affected.** These images do **not** make a missing page FOUND; they are counted separately ("sorted by class name").
* The **Sorted by Class Name** sheet lists each of these images with the spelling found and why no page matched it (for example "Generation W168 is not one of the A-Class pages" or "Model C43 is not one of the C-Class pages").

Use `--no-class-name-sort` to switch this off.

## Second pass: re-checking the leftover folders

After copying, every normal run (`python main.py`) re-checks every file in `_Duplicates/`, `_Remaining/` and `_Unrelated Files/` (any depth). It uses **exactly the first-pass rules**: the same matcher (model → number → chassis, context checks, cross-class claims) runs over the whole output folder. The folder a file currently sits in is ignored.

| Second-pass status | Meaning | What happens |
| --- | --- | --- |
| `RECOVERED` | The rules assign the file to a class | Moved into that class folder |
| `DUPLICATE` | A file in `_Duplicates/`, an exact SHA-256 copy of a file elsewhere (if it matches a class, that class already has this content) | Left in place |
| `AMBIGUOUS` | Claimed equally by several classes | Left in place |
| `STILL UNMATCHED` | No identifier from the Markdown matched in a valid context | Left in place |

* Files only ever move from a leftover folder into a class folder, never back.
* The pass rescans until nothing new is recovered.
* Nothing is deleted, and the source folder is not touched.
* The results are in the **Second Pass Recovery** sheet, the *Recovered (2nd pass)* column of **Category Status**, `processing_report.txt` and the terminal.

**On an unchanged output folder the second pass recovers nothing, by design.** The first pass already applied the same rules to every source file, duplicates included. The pass recovers files you **add** to a leftover folder yourself (for example a renamed `Mercedes_B200_W246.webp`), or files left behind when the output drifts from the rules. A normal re-run keeps files that do not come from the source folder, so a recovery is not undone.

Use `--no-second-pass` to skip it. With `--dry-run` it only reports what it would move.

## Manual decisions: `overrides.csv`

Create `overrides.csv` next to `main.py`. It is picked up automatically, or you can pass it with `--overrides PATH`:

```csv
Category,Page,Image
A-Class,A 180,mercedes-benz-a-class-w176-standard style-hatchback-2012–2015-major transition to low, sporty hatchback-hyacinth red metallic-a-class w176 std style
GLA,GLA 180 [[VERIFY CHASSIS CODE]],MISSING
```

* `Page` must match the Markdown bullet text.
* `Image` is a filename, or the name shared by all the views of one render (the filename without `-front-view` etc.).
* An `Image` of `MISSING` forces the entry to MISSING.
* An override counts as the strongest claim.
* Rows that match nothing are reported as problems. They are never applied silently.

## Installation

Requires Python 3.10 or later.

```bash
python -m pip install -r requirements.txt
```

## Usage

```bash
python main.py --dry-run     # analyse and write the reports only; copies nothing, creates no output folder
python main.py               # analyse, copy every file into the output folders and write the reports
```

Options:

```text
--markdown PATH     Markdown page list            (default: EF_Mercedes_Full_Page_List.md)
--source PATH       WebP image folder, read-only  (default: "Mercedez Split")
--output PATH       output folder                 (default: Mercedes_Organized)
--report-dir PATH   where the xlsx/txt/log go     (default: project folder)
--overrides PATH    manual assignments CSV        (default: overrides.csv if present)
--keep-stale        keep files from earlier runs in the output folder
--no-second-pass    skip re-checking _Duplicates / _Remaining / _Unrelated Files
--no-class-name-sort do not sort unmatched images into class folders by class name
-v, --verbose       also print log detail in the terminal
```

### Guarantees

* **Source files are never modified.** The tool only reads them and uses `shutil.copy2`. It never moves, renames or deletes anything in the source folder.
* **Folders cannot overlap.** The program refuses to run if the output and source folders contain each other.
* **Re-runs are idempotent.** A file that already exists in the output with an identical SHA-256 is skipped.
* **Stale files are cleaned only in the generated output folder.** Copies of source files that the current plan no longer places in that spot are removed from `Mercedes_Organized/`, and each removal is logged. Files that do not come from the source folder (added by hand, or recovered by the second pass) are never deleted. Pass `--keep-stale` to keep everything.
* Long Windows paths (over 260 characters) are handled.

## Tests

```bash
python -m unittest discover -s tests -t . -v
```

The tests build small synthetic folders. They cover the identifier tiers (`A200` → `200` → `W176`, `A220` → `220` → `W177`, `B200` → `W246`), name normalisation, class-specific IDs versus a generic `200`, context rejections, year and view-suffix pitfalls, duplicates, unrelated files, placing every file exactly once, dry run, idempotency, stale cleanup, the read-only source and overrides.

## Project structure

```text
main.py                         CLI entry point
mercedes_organizer/
  parser.py                     Markdown -> classes, entries, identifiers (model / number / chassis)
  catalog.py                    recursive file index: normalised names, class context, SHA-256 duplicates
  matcher.py                    identifier tiers, context checks, cross-class claims, overrides
  organizer.py                  placement plan, copying (idempotent, long-path safe), stale cleanup
  class_names.py                class-name patterns (case, spacing, hyphen, 'dash' and spelled-out variants)
  second_pass.py                re-check of the leftover folders with the same rules; recovery moves
  excel_report.py               the Excel workbook (readable layout)
  reporter.py                   totals, text report, terminal summary
  utils.py                      normalisation, paths, logging
tests/test_organizer.py
```

## Troubleshooting

| Symptom | Fix |
| --- | --- |
| `ModuleNotFoundError: openpyxl` | `python -m pip install -r requirements.txt` |
| `Markdown file not found` / `Source image folder not found` | Pass `--markdown` / `--source` |
| Excel saved as `missing_mercedes_images_YYYYMMDD_HHMMSS.xlsx` | The original file was open in Excel. Close it and run again |
| An entry is MISSING but you know an image exists | Read its *Notes*: they list the identifiers searched and any context rejections (for example a different chassis). Rename the image to include an identifier, or add a row to `overrides.csv` |
| An entry is AMBIGUOUS | The same file was claimed equally by several classes. Its candidates are in the *Ambiguous Matches* sheet; decide with `overrides.csv` |
| Garbled `–` / `é` in the terminal | Only a display issue. Files, the Excel report and the log are UTF-8 and correct |
