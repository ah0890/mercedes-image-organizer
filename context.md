# Project context: Mercedes WebP Image Organizer

Read this first. It holds what you need to continue without re-reading the code or the conversation. Last updated: 2026-10-07, after the Not Matched class-name sub-sort.

## Purpose
- Sort the WebP images in `Mercedez Split/` into one folder per Mercedes class, using `EF_Mercedes_Full_Page_List.md` (the Engine Finders page list).
- Report which pages have no image, in an Excel workbook.
- The source folder and the Markdown are **read-only** and must never be modified.

## Inputs (facts already checked)
- **Markdown.** 29 `## <Class> — N pages` headings with 207 `- ` bullet pages.
  - Pages are engine badges, not filenames: `A 200 (W176)`, `E 220 CDI / E 220 d (W211)`, `CLK 200 Kompressor (C209/A209)`.
  - 5 GLA pages carry the placeholder `[[VERIFY CHASSIS CODE]]`.
  - The `## Total: 207 pages` heading has no bullets, so it is ignored.
  - The Markdown contains **no** alias mappings (nothing for GLK, CLC, SLS AMG, Vaneo or Vario).
- **Images.** 1,537 files: 1,536 WebP plus `.DS_Store`, flat, no subfolders.
  - 384 renders × 4 views (`-45-angle-front-view`, `-back-view`, `-flat-side-view`, `-front-view`).
  - Name pattern: `mercedes-benz-{family}-{chassis}-{variant}-{body}-{years}-{note}-{colour}-{label}-{view}.webp`.
  - 124 files are exact SHA-256 duplicates.
  - 4 `ChatGPT Image …` files are unrelated.
  - The WebP files hold no text metadata (image data only), so the filename is the only text available.

## Matching rules currently in force (first pass)
1. **Identifiers per page, tried in this order:** model `a200` → number `200` → chassis `w176`. The first tier with a usable file wins. Normalisation: case, accents, separators (`A-200` = `a200`), extension, view suffix stripped.
2. **Scope.** Only files whose name contains the category word (`a-class`, `gla`, `cl`, `amg gt`). Files naming no class are open to every class. Files naming a non-Markdown `x-class` (e.g. `clc-class`) are open to none.
3. **Conflicts reject a file:**
   - a different chassis (an `a 200 turbo` W169 image is not `A 200 (W176)`);
   - a different model with the same prefix (`c63` is not `C 200`);
   - an AMG 2-digit badge for a non-AMG page.
4. **Cross-class claims.** The stronger tier wins. On a tie, the class named first in the filename wins. If the file names neither class, it is AMBIGUOUS.
5. All matching renders at the winning tier are kept. Duplicates are collapsed per class folder by SHA-256.

## Output layout (`Mercedes_Organized/`). Every source file appears exactly once.
| Folder | Files now |
|---|---|
| 25 class folders with files (+4 empty: CLE, GLB, GL-Class, X-Class) | 684 |
| `_Remaining/Not Matched/` | 724: 652 in 22 class subfolders (from `segregate_not_matched.py`), 72 directly inside (44 no class, 28 ambiguous) |
| `_Remaining/Ambiguous Candidates/` | 0 |
| `_Duplicates/` | 124 |
| `_Unrelated Files/` | 5 |

Results: 128 of 207 pages FOUND, 79 MISSING, 0 AMBIGUOUS. Only **CLA** is `DONE - NO MISSING`.

## Code map
- `main.py`: CLI. Flags: `--dry-run`, `--source`, `--markdown`, `--output`, `--report-dir`, `--overrides`, `--keep-stale`, `--no-second-pass`, `-v`.
- `mercedes_organizer/parser.py`: categories, pages, `page.identifiers` (models, numbers, chassis, prefixes).
- `catalog.py`: per-file index (compact/spaced names, contexts, `primary_context`, `foreign_class`, `is_amg`, SHA-256 duplicates).
- `matcher.py`: tiers, `conflict()`, claims (`Matcher.owner`), overrides (`overrides.csv`: Category, Page, Image).
- `organizer.py`: `build_plan()` (one placement per source file), copying with `shutil.copy2`.
  - It is idempotent.
  - Stale cleanup removes only output files whose content exists in the source; files added by hand are never deleted.
  - Long Windows paths are handled through `fs_path()`.
- `second_pass.py`: re-checks the leftover folders with the same Matcher over the output and moves recoveries into the class folders. On unchanged output it recovers 0, by design.
- `reporter.py`: totals, reconciliation, `processing_report.txt`, terminal summary.
- `excel_report.py`: `missing_mercedes_images.xlsx` with 6 sheets: Summary, Missing Images, Needs Review, Detailed Matching, Second Pass Recovery, Run Info. It uses short readable image names and plain-language reasons.
- `segregate_not_matched.py` (root): optional class-name sub-sort inside `_Remaining/Not Matched/` only. Use `--dry-run` first.
- `tests/test_organizer.py`: 24 unittest tests. Run `python -m unittest discover -s tests -t .`

## History (what was done and undone)
1. First pass built, then refined:
   - the identifier-tier rule requested by the user;
   - fixes: view-suffix "SL45" bug, AMG detection from 2-digit badges, primary-context tie-break, numbers searched in the "spaced" form, `clc-class` foreign class.
2. Leftover folders added so the output mirrors the source (1,537 = 1,537).
3. Second pass added. It recovered 0 files.
4. Excel redesigned for readability.
5. **Class-name sort** (A-Class / a-dash class / G-L-E spellings into class folders; 672 files moved, leaving 52) was added and then **ROLLED BACK on 2026-10-07 at the user's request**.
   - Do not reintroduce it unless the user asks again.
   - The removed code (`class_names.py`) and the pre-rollback output are in `backup_before_rollback/` (it has a `MANIFEST.sha256`).
   - The 672 moves are listed in `rollback_moves.csv`; details are in `rollback_report.txt`.
6. **Not Matched sub-sort (2026-10-07, user request).** `segregate_not_matched.py` moves files *within* `_Remaining/Not Matched/` into `Not Matched/<Class>/` by class name only.
   - Spellings are generated from the MD classes (A-Class ↔ A_Class ↔ Class A ↔ aclass …).
   - Several classes, or the file's own family being different (SLS AMG GT, SLC/SLK R170, V-Class/Viano W639), means AMBIGUOUS and the file stays put.
   - Result: 652 moved, 44 no match (CLC, GLK, SLS AMG, Vaneo, Vario), 28 ambiguous. Report: `Not Matched/class_name_segmentation_report.xlsx`.
   - This is NOT the rolled-back feature: main class folders are untouched.
   - `organizer.organize()` treats a file found in a `Not Matched/<Class>/` subfolder as already in place.
   - `second_pass` and `folder_counts` ignore the report file (`GENERATED_REPORTS`).
7. The user drafted a LinkedIn post about the project (client-oriented, "5 hours → 10 seconds" hook).

## Git
- Commits: `83ddf64` (initial README) and `f2bc53d` "Successfully done" (user, 2026-10-06 21:50). `f2bc53d` contains the post-class-name code and `Mercedes_Organized.rar` (post-class-name state).
- Working tree after the rollback is uncommitted: code reverted, `class_names.py` deleted, `.rar` deleted by the user, `Mercedes_Organized.zip` untracked (also post-class-name).
- Don't commit unless asked.

## Gotchas that cost time before
- When the user has `missing_mercedes_images.xlsx` open in Excel, saving fails. The program then writes `missing_mercedes_images_YYYYMMDD_HHMMSS.xlsx`. Tell the user to close Excel and re-run; don't delete the only fresh copy.
- Bash heredocs mangle backslashes and `\n` inside Python patch scripts. Write the patch script to the scratchpad with the Write tool, or use Edit.
- Paths over 260 characters: always go through `fs_path()` (`\\?\` prefix) when walking or hashing output files.
- Excel COM automation is not registered on this machine, so workbooks can't be rendered to PDF/PNG. Verify them with openpyxl instead.
- Source integrity: `scratchpad/fingerprint.py` writes `hash size mtime path` lines. Compare them with the same `PYTHONIOENCODING` setting, or the encoding differs.

## Open decisions for the user
- 79 missing pages need images whose filenames contain the model number or chassis code, or rows in `overrides.csv`.
- Whether GLK, CLC, SLS AMG, Vaneo and Vario should map to any class. They are not in the Markdown.
