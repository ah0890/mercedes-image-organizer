"""Unit tests on small synthetic folders.  Run:  python -m unittest discover -s tests -t . -v"""

from __future__ import annotations

import csv
import os
import tempfile
import unittest
from pathlib import Path

from mercedes_organizer.catalog import build_catalog
from mercedes_organizer.matcher import AMBIGUOUS, FOUND, MISSING, Matcher, load_overrides
from mercedes_organizer.organizer import organize
from mercedes_organizer.parser import parse_markdown

AB_MARKDOWN = """# Page list
Intro text, not a category.

## A-Class — 3 pages
- A 180
- A 200 (W176)
- A 220 d (W177)

## B-Class — 1 pages
- B 200 (W246)

---
"""


class Case:
    """A temp source folder + Markdown, matched in memory."""

    def __init__(self, files: dict[str, bytes], markdown: str = AB_MARKDOWN):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.source = root / "src"
        self.source.mkdir()
        for name, content in files.items():
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        md = root / "list.md"
        md.write_text(markdown, encoding="utf-8")
        self.output = root / "out"
        self.categories = parse_markdown(md)
        self.catalog = build_catalog(self.source, [c.name for c in self.categories])
        self.matcher = Matcher(self.catalog, self.categories)
        self.results, self.problems = self.matcher.match_all()

    def result(self, page: str):
        return next(r for rows in self.results.values() for r in rows if r.page.raw == page)

    def organize(self, dry_run=False):
        return organize(self.results, self.catalog, self.output, self.matcher.owner, dry_run)

    def close(self):
        self.tmp.cleanup()


class IdentifierTest(unittest.TestCase):
    def check(self, files, page, status, tier=None, category=None):
        case = Case({name: name.encode() for name in files})
        try:
            r = case.result(page)
            self.assertEqual(r.status, status, r.notes)
            if tier:
                self.assertEqual(r.tier, tier)
            if category:
                self.assertEqual(r.category, category)
            return r
        finally:
            case.close()

    def test_parser_identifiers(self):
        case = Case({})
        try:
            self.assertEqual([c.name for c in case.categories], ["A-Class", "B-Class"])
            ids = case.result("A 200 (W176)").page.identifiers
            self.assertEqual((ids.models, ids.numbers, ids.chassis), (("a200",), ("200",), ("w176",)))
            ids = case.result("A 180").page.identifiers
            self.assertEqual((ids.models, ids.numbers, ids.chassis), (("a180",), ("180",), ()))
        finally:
            case.close()

    def test_a200_matches_a200(self):
        self.check(["Mercedes_A200_W176.webp"], "A 200 (W176)", FOUND, "model")

    def test_a200_falls_back_to_200(self):
        self.check(["Mercedes A-Class 200.webp"], "A 200 (W176)", FOUND, "number")

    def test_a200_falls_back_to_w176(self):
        self.check(["mercedes-benz-a-class-w176-standard-front-view.webp"], "A 200 (W176)", FOUND, "chassis")

    def test_a220_falls_back_to_220_then_w177(self):
        self.check(["Mercedes A-Class 220.webp"], "A 220 d (W177)", FOUND, "number")
        self.check(["Mercedes_W 177.webp"], "A 220 d (W177)", FOUND, "chassis")

    def test_b200_falls_back_to_w246(self):
        r = self.check(["Mercedes_W246.webp"], "B 200 (W246)", FOUND, "chassis", "B-Class")
        self.assertEqual(r.assigned[0].file.name, "Mercedes_W246.webp")

    def test_normalised_variants(self):
        for name in ["A-180.webp", "a_180.WEBP", "Mercedes A 180.webp", "MERCEDES_A180.webp"]:
            self.check([name], "A 180", FOUND, "model")

    def test_class_specific_model_ids(self):
        case = Case({"Mercedes_A200.webp": b"a", "Mercedes_B200.webp": b"b"})
        try:
            self.assertEqual([h.file.name for h in case.result("A 200 (W176)").assigned], ["Mercedes_A200.webp"])
            self.assertEqual([h.file.name for h in case.result("B 200 (W246)").assigned], ["Mercedes_B200.webp"])
        finally:
            case.close()

    def test_generic_number_is_not_copied_into_every_class(self):
        case = Case({"Mercedes_200.webp": b"x"})
        try:
            self.assertEqual(case.result("A 200 (W176)").status, AMBIGUOUS)
            self.assertEqual(case.result("B 200 (W246)").status, AMBIGUOUS)
            stats = case.organize()
            self.assertEqual(stats.category_files, 0)
            self.assertTrue((case.output / "_Remaining" / "Ambiguous Candidates" / "Mercedes_200.webp").is_file())
        finally:
            case.close()

    def test_context_rejects_other_generation_and_other_model(self):
        r = self.check(["mercedes-benz-a-class-w169-a 200 turbo-hatchback.webp"], "A 200 (W176)", MISSING)
        self.assertIn("different chassis", r.notes)
        md = "## C-Class — 1 pages\n- C 200 (W204)\n"
        case = Case({"mercedes-benz-c-class-w204-c63 amg.webp": b"1",
                     "mercedes-benz-c-class-w204-standard-saloon.webp": b"2"}, md)
        try:
            self.assertEqual([h.file.name for h in case.result("C 200 (W204)").assigned],
                             ["mercedes-benz-c-class-w204-standard-saloon.webp"])
        finally:
            case.close()

    def test_years_do_not_match_numbers(self):
        self.check(["mercedes-benz-a-class-hatchback-2005-2012.webp"], "A 200 (W176)", MISSING)

    def test_view_suffix_is_not_a_badge(self):
        md = "## SL — 1 pages\n- SL 500 (R129)\n"
        case = Case({"mercedes-benz-sl-r129-sl-roadster-sl-45-angle-front-view.webp": b"1"}, md)
        try:
            self.assertEqual(case.result("SL 500 (R129)").status, FOUND)
        finally:
            case.close()

    def test_missing(self):
        r = self.check(["Mercedes_W999.webp"], "A 180", MISSING)
        self.assertIn("A180", r.notes)


class OrganizerTest(unittest.TestCase):
    FILES = {
        "Mercedes_A180.webp": b"a180",
        "sub/Mercedes_A180 copy.webp": b"a180",            # exact duplicate (recursive)
        "Mercedes_W246.webp": b"w246",
        "mercedes-benz-glk-x204-suv.webp": b"glk",          # Mercedes, but no entry
        "ChatGPT Image.webp": b"chatgpt",                   # unrelated
        ".DS_Store": b"",
    }

    def setUp(self):
        self.case = Case(self.FILES)

    def tearDown(self):
        self.case.close()

    def test_every_source_file_placed_once(self):
        stats = self.case.organize()
        out = self.case.output
        self.assertEqual(len([p for p in out.rglob("*") if p.is_file()]), len(self.FILES))
        self.assertTrue((out / "A-Class" / "Mercedes_A180.webp").is_file())
        self.assertTrue((out / "B-Class" / "Mercedes_W246.webp").is_file())
        self.assertTrue((out / "_Duplicates" / "sub" / "Mercedes_A180 copy.webp").is_file())
        self.assertTrue((out / "_Remaining" / "Not Matched" / "mercedes-benz-glk-x204-suv.webp").is_file())
        self.assertTrue((out / "_Unrelated Files" / "ChatGPT Image.webp").is_file())
        self.assertTrue((out / "_Unrelated Files" / ".DS_Store").is_file())
        self.assertEqual(stats.placed_files, len(self.FILES))

    def test_dry_run_copies_nothing(self):
        stats = self.case.organize(dry_run=True)
        self.assertEqual(stats.planned, len(self.FILES))
        self.assertFalse(self.case.output.exists())

    def test_idempotent_source_untouched_and_stale_cleanup(self):
        src = self.case.source
        before = sorted((str(p.relative_to(src)), p.read_bytes(), p.stat().st_mtime_ns)
                        for p in src.rglob("*") if p.is_file())
        first = self.case.organize()
        stale = self.case.output / "B-Class" / "Mercedes_A180.webp"        # source copy in the wrong place
        stale.write_bytes(b"a180")
        hand_added = self.case.output / "A-Class" / "added-by-hand.webp"   # not from the source folder
        hand_added.write_bytes(b"mine")
        second = self.case.organize()
        self.assertEqual(second.copied, 0)
        self.assertEqual(second.skipped_identical, first.copied)
        self.assertEqual(second.removed_stale, 1)
        self.assertFalse(stale.exists())
        self.assertTrue(hand_added.exists())
        after = sorted((str(p.relative_to(src)), p.read_bytes(), p.stat().st_mtime_ns)
                       for p in src.rglob("*") if p.is_file())
        self.assertEqual(before, after)

    def test_output_inside_source_is_refused(self):
        with self.assertRaises(ValueError):
            organize(self.case.results, self.case.catalog, self.case.source / "out", self.case.matcher.owner, False)

    def test_overrides(self):
        path = Path(self.case.tmp.name) / "overrides.csv"
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["Category", "Page", "Image"])
            w.writerow(["A-Class", "A 200 (W176)", "mercedes-benz-glk-x204-suv"])
            w.writerow(["A-Class", "A 180", "MISSING"])
        results, problems = Matcher(self.case.catalog, self.case.categories).match_all(load_overrides(path))
        self.assertEqual(problems, [])
        rows = {r.page.raw: r for r in results["A-Class"]}
        self.assertEqual(rows["A 200 (W176)"].status, FOUND)
        self.assertEqual(rows["A 180"].status, MISSING)


class NotMatchedSegregationTest(unittest.TestCase):
    def test_patterns_from_class_names(self):
        import segregate_not_matched as seg
        pats = {c: seg.class_patterns(c) for c in ["A-Class", "B-Class", "AMG GT", "GLE", "SL", "SLC"]}
        for name, expected in [("Mercedes_A-Class_200", ["A-Class"]), ("Mercedes_Class_A_200", ["A-Class"]),
                               ("Mercedes_A_Class_200", ["A-Class"]), ("class-a", ["A-Class"]), ("ACLASS", ["A-Class"]),
                               ("B-Class-220", ["B-Class"]), ("amg_gt 63", ["AMG GT"]), ("my GLE", ["GLE"]),
                               ("data", []), ("amgx", []), ("sls", []), ("class amg", [])]:
            found, _ = seg.classify(name + ".webp", pats)
            self.assertEqual([c for c, _ in found], expected, name)
        found, family = seg.classify("mercedes-benz-sls amg-c197-sls amg gt-coupe.webp", pats)
        self.assertEqual(([c for c, _ in found], family), (["AMG GT"], "sls amg"))   # -> AMBIGUOUS in main()

    def test_main_run_keeps_segregated_subfolders(self):
        case = Case({"Mercedes_W999_A-Class.webp": b"x", "Mercedes_A180.webp": b"a"})
        try:
            case.organize()
            nm = case.output / "_Remaining" / "Not Matched"
            (nm / "A-Class").mkdir()
            os.replace(nm / "Mercedes_W999_A-Class.webp", nm / "A-Class" / "Mercedes_W999_A-Class.webp")
            again = case.organize()
            self.assertEqual((again.copied, again.removed_stale), (0, 0))
            self.assertTrue((nm / "A-Class" / "Mercedes_W999_A-Class.webp").is_file())
            self.assertFalse((nm / "Mercedes_W999_A-Class.webp").exists())
        finally:
            case.close()


class ViewSegregationTest(unittest.TestCase):
    def test_detection_variants(self):
        from mercedes_organizer.view_categories import detect, enabled_categories
        on = enabled_categories()
        self.assertEqual([c.folder for c in on], ["45 Angle Front View"])
        for name in ["A180-45-angle-front-view.webp", "A180_45_angle_front_view.webp", "C200-45-Angle-Front-View.webp",
                     "Mercedes-A-Class-45-angle-front-view.webp", "45-angle-front-view.webp", "x 45 Angle Front View.webp"]:
            self.assertEqual(detect(name, on)[0].folder, "45 Angle Front View", name)
        for name in ["A200-side-view.webp", "C300-front-view.webp", "w145-angle-front-view.webp", "45-angle-front-viewer.webp"]:
            self.assertIsNone(detect(name, on), name)

    def test_moves_inside_class_only_and_rerun_is_safe(self):
        import segregate_views as sv
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "Mercedes_Organized"
            for rel, data in {"A-Class/A180-45-angle-front-view.webp": b"1", "A-Class/A180-back-view.webp": b"2",
                              "C-Class/C200_45_Angle_Front_View.webp": b"3",
                              "C-Class/45 Angle Front View/C200_45_Angle_Front_View.webp": b"other",   # collision
                              "_Remaining/Not Matched/X-45-angle-front-view.webp": b"4"}.items():
                (root / rel).parent.mkdir(parents=True, exist_ok=True)
                (root / rel).write_bytes(data)
            saved = (sv.PLAN, sv.REPORT, sv.LOG)
            sv.PLAN, sv.REPORT, sv.LOG = Path(tmp) / "plan.csv", Path(tmp) / "r.xlsx", Path(tmp) / "l.log"
            try:
                from mercedes_organizer.view_categories import enabled_categories
                items = sv.plan(root, enabled_categories())
                sv.write_plan(items, root, False)
                sv.execute(root, items, False)
                self.assertTrue((root / "A-Class/45 Angle Front View/A180-45-angle-front-view.webp").is_file())
                self.assertTrue((root / "A-Class/A180-back-view.webp").is_file())                  # not matched: stays
                self.assertTrue((root / "C-Class/45 Angle Front View/C200_45_Angle_Front_View (2).webp").is_file())
                self.assertEqual((root / "C-Class/45 Angle Front View/C200_45_Angle_Front_View.webp").read_bytes(),
                                 b"other")                                                        # never overwritten
                self.assertTrue((root / "_Remaining/Not Matched/X-45-angle-front-view.webp").is_file())  # out of scope
                again = sv.plan(root, enabled_categories())
                self.assertFalse([i for i in again if i.action == "move"])
                self.assertEqual(sum(i.status == sv.ALREADY for i in again), 3)
            finally:
                sv.PLAN, sv.REPORT, sv.LOG = saved

    def test_main_run_keeps_view_subfolders(self):
        case = Case({"Mercedes_A180-45-angle-front-view.webp": b"a", "Mercedes_A180-back-view.webp": b"b"})
        try:
            case.organize()
            cls = case.output / "A-Class"
            (cls / "45 Angle Front View").mkdir()
            os.replace(cls / "Mercedes_A180-45-angle-front-view.webp",
                       cls / "45 Angle Front View" / "Mercedes_A180-45-angle-front-view.webp")
            again = case.organize()
            self.assertEqual((again.copied, again.removed_stale), (0, 0))
            self.assertTrue((cls / "45 Angle Front View" / "Mercedes_A180-45-angle-front-view.webp").is_file())
            self.assertFalse((cls / "Mercedes_A180-45-angle-front-view.webp").exists())
        finally:
            case.close()


class SecondPassTest(unittest.TestCase):
    FILES = {
        "Mercedes_A180.webp": b"a180",
        "sub/Mercedes_A180 copy.webp": b"a180",            # exact duplicate of a class file
        "mercedes-benz-glk-x204-suv.webp": b"glk",          # Mercedes, no entry
        "ChatGPT Image.webp": b"chatgpt",                   # unrelated
    }

    def setUp(self):
        self.case = Case(self.FILES)
        self.case.organize()
        self.out = self.case.output

    def tearDown(self):
        self.case.close()

    def run_pass(self, **kw):
        from mercedes_organizer.second_pass import run_second_pass
        return run_second_pass(self.case.categories, self.out, **kw)

    def test_unchanged_output_recovers_nothing(self):
        sp = self.run_pass()
        statuses = {r.file: r.status for r in sp.rows}
        self.assertNotIn("RECOVERED", statuses.values())
        self.assertEqual(statuses["Mercedes_A180 copy.webp"], "DUPLICATE")      # twin already in A-Class
        self.assertEqual(statuses["mercedes-benz-glk-x204-suv.webp"], "STILL UNMATCHED")
        self.assertEqual(statuses["ChatGPT Image.webp"], "STILL UNMATCHED")

    def test_files_added_to_leftover_folders_are_recovered(self):
        (self.out / "_Remaining" / "Not Matched" / "Mercedes_B200_W246.webp").write_bytes(b"new b200")
        (self.out / "_Unrelated Files" / "W177.webp").write_bytes(b"new w177")
        sp = self.run_pass()
        rows = {r.file: r for r in sp.rows}
        self.assertEqual((rows["Mercedes_B200_W246.webp"].status, rows["Mercedes_B200_W246.webp"].category),
                         ("RECOVERED", "B-Class"))
        self.assertEqual(rows["Mercedes_B200_W246.webp"].identifier, "B200 (model)")
        self.assertEqual((rows["W177.webp"].status, rows["W177.webp"].category), ("RECOVERED", "A-Class"))
        self.assertTrue((self.out / "B-Class" / "Mercedes_B200_W246.webp").is_file())
        self.assertTrue((self.out / "A-Class" / "W177.webp").is_file())
        self.assertEqual(sp.recovered_from("Remaining"), 1)
        self.assertEqual(sp.recovered_from("Unrelated"), 1)
        # a normal re-run keeps recovered files (they do not come from the source folder)
        self.case.organize()
        self.assertTrue((self.out / "B-Class" / "Mercedes_B200_W246.webp").is_file())
        # and a further second pass does not move anything back
        again = self.run_pass()
        self.assertNotIn("RECOVERED", {r.status for r in again.rows})

    def test_generic_number_tie_stays_ambiguous(self):
        (self.out / "_Duplicates" / "Mercedes_200.webp").write_bytes(b"generic")
        rows = {r.file: r for r in self.run_pass().rows}
        self.assertEqual(rows["Mercedes_200.webp"].status, "AMBIGUOUS")       # A 200 and B 200 tie
        self.assertTrue((self.out / "_Duplicates" / "Mercedes_200.webp").is_file())   # left in place

    def test_dry_run_moves_nothing(self):
        (self.out / "_Remaining" / "Not Matched" / "Mercedes_B200_W246.webp").write_bytes(b"new b200")
        sp = self.run_pass(dry_run=True)
        self.assertEqual(sp.recovered_from("Remaining"), 1)
        self.assertTrue((self.out / "_Remaining" / "Not Matched" / "Mercedes_B200_W246.webp").is_file())
        self.assertFalse((self.out / "B-Class" / "Mercedes_B200_W246.webp").exists())


if __name__ == "__main__":
    unittest.main()
