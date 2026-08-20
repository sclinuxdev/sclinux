#!/usr/bin/env python3
"""Validate every packages/*/recipe.toml against what sage actually parses.

Field names and types mirror sage::package::Recipe::parse_toml upstream.
See docs/SAGE_DESIGN.md §4 for the documented schema.

    python3 tests/validate-recipes.py
"""

import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Keys sage reads out of [package]. Anything else is silently ignored by the
# parser, so an unknown key is almost always a typo rather than an extension.
KNOWN_PACKAGE_KEYS = {
    "name", "version", "release", "description", "license", "channel", "arch",
    "dependencies", "build_dependencies", "provides",
    "prepare", "build", "install",
}
STRING_FIELDS = ("name", "version", "release", "description", "license", "channel", "arch")
LIST_FIELDS = ("dependencies", "build_dependencies", "provides", "prepare", "build", "install")


def validate(path: Path) -> list[str]:
    errors: list[str] = []
    try:
        data = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as exc:
        return [f"not valid TOML: {exc}"]

    if data.get("schema_version") != 1:
        errors.append("schema_version must be 1")

    pkg = data.get("package")
    if not isinstance(pkg, dict):
        return errors + ["missing [package] section (sage rejects the recipe outright)"]

    for field in ("name", "version"):
        if not pkg.get(field):
            errors.append(f"[package] {field} is required")

    for field in STRING_FIELDS:
        if field in pkg and not isinstance(pkg[field], str):
            # `release = 1` parses as an integer and sage's value_or("1") then
            # falls back to the default, silently changing the archive name.
            errors.append(f"[package] {field} must be a quoted string, got {type(pkg[field]).__name__}")

    for field in LIST_FIELDS:
        if field in pkg:
            if not isinstance(pkg[field], list):
                errors.append(f"[package] {field} must be an array")
            elif not all(isinstance(x, str) for x in pkg[field]):
                errors.append(f"[package] {field} must contain only strings")

    for key in sorted(set(pkg) - KNOWN_PACKAGE_KEYS):
        errors.append(f"[package] unknown key {key!r} -- sage ignores it, likely a typo")

    src = data.get("source")
    if isinstance(src, dict):
        if not src.get("url"):
            errors.append("[source] present but url is empty")
        # Policy: docs/DISTRO_POLICY.md §5.1 -- an unverified download is an
        # unverified download, and sage skips the check when sha256 is absent.
        if not src.get("sha256"):
            errors.append("[source] sha256 is required whenever url is set")

    if not any(pkg.get(p) for p in ("prepare", "build", "install")):
        errors.append("no prepare/build/install commands -- the package would ship empty")

    return errors


def main() -> int:
    recipes = sorted((REPO / "packages").glob("*/recipe.toml"))
    if not recipes:
        print("no recipes found under packages/", file=sys.stderr)
        return 1

    failed = 0
    for path in recipes:
        rel = path.relative_to(REPO)
        errors = validate(path)
        if errors:
            failed += 1
            print(f"FAIL  {rel}")
            for err in errors:
                print(f"        {err}")
        else:
            print(f"ok    {rel}")

    print(f"\n{len(recipes) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
