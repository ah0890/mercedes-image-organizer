"""Identifier-based matching of Markdown entries to WebP files.

For every entry the parser extracts identifiers, strongest first::

    A 200 (W176)  ->  model "a200"   ->  number "200"   ->  chassis "w176"

The whole entry text never has to equal a filename. Each tier is searched in
the (normalised) filenames, and the first tier that produces a match wins:

    1. model    'A200'  (also 'A 200', 'A-200', 'A_200')
    2. number   '200'   (as a standalone number - never part of '2005' or 'B200')
    3. chassis  'W176'  (also 'W 176'; 'W463' does not match 'W463A')

A filename only has to contain ONE identifier. Context keeps generic numbers
and shared chassis codes from landing in the wrong class:

* Scope - only files that name this category ('a-class', 'gla', ...) or that
  name no category at all are searched.
* Conflicts - a file is rejected for an entry when it carries a *different*
  identifier of the same kind: another chassis ('a 200 turbo' from the W169 is
  not 'A 200 (W176)'), another model number ('c63' is not 'C 200'), or an AMG
  model (2-digit badge such as 'gla45') for a non-AMG entry.
* Claims - if entries of different categories claim the same file, the
  strongest identifier tier wins; on a tie the class the filename names first
  wins; if the file names neither, nobody gets it and the entries are
  AMBIGUOUS. One generic '200' image is never copied into every class.

Several different images matching one entry are all kept (e.g. a standard and
a facelift W204 for 'C 200 (W204)'); exact duplicates are collapsed by SHA-256.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

from .catalog import Catalog, SourceFile, spaced
from .parser import Category, Page
from .utils import get_logger, normalize_name

FOUND = "FOUND"
MISSING = "MISSING"
AMBIGUOUS = "AMBIGUOUS"

TIER_RANK = {"manual": 0, "model": 1, "number": 2, "chassis": 3}


@dataclass
class Hit:
    file: SourceFile
    tier: str
    identifier: str


@dataclass
class MatchResult:
    category: str
    page: Page
    status: str = MISSING
    tier: str = ""                                    # tier that produced the matches
    hits: list[Hit] = field(default_factory=list)     # every file the identifiers matched
    assigned: list[Hit] = field(default_factory=list) # files copied into this category
    contested: list[Hit] = field(default_factory=list)
    rejected: list[tuple[SourceFile, str]] = field(default_factory=list)
    notes: str = ""

    @property
    def match_type(self) -> str:
        ids = sorted({h.identifier.upper() for h in (self.assigned or self.hits)})
        return f"{' / '.join(ids)} ({self.tier})" if ids else ""


def _model_re(identifier: str) -> re.Pattern:
    return re.compile(r"(?<![a-z0-9.])" + re.escape(identifier) + r"(?![0-9])")


def _number_re(number: str) -> re.Pattern:
    return re.compile(r"(?<![a-z0-9.])" + re.escape(number) + r"(?![0-9]|\.\d)")


def _chassis_re(code: str) -> re.Pattern:
    return re.compile(r"(?<![a-z0-9])" + re.escape(code) + r"(?![a-z0-9])")


_PATTERN = {"model": _model_re, "number": _number_re, "chassis": _chassis_re}


def conflict(page: Page, f: SourceFile) -> str:
    """Why this file cannot depict this entry, or '' if it can."""
    ids = page.identifiers
    page_chassis = set(ids.chassis)
    if page_chassis:
        file_chassis = f.chassis_tokens(ids.prefixes)
        if file_chassis and not (file_chassis & page_chassis):
            return f"different chassis ({', '.join(sorted(c.upper() for c in file_chassis))})"
    for token in f.tokens:
        for prefix in ids.prefixes:
            m = re.fullmatch(re.escape(prefix) + r"(\d{2,3})[a-z]*", token)
            if m and m.group(1) not in ids.numbers and token not in page_chassis:
                return f"different model ({token.upper()})"
    if f.is_amg and not page.is_amg:
        return "AMG model image for a non-AMG entry"
    return ""


class Matcher:
    def __init__(self, catalog: Catalog, categories: list[Category]):
        self.catalog = catalog
        self.categories = categories
        self.log = get_logger()
        self.owner: dict[str, str | None] = {}

    def _scope(self, category: str) -> list[SourceFile]:
        # Files that name no class at all ("A200.webp", "Mercedes_W246.webp") are open to every class;
        # a file naming a class that is not in the Markdown ("clc-class") is open to none.
        return [f for f in self.catalog.webp
                if category in f.contexts or (not f.contexts and not f.foreign_class)]

    def search(self, category: str, page: Page) -> MatchResult:
        result = MatchResult(category, page)
        scope = self._scope(category)
        for tier, identifiers in page.identifiers.tiers():
            if not identifiers:
                continue
            patterns = [(i, _PATTERN[tier](i)) for i in identifiers]
            hits, rejected = [], []
            for f in scope:
                # Numbers are searched with separators kept ("mercedes 200"), so a bare
                # number is not swallowed by the word before it ("mercedes200").
                text = f.spaced if tier == "number" else f.compact
                identifier = next((i for i, p in patterns if p.search(text)), None)
                if identifier is None:
                    continue
                reason = conflict(page, f)
                if reason:
                    rejected.append((f, f"{identifier.upper()} found but {reason}"))
                else:
                    hits.append(Hit(f, tier, identifier))
            result.rejected.extend(rejected)
            if hits:
                result.tier, result.hits = tier, hits
                break
        return result

    def match_all(self, overrides: dict[tuple[str, str], str] | None = None
                  ) -> tuple[dict[str, list[MatchResult]], list[str]]:
        results = {c.name: [self.search(c.name, p) for p in c.pages] for c in self.categories}
        problems = self._apply_overrides(results, overrides or {})
        self._resolve_claims(results)
        for rows in results.values():
            for r in rows:
                self._finalise(r)
        return results, problems

    # ----------------------------------------------------------------- claims

    def _resolve_claims(self, results: dict[str, list[MatchResult]]) -> None:
        """Give each file to the category with the strongest claim; ties -> nobody."""
        best: dict[str, dict[str, int]] = {}      # file -> category -> best rank
        for category, rows in results.items():
            for r in rows:
                for h in r.hits:
                    ranks = best.setdefault(h.file.rel_path, {})
                    ranks[category] = min(ranks.get(category, 99), TIER_RANK[h.tier])
        files = {f.rel_path: f for f in self.catalog.webp}
        for rel, ranks in best.items():
            top = min(ranks.values())
            winners = [c for c, rank in ranks.items() if rank == top]
            primary = files[rel].primary_context
            if len(winners) > 1 and primary in winners:
                # Equal identifier strength: the class the filename names first decides.
                winners = [primary]
            self.owner[rel] = winners[0] if len(winners) == 1 else None
            if len(ranks) > 1:
                self.log.warning("File claimed by %d categories %s -> %s: %s", len(ranks), ranks,
                                 winners[0] if len(winners) == 1 else "AMBIGUOUS", rel)
        for category, rows in results.items():
            for r in rows:
                r.assigned = [h for h in r.hits if self.owner.get(h.file.rel_path) == category]
                r.contested = [h for h in r.hits if self.owner.get(h.file.rel_path) != category]

    def _finalise(self, r: MatchResult) -> None:
        ids = r.page.identifiers
        searched = ", ".join(i.upper() for _, group in ids.tiers() for i in group) or "(none)"
        if r.assigned:
            r.status = FOUND
            if r.contested:
                r.notes = _join(r.notes, f"{len(r.contested)} other matching file(s) went to another category")
            if r.page.is_amg and r.tier == "chassis" and not any(h.file.is_amg for h in r.assigned):
                r.notes = _join(r.notes, "Chassis-only match: the image shows this generation but not the AMG model")
            if r.page.verify_flag:
                r.notes = _join(r.notes, "Markdown flags this entry [[VERIFY CHASSIS CODE]] - confirm the generation")
            self.log.info("FOUND     [%s] %s via %s -> %d file(s)", r.category, r.page.raw,
                          r.match_type, len(r.assigned))
        elif r.hits:
            r.status = AMBIGUOUS
            others = sorted({self.owner.get(h.file.rel_path) or "tie" for h in r.contested})
            r.notes = _join(r.notes, "Identifier matched, but the image(s) are claimed equally or more strongly "
                                     f"by another category ({', '.join(others)}) - cannot safely decide")
            self.log.warning("AMBIGUOUS [%s] %s via %s | %s", r.category, r.page.raw, r.match_type, r.notes)
        else:
            r.status = MISSING
            if r.rejected:
                reasons = sorted({why for _, why in r.rejected})
                note = (f"No usable WebP file for identifiers ({searched}); {len(r.rejected)} file(s) contained "
                        f"an identifier but were rejected by context: {'; '.join(reasons[:4])}")
            else:
                note = f"None of the identifiers ({searched}) found in any WebP filename"
            if r.page.verify_flag:
                note += "; Markdown flags this entry [[VERIFY CHASSIS CODE]]"
            r.notes = _join(r.notes, note)
            self.log.warning("MISSING   [%s] %s | %s", r.category, r.page.raw, r.notes)

    # ---------------------------------------------------------------- overrides

    def _apply_overrides(self, results, overrides) -> list[str]:
        problems, used = [], set()
        names = [(spaced(f.name), f) for f in self.catalog.webp]
        for category, rows in results.items():
            for r in rows:
                key = (normalize_name(category), normalize_name(r.page.raw))
                if key not in overrides:
                    continue
                used.add(key)
                value = overrides[key]
                if value.upper() == MISSING:
                    r.hits, r.tier, r.notes = [], "", "Marked MISSING in overrides file"
                    continue
                wanted = spaced(value)
                files = [f for n, f in names if n == wanted or n.startswith(wanted + " ")]
                if not files:
                    problems.append(f"Override [{category}] {r.page.raw}: no file named '{value}'")
                    continue
                r.hits = [Hit(f, "manual", "override") for f in files]
                r.tier, r.notes = "manual", "Assigned manually via overrides file"
        for key in overrides.keys() - used:
            problems.append(f"Override row matches no Markdown entry: {key}")
        for p in problems:
            self.log.error(p)
        return problems


def _join(*parts: str) -> str:
    return "; ".join(p for p in parts if p)


def load_overrides(path: Path) -> dict[tuple[str, str], str]:
    """CSV columns Category, Page, Image. Image = a filename, or the name shared
    by all views of one render (the filename without '-front-view' etc.), or MISSING."""
    overrides: dict[tuple[str, str], str] = {}
    with open(path, newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            row = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
            if row.get("category") and row.get("page") and row.get("image"):
                overrides[(normalize_name(row["category"]), normalize_name(row["page"]))] = row["image"]
    return overrides
