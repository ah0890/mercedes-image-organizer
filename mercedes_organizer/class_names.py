"""Recognise a Markdown class name inside a file name, in any spelling.

Patterns are built from the class names in the Markdown - nothing is hard-coded.
Every pattern is case-insensitive and tolerant of separators:

    "A-Class"  ->  A-Class, a class, A_CLASS, aclass, A-Dash Class, a-dash-class, A DASH CLASS ...
    "GLE"      ->  GLE, gle, G-L-E, G L E, G, L, E, g.l.e, G-dash-L-dash-E ...   (3+ letter names)
    "AMG GT"   ->  AMG GT, amg-gt, AMG_GT, amggt, AMG-Dash GT ...
    "SL", "CL" ->  the word itself only (two letters are too short to spell out)

Which class a file belongs to:

1. The class name at the START of the name (after an optional "Mercedes-Benz"
   prefix) is the file's own class - "mercedes-benz-slc-r170 slk ..." is SLC
   even though it also mentions SLK.
2. If the name starts with the brand prefix but the first part is not a
   Markdown class ("sls amg", "glk", "clc-class", "vario"), the file belongs to
   that other family and is left alone - a later mention ("sls amg gt") does
   not count.
3. A free-form name without the brand pattern ("Photo of my a-dash-class.webp")
   is assigned when exactly one Markdown class is mentioned anywhere.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .utils import strip_accents

_SEP = r"[\s_.,\-]*"
_DASH = r"(?:" + _SEP + r"dash" + _SEP + r")?"
_BRAND = re.compile(r"^\s*mercedes(?:[\s_.,\-]*benz)?[\s_.,\-]+", re.I)


def _norm(text: str) -> str:
    text = strip_accents(text).lower().replace("–", "-").replace("—", "-")
    return re.sub(r"\.webp$", "", text.strip())


def class_pattern(class_name: str) -> re.Pattern:
    words = re.split(r"[\s_\-]+", _norm(class_name))
    words = [w for w in words if w]
    if len(words) == 2 and words[1] == "class":
        core = re.escape(words[0]) + _SEP + _DASH + _SEP + "class"
    elif len(words) == 1 and words[0].isalpha() and len(words[0]) >= 3 and len(words[0]) <= 4:
        # letters may be spelled out: G-L-E, G L E, G, L, E, g-dash-l-dash-e
        core = (_SEP + _DASH + _SEP).join(re.escape(ch) for ch in words[0])
    else:
        core = (_SEP + _DASH + _SEP).join(re.escape(w) for w in words)
    return re.compile(r"(?<![a-z0-9])" + core + r"(?![a-z0-9])")


@dataclass
class ClassNameMatch:
    category: str | None
    variant: str          # the text found in the file name
    how: str              # "start of name" / "named in free-form file name" / reason for no match


class ClassNameIndex:
    def __init__(self, class_names: list[str]):
        self.patterns = {name: class_pattern(name) for name in class_names}

    def classify(self, filename: str) -> ClassNameMatch:
        name = _norm(filename)
        branded = bool(_BRAND.match(name))
        rest = _BRAND.sub("", name, count=1)
        leading = [(m.end(), cat, m.group(0)) for cat, p in self.patterns.items() if (m := p.match(rest))]
        if leading:
            _, cat, text = max(leading)                 # longest class name at the start wins
            return ClassNameMatch(cat, text, "class name at the start of the file name")
        if branded:
            fam = re.match(r"[a-z ]+?(?:-class\b|(?=-)|$)", rest)
            family = (fam.group(0) if fam else rest[:20]).strip()
            return ClassNameMatch(None, "", f"file name starts with '{family}', which is not a class in the page list")
        anywhere = {cat: m.group(0) for cat, p in self.patterns.items() if (m := p.search(rest))}
        if len(anywhere) == 1:
            cat, text = next(iter(anywhere.items()))
            return ClassNameMatch(cat, text, "only class named in the file name")
        if len(anywhere) > 1:
            return ClassNameMatch(None, "", "several classes named (" + ", ".join(sorted(anywhere)) + ")")
        return ClassNameMatch(None, "", "no class name from the page list in the file name")
