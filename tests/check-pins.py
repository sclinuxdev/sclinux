#!/usr/bin/env python3
"""Check that every pinned upstream commit still lives on its upstream branch.

Recipes pin third-party sources by commit. A commit that a force-push or a
rebase-merge rewrote away stays downloadable for a while and then stops: the
archive URL starts returning 404 and every build recorded against it becomes
unreproducible. That already happened once here -- Stage1 pinned a Sage commit
from a pull-request branch, the branch was rebased while the pull request was
being reviewed, and the pinned commit ended up reachable from no branch at all.

Nothing local can catch that, because the recipe still parses and the checksum
still matches whatever the cache holds. So this asks the forge whether the
pinned commit is an ancestor of the upstream default branch.

    python3 tests/check-pins.py                    # skips when the API is unreachable
    python3 tests/check-pins.py --require-network  # network failure is a failure (CI)
"""

import argparse
import http.client
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SKIP_DIRS = {".git", ".github", "pkg", "src", "distfiles", "out"}

# codeload and the /archive/ endpoint both name the commit in the last path
# segment; anything else (a release tarball, a version tag) is not a pin.
GITHUB_COMMIT = re.compile(
    r"https://(?:codeload\.github\.com|github\.com)/([^/]+)/([^/]+)/"
    r"(?:tar\.gz|archive(?:/refs/heads|/refs/tags)?)/([0-9a-f]{40})"
)

# per_page=1 keeps the response small: a diverged comparison otherwise carries
# the full commit list, and only the summary status is read here.
API = "https://api.github.com/repos/{owner}/{repo}/compare/{base}...{head}?per_page=1"

ATTEMPTS = 3


class Unreachable(Exception):
    """The forge could not be asked -- distinct from a failed answer."""


def recipes():
    for path in sorted(REPO.rglob("recipe.toml")):
        if SKIP_DIRS & set(path.parts):
            continue
        yield path


def pins():
    """(recipe, owner, repo, commit) for every GitHub commit pin in the tree."""
    for path in recipes():
        for owner, repo, commit in GITHUB_COMMIT.findall(path.read_text()):
            yield path, owner, repo.removesuffix(".git"), commit


def default_branch(owner: str, repo: str) -> str:
    return fetch(f"https://api.github.com/repos/{owner}/{repo}")["default_branch"]


def fetch(url: str) -> dict:
    request = urllib.request.Request(
        url, headers={"Accept": "application/vnd.github+json", "User-Agent": "sclinux-pin-check"}
    )
    last = ""
    for attempt in range(ATTEMPTS):
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as error:
            # 404 on the compare endpoint means the commit is not in this
            # repository at all; that is an answer, not an outage.
            if error.code == 404:
                return {"status": "missing"}
            raise Unreachable(f"{url}: HTTP {error.code}") from error
        except (urllib.error.URLError, http.client.HTTPException, OSError,
                json.JSONDecodeError) as error:
            # A truncated response or a dropped connection is an outage, not
            # an answer about the pin. Retry before giving up on it.
            last = repr(error)
            if attempt + 1 < ATTEMPTS:
                time.sleep(1 + attempt)
    raise Unreachable(f"{url}: {last}")


def ancestor_of_default(owner: str, repo: str, commit: str) -> tuple[bool, str]:
    """Is `commit` reachable from the upstream default branch?"""
    base = default_branch(owner, repo)
    # base...head is "behind" when head is an ancestor of base, "identical" when
    # they are the same commit. "ahead" and "diverged" both mean the pin sits on
    # a line the default branch never took.
    status = fetch(API.format(owner=owner, repo=repo, base=base, head=commit)).get("status")
    return status in {"behind", "identical"}, f"{status} relative to {base}"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--require-network",
        action="store_true",
        help="treat an unreachable forge as a failure instead of skipping",
    )
    args = parser.parse_args(argv)

    found = list(pins())
    if not found:
        print("no GitHub commit pins found")
        return 0

    failed = 0
    skipped = 0
    for path, owner, repo, commit in found:
        where = path.relative_to(REPO)
        try:
            reachable, detail = ancestor_of_default(owner, repo, commit)
        except Unreachable as error:
            skipped += 1
            print(f"SKIP  {where}: cannot reach the forge ({error})")
            continue
        if reachable:
            print(f"ok    {where} pins {owner}/{repo}@{commit[:7]} ({detail})")
        else:
            failed += 1
            print(
                f"FAIL  {where} pins {owner}/{repo}@{commit[:7]}\n"
                f"        {detail} -- the pinned commit is not on the upstream\n"
                f"        default branch, so its archive URL is not durable.\n"
                f"        Repin to a commit that branch contains."
            )

    print(f"\n{len(found) - failed - skipped} passed, {failed} failed, {skipped} skipped")
    if skipped and args.require_network:
        print("--require-network was given, so a skipped pin is a failure")
        return 1
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
