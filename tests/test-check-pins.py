#!/usr/bin/env python3
"""Regression tests for the upstream pin checker.

The forge is stubbed: these assert how each comparison status is classified and
that an outage stays distinguishable from an answer, without a network call.
"""

import importlib.util
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("check_pins", HERE / "check-pins.py")
checker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(checker)


def with_status(status: str):
    """Stub fetch() so the repository lookup and the comparison both answer."""

    def fetch(url: str) -> dict:
        if "/compare/" in url:
            return {"status": status}
        return {"default_branch": "main"}

    return fetch


def outage(url: str) -> dict:
    raise checker.Unreachable(f"{url}: stubbed outage")


def reachable_for(status: str) -> bool:
    original = checker.fetch
    checker.fetch = with_status(status)
    try:
        result, _ = checker.ancestor_of_default("owner", "repo", "0" * 40)
        return result
    finally:
        checker.fetch = original


# Counted rather than hand-maintained: a constant here drifts the moment a
# case is added or removed, and then the summary stops meaning anything.
checks_run = 0


def check(description: str, actual: object, expected: object) -> bool:
    global checks_run
    checks_run += 1
    if actual == expected:
        print(f"ok    {description}")
        return True
    print(f"FAIL  {description}\n        expected {expected!r}, got {actual!r}")
    return False


def main() -> int:
    failed = 0

    # A pin is durable exactly when the default branch can reach it. "ahead" and
    # "diverged" both describe a commit the branch never took -- the shape the
    # rewritten Sage pull-request commit had.
    for status, expected in (
        ("behind", True),
        ("identical", True),
        ("ahead", False),
        ("diverged", False),
        ("missing", False),
    ):
        failed += not check(
            f"comparison status {status!r} is {'accepted' if expected else 'rejected'}",
            reachable_for(status),
            expected,
        )

    # An outage must not read as a verdict either way.
    original = checker.fetch
    checker.fetch = outage
    try:
        raised = False
        try:
            checker.ancestor_of_default("owner", "repo", "0" * 40)
        except checker.Unreachable:
            raised = True
        failed += not check("an unreachable forge raises Unreachable", raised, True)
    finally:
        checker.fetch = original

    # Only a 40-character commit is a pin. Release tarballs and tag archives
    # name a version, which upstream is expected to keep serving.
    commit = "a" * 40
    failed += not check(
        "codeload commit archives are recognised as pins",
        checker.GITHUB_COMMIT.findall(
            f"url = \"https://codeload.github.com/owner/repo/tar.gz/{commit}\""
        ),
        [("owner", "repo", commit)],
    )
    failed += not check(
        "github archive commit URLs are recognised as pins",
        checker.GITHUB_COMMIT.findall(
            f"url = \"https://github.com/owner/repo/archive/{commit}.tar.gz\""
        ),
        [("owner", "repo", commit)],
    )
    failed += not check(
        "tag and release tarballs are not pins",
        checker.GITHUB_COMMIT.findall(
            "url = \"https://github.com/systemd/systemd/archive/refs/tags/v261.2.tar.gz\""
        ),
        [],
    )
    failed += not check(
        "a short hash is not treated as a commit pin",
        checker.GITHUB_COMMIT.findall(
            "url = \"https://codeload.github.com/owner/repo/tar.gz/abc1234\""
        ),
        [],
    )

    # Build products carry copies of recipes; walking them would double-report.
    failed += not check(
        "build product directories are skipped",
        {"pkg", "src", "distfiles", "out"} <= checker.SKIP_DIRS,
        True,
    )

    print(f"\n{checks_run - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
