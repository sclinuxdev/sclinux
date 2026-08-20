#!/usr/bin/env python3
"""Validate every packages/*/recipe.toml against what sage actually parses.

Mirrors sage::package::Recipe::parse_toml upstream. The important subtlety is
scope: sage reads the dependency and build-phase arrays from THREE places and
merges them -- the top level, [package], and [source] -- while the scalar
metadata comes only from [package] and the source fields only from [source].
Validating just one scope both rejects valid recipes and misses typos in the
others. See docs/SAGE_DESIGN.md §4.

    python3 tests/validate-recipes.py
"""

import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Arrays sage merges across all three scopes.
MERGED_LISTS = ("dependencies", "build_dependencies", "provides")
MERGED_PHASES = ("prepare", "build", "install")
MERGED = MERGED_LISTS + MERGED_PHASES

# Scalars read only from [package].
PACKAGE_STRINGS = ("name", "version", "release", "description", "license", "channel", "arch")
# Scalars read only from [source].
SOURCE_STRINGS = ("url", "sha256")

# Keys sage reads in each scope. Anything else is silently ignored by the
# parser, which makes a typo look like it works.
ALLOWED = {
    "": {"schema_version", "package", "source", *MERGED},
    "package": {*PACKAGE_STRINGS, *MERGED},
    "source": {*SOURCE_STRINGS, *MERGED},
}


def scope_label(scope: str) -> str:
    return "top level" if scope == "" else f"[{scope}]"


def check_scope(table: dict, scope: str, errors: list[str]) -> None:
    """Type-check the merged arrays and flag unknown keys within one scope."""
    for field in MERGED:
        if field in table:
            value = table[field]
            if not isinstance(value, list):
                errors.append(f"{scope_label(scope)} {field} must be an array")
            elif not all(isinstance(x, str) for x in value):
                errors.append(f"{scope_label(scope)} {field} must contain only strings")

    for key in sorted(set(table) - ALLOWED[scope]):
        errors.append(f"{scope_label(scope)} unknown key {key!r} -- sage ignores it, likely a typo")


def require_string(table: dict, field: str, scope: str, errors: list[str]) -> None:
    """sage reads these with a typed accessor plus a default.

    A non-string (`release = 1`, `url = 123`) does not raise: the typed read
    misses and the default is substituted, so the recipe builds with values the
    author never wrote -- a wrong archive name, or a skipped source fetch.
    """
    if field in table and not isinstance(table[field], str):
        got = type(table[field]).__name__
        errors.append(
            f"{scope_label(scope)} {field} must be a quoted string, got {got} "
            f"-- sage would silently fall back to its default"
        )


def validate(path: Path) -> list[str]:
    errors: list[str] = []
    try:
        data = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as exc:
        return [f"not valid TOML: {exc}"]

    schema_version = data.get("schema_version")
    if type(schema_version) is not int or schema_version != 1:
        errors.append("schema_version must be 1")

    pkg = data.get("package")
    if not isinstance(pkg, dict):
        return errors + ["missing [package] section (sage rejects the recipe outright)"]

    for field in ("name", "version"):
        if not pkg.get(field):
            errors.append(f"[package] {field} is required")
    for field in PACKAGE_STRINGS:
        require_string(pkg, field, "package", errors)

    src = data.get("source")
    if src is not None and not isinstance(src, dict):
        errors.append("[source] must be a table")
        src = None

    # Walk every scope sage merges from, so a typo or a mistyped array is
    # caught wherever the author put it.
    scopes = [(data, ""), (pkg, "package")]
    if src is not None:
        scopes.append((src, "source"))
    for table, scope in scopes:
        check_scope(table, scope, errors)

    if src is not None:
        for field in SOURCE_STRINGS:
            require_string(src, field, "source", errors)
        if not src.get("url"):
            errors.append("[source] present but url is empty")
        # Policy: docs/DISTRO_POLICY.md §5.1 -- sage skips verification entirely
        # when sha256 is absent, which makes the download unverified.
        if not src.get("sha256"):
            errors.append("[source] sha256 is required whenever url is set")

    # Emptiness is judged against the merged view, exactly as sage builds it.
    merged_phases = [
        cmd
        for table, _ in scopes
        for field in MERGED_PHASES
        if isinstance(table.get(field), list)
        for cmd in table[field]
    ]
    merged_deps = [
        dep
        for table, _ in scopes
        if isinstance(table.get("dependencies"), list)
        for dep in table["dependencies"]
    ]
    # A package with no phases is legitimate only as a meta-package: no files,
    # just a dependency list (this is what `base` will be).
    if not merged_phases and not merged_deps:
        errors.append(
            "no prepare/build/install commands and no dependencies "
            "-- the package would ship nothing and pull in nothing"
        )

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
