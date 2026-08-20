#!/usr/bin/env python3
"""Regression tests for GitHub-style Markdown link handling."""

import importlib.util
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("check_links", HERE / "check-links.py")
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def main() -> int:
    cases = [
        (
            "duplicate headings receive numbered anchors",
            checker.anchors_of("# Foo\n# Foo\n# Foo\n"),
            {"foo", "foo-1", "foo-2"},
        ),
        (
            "numbering skips an anchor already claimed by a heading",
            checker.anchors_of("# Foo\n# Foo-1\n# Foo\n"),
            {"foo", "foo-1", "foo-2"},
        ),
        (
            "links inside fenced code blocks are ignored",
            checker.links_of(
                "```markdown\n[example](missing.md)\n```\n[real](README.md)\n"
            ),
            ["README.md"],
        ),
        (
            "links inside tilde-fenced code blocks are ignored",
            checker.links_of(
                "~~~markdown\n[example](missing.md)\n~~~\n[real](README.md)\n"
            ),
            ["README.md"],
        ),
        (
            "a backtick fence inside a tilde block does not close it early",
            checker.links_of(
                "~~~\n```\n[example](missing.md)\n```\n~~~\n[real](README.md)\n"
            ),
            ["README.md"],
        ),
        (
            "headings inside tilde-fenced blocks are not headings",
            checker.anchors_of("~~~\n# Fake\n~~~\n# Real\n"),
            {"real"},
        ),
        (
            "a shorter marker does not close a longer tilde fence",
            checker.links_of(
                "~~~~\n~~~\n[example](missing.md)\n~~~\n~~~~\n[real](README.md)\n"
            ),
            ["README.md"],
        ),
        (
            "headings stay hidden until the full-length fence closes",
            checker.anchors_of("~~~~\n~~~\n# Fake\n~~~\n~~~~\n# Real\n"),
            {"real"},
        ),
        (
            "trailing text prevents a line from closing the fence",
            checker.links_of(
                "```\n[first](one.md)\n```python\n[second](two.md)\n```\n[real](README.md)\n"
            ),
            ["README.md"],
        ),
        (
            "fences may be indented by up to three spaces",
            checker.links_of(
                "  ```\n[example](missing.md)\n   ```\n[real](README.md)\n"
            ),
            ["README.md"],
        ),
        (
            "an unclosed fence continues to end of file",
            checker.links_of("```\n[example](missing.md)\n"),
            [],
        ),
        (
            "a longer closing marker is accepted",
            checker.links_of(
                "```\n[example](missing.md)\n````\n[real](README.md)\n"
            ),
            ["README.md"],
        ),
        (
            "a backtick in the info string prevents a backtick fence opener",
            checker.links_of("```bad`info\n[real](README.md)\n"),
            ["README.md"],
        ),
    ]

    failed = 0
    for description, actual, expected in cases:
        if actual == expected:
            print(f"ok    {description}")
        else:
            failed += 1
            print(f"FAIL  {description}\n        expected {expected!r}, got {actual!r}")

    print(f"\n{len(cases) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
