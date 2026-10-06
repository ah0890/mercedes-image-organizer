"""Parse the Markdown page list into categories and pages.

Structure found in ``EF_Mercedes_Full_Page_List.md``::

    ## A-Class — 10 pages          <- category heading (declared page count)
    - A 180                        <- page: badge only
    - A 200 (W176)                 <- page: badge + chassis code
    - E 220 CDI / E 220 d (W211)   <- page: alternative badge names + code
    - CLK 200 Kompressor (C209/A209)
    - GLA 180 [[VERIFY CHASSIS CODE]]   <- chassis code unknown in source

Only ``##`` headings followed by bullet items are treated as categories, so the
document title, notes and horizontal rules are ignored.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .utils import get_logger, normalize_text

_HEADING = re.compile(r"^##\s+(?P<title>.+?)\s*$")
_DECLARED_COUNT = re.compile(r"^(?P<name>.+?)\s*[—–-]+\s*(?P<count>\d+)\s+pages?\b", re.I)
_BULLET = re.compile(r"^\s*[-*+]\s+(?P<text>.+?)\s*$")
_NUMBERED = re.compile(r"^\s*\d+[.)]\s+(?P<text>.+?)\s*$")
_CHASSIS_GROUP = re.compile(r"\(([^)]*)\)")
_VERIFY_FLAG = re.compile(r"\[\[\s*verify[^\]]*\]\]", re.I)
_CHASSIS_CODE = re.compile(r"^[a-z]{0,2}\d{3}[a-z]?$")
# "C 220 CDI" -> prefix "c", number "220", suffix "cdi"; "Viano 2.2 CDI" -> 2.2
_BADGE = re.compile(r"^(?P<prefix>[a-z][a-z ]*?)\s*(?P<number>\d+(?:\.\d+)?)\s*(?P<suffix>.*)$")


@dataclass(frozen=True)
class Badge:
    prefix: str        # "c", "amg gt", "sprinter"
    number: str        # "220", "63", "2.2"
    suffix: str        # "cdi", "amg", "kompressor", ""

    @property
    def prefix_last(self) -> str:
        """Last word of the prefix: 'amg gt' -> 'gt' (image badges say 'gt 63')."""
        return self.prefix.split()[-1] if self.prefix else ""


@dataclass
class Page:
    category: str
    raw: str                                  # exactly as written in the Markdown
    line_no: int
    badges: list[Badge] = field(default_factory=list)
    chassis_codes: frozenset[str] = frozenset()
    verify_flag: bool = False

    @property
    def is_amg(self) -> bool:
        return any("amg" in f"{b.prefix} {b.suffix}".split() for b in self.badges)

    @property
    def numbers(self) -> set[str]:
        return {b.number for b in self.badges}

    @property
    def prefixes(self) -> set[str]:
        return {b.prefix_last for b in self.badges}

    @property
    def identifiers(self) -> Identifiers:
        return page_identifiers(self)


@dataclass(frozen=True)
class Identifiers:
    """Searchable identifiers of one page, strongest first (all lower-case, compact)."""
    models: tuple[str, ...]      # "a200", "amggt63", "gt63"
    numbers: tuple[str, ...]     # "200", "2.2"
    chassis: tuple[str, ...]     # "w176", "c209", "a209"
    prefixes: frozenset[str]     # "a", "amggt", "gt" - letter part of the model identifiers

    def tiers(self) -> list[tuple[str, tuple[str, ...]]]:
        return [("model", self.models), ("number", self.numbers), ("chassis", self.chassis)]


def page_identifiers(page: "Page") -> Identifiers:
    models: list[str] = []
    numbers: list[str] = []
    prefixes: set[str] = set()
    for badge in page.badges:
        if not badge.number:
            continue
        compact_prefix = badge.prefix.replace(" ", "")
        for prefix in dict.fromkeys([compact_prefix, badge.prefix_last]):
            prefixes.add(prefix)
            models.append(prefix + badge.number)
        numbers.append(badge.number)
    return Identifiers(
        tuple(dict.fromkeys(models)), tuple(dict.fromkeys(numbers)),
        tuple(sorted(page.chassis_codes)), frozenset(prefixes),
    )


@dataclass
class Category:
    name: str
    line_no: int
    declared_count: int | None
    pages: list[Page] = field(default_factory=list)

    @property
    def coded_chassis(self) -> frozenset[str]:
        """All chassis codes named explicitly by pages in this category."""
        codes: set[str] = set()
        for page in self.pages:
            codes |= page.chassis_codes
        return frozenset(codes)

    @property
    def has_unverified_pages(self) -> bool:
        return any(p.verify_flag for p in self.pages)


def parse_page(category: str, text: str, line_no: int) -> Page:
    verify = bool(_VERIFY_FLAG.search(text))
    body = _VERIFY_FLAG.sub("", text)

    codes: set[str] = set()
    for group in _CHASSIS_GROUP.findall(body):
        for token in re.split(r"[/,\s]+", normalize_text(group)):
            if _CHASSIS_CODE.match(token):
                codes.add(token)
    body = _CHASSIS_GROUP.sub("", body)

    badges = []
    for alternative in body.split("/"):
        alt = normalize_text(alternative)
        if not alt:
            continue
        match = _BADGE.match(alt)
        if match:
            badges.append(Badge(match["prefix"].strip(), match["number"], match["suffix"].strip()))
        else:
            badges.append(Badge(alt, "", ""))

    return Page(category, text.strip(), line_no, badges, frozenset(codes), verify)


def parse_markdown(path: Path) -> list[Category]:
    log = get_logger()
    log.info("Parsing Markdown page list: %s", path)
    lines = path.read_text(encoding="utf-8").splitlines()

    categories: list[Category] = []
    current: Category | None = None

    for index, line in enumerate(lines, start=1):
        heading = _HEADING.match(line)
        if heading:
            title = heading["title"].strip()
            declared = _DECLARED_COUNT.match(title)
            name = declared["name"].strip() if declared else title
            count = int(declared["count"]) if declared else None
            current = Category(name, index, count)
            categories.append(current)
            continue
        if line.startswith("#"):          # any other heading level ends a category
            current = None
            continue
        if current is None:
            continue
        item = _BULLET.match(line) or _NUMBERED.match(line)
        if item:
            current.pages.append(parse_page(current.name, item["text"], index))

    # Headings without any page bullets are not categories (e.g. notes sections).
    categories = [c for c in categories if c.pages]

    for cat in categories:
        log.info("Category '%s': %d pages (declared %s)", cat.name, len(cat.pages), cat.declared_count)
        if cat.declared_count is not None and cat.declared_count != len(cat.pages):
            log.warning(
                "Category '%s' declares %d pages but %d were listed",
                cat.name, cat.declared_count, len(cat.pages),
            )
        for page in cat.pages:
            log.debug(
                "  page L%d %r -> badges=%s codes=%s verify=%s",
                page.line_no, page.raw,
                [f"{b.prefix}|{b.number}|{b.suffix}" for b in page.badges],
                sorted(page.chassis_codes), page.verify_flag,
            )
    return categories
