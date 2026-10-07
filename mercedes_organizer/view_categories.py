"""Image-view categories recognised in file names (second-level sort inside class folders).

Each category is defined once by its words; the folder name and the matching
pattern are derived from them. Separators between the words may be hyphens,
underscores or spaces, in any case:

    words ("45", "angle", "front", "view")
      -> folder  "45 Angle Front View"
      -> matches 45-angle-front-view, 45_Angle_Front_View, 45 angle front view, ...

To support another view later, add a ViewCategory below (or switch `enabled`
on) - nothing else needs to change. More specific categories must come first:
"45-angle-front-view" also ends in "front-view", and the first match wins.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_SEP = r"[\s_\-]*"


@dataclass(frozen=True)
class ViewCategory:
    words: tuple[str, ...]
    enabled: bool = False
    # words that must NOT directly precede the match (keeps "front-view" from
    # matching inside "45-angle-front-view")
    not_after: tuple[str, ...] = ()

    @property
    def folder(self) -> str:
        return " ".join(w if w.isdigit() else w.capitalize() for w in self.words)

    @property
    def pattern(self) -> re.Pattern:
        core = _SEP.join(re.escape(w) for w in self.words)
        guard = "".join(rf"(?<!{re.escape(w)}[\s_\-])(?<!{re.escape(w)})" for w in self.not_after)
        return re.compile(rf"(?<![a-z0-9]){guard}{core}(?![a-z0-9])", re.I)


# Order matters: most specific first.
VIEW_CATEGORIES: list[ViewCategory] = [
    ViewCategory(("45", "angle", "front", "view"), enabled=True),
    # Present in the current file names, but not requested yet - switch on when needed:
    ViewCategory(("flat", "side", "view")),
    ViewCategory(("front", "view"), not_after=("angle",)),
    ViewCategory(("back", "view")),
    # Possible future categories:
    ViewCategory(("rear", "view")),
    ViewCategory(("side", "view"), not_after=("flat",)),
    ViewCategory(("interior",)),
    ViewCategory(("dashboard",)),
]


def enabled_categories(extra: list[str] | None = None) -> list[ViewCategory]:
    """Enabled categories, plus any extra folder names switched on from the command line."""
    wanted = {e.lower() for e in (extra or [])}
    return [c for c in VIEW_CATEGORIES if c.enabled or c.folder.lower() in wanted]


def detect(filename: str, categories: list[ViewCategory]) -> tuple[ViewCategory, str] | None:
    """First category whose pattern occurs anywhere in the file name, with the matched text."""
    for cat in categories:
        m = cat.pattern.search(filename)
        if m:
            return cat, m.group(0)
    return None
