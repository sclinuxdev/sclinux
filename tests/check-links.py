#!/usr/bin/env python3
"""Check that relative links and in-document anchors in Markdown resolve.

Catches two things that have actually broken here before: links to files that
do not exist, and table-of-contents anchors left behind when a heading was
renamed.

    python3 tests/check-links.py
"""

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", ".github", "node_modules"}

LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
HEADING = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*$", re.MULTILINE)
FENCE = re.compile(r"^```.*?^```", re.MULTILINE | re.DOTALL)


def slug(heading: str) -> str:
    """Approximate GitHub's heading -> anchor transformation.

    Non-word characters (punctuation, emoji) are dropped; each remaining space
    becomes a hyphen. Spaces are replaced individually rather than collapsed,
    which is why "A & B" yields "a--b".
    """
    s = heading.strip().lower()
    s = re.sub(r"[^\w\s-]", "", s, flags=re.UNICODE)
    return s.replace(" ", "-")


def anchors_of(text: str) -> set[str]:
    # Headings inside fenced code blocks are not headings.
    anchors: set[str] = set()
    for heading in HEADING.findall(FENCE.sub("", text)):
        base = slug(heading)
        anchor = base
        suffix = 0
        while anchor in anchors:
            suffix += 1
            anchor = f"{base}-{suffix}"
        anchors.add(anchor)
    return anchors


def links_of(text: str) -> list[str]:
    # Markdown-looking examples inside fenced code blocks are not links.
    return LINK.findall(FENCE.sub("", text))


def main() -> int:
    files = [
        p for p in REPO.rglob("*.md")
        if not any(part in SKIP_DIRS for part in p.relative_to(REPO).parts)
    ]

    problems = 0
    for path in sorted(files):
        rel = path.relative_to(REPO)
        text = path.read_text()
        own_anchors = anchors_of(text)

        for target in links_of(text):
            if target.startswith(("http://", "https://", "mailto:")):
                continue

            file_part, _, anchor = target.partition("#")

            if not file_part:  # in-document anchor
                if anchor and anchor not in own_anchors:
                    print(f"FAIL  {rel}: anchor #{anchor} has no matching heading")
                    problems += 1
                continue

            dest = (path.parent / file_part).resolve()
            if not dest.exists():
                print(f"FAIL  {rel}: {file_part} does not exist")
                problems += 1
                continue

            if anchor and dest.suffix == ".md":
                if anchor not in anchors_of(dest.read_text()):
                    print(f"FAIL  {rel}: {file_part}#{anchor} -- no such heading in target")
                    problems += 1

    print(f"\nchecked {len(files)} markdown files, {problems} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
