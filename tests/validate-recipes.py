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

# Directories `sage build` creates inside a recipe directory. A recipe.toml
# unpacked from a source tarball would otherwise be validated as if it were
# ours.
BUILD_ARTIFACT_DIRS = {"pkg", "src", "distfiles"}

MISSING_CHECKSUM = "[source] sha256 is required whenever url is set"

# Recipes still carrying a `sha256 = ""` placeholder. DISTRO_POLICY §5.1 makes
# the checksum mandatory -- without one sage downloads and builds whatever it
# is handed -- but the Stage1 tree was written with placeholders to be filled
# in later. Blocking CI on all of them would stop work; letting them pass
# silently is how the tree grew 102 of them unnoticed. So the count is pinned:
# it may fall, never rise. Lower this number as checksums land, and delete it
# once it reaches zero.
CHECKSUM_DEBT_BASELINE = 102

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
            errors.append(MISSING_CHECKSUM)

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


def discover_recipes() -> list[Path]:
    """Every recipe.toml in the tree, not just one hardcoded root.

    Enumerating a single directory is what let the Stage1 tree bypass this
    validator entirely while CI stayed green, so discovery is recursive: a
    recipe tree added later cannot go unchecked by default.
    """
    found = []
    for path in REPO.rglob("recipe.toml"):
        parts = path.relative_to(REPO).parts[:-1]
        if any(part in BUILD_ARTIFACT_DIRS or part.startswith(".") for part in parts):
            continue
        found.append(path)
    return sorted(found)


def main(argv: list[str] | None = None) -> int:
    strict = "--strict" in (argv if argv is not None else sys.argv[1:])

    recipes = discover_recipes()
    if not recipes:
        print("no recipe.toml found anywhere in the tree", file=sys.stderr)
        return 1

    failed = 0
    debt: list[Path] = []

    for path in recipes:
        rel = path.relative_to(REPO)
        errors = validate(path)

        if not strict and errors == [MISSING_CHECKSUM]:
            # Only the known placeholder, nothing else wrong with the recipe.
            debt.append(rel)
            continue

        if errors:
            failed += 1
            print(f"FAIL  {rel}")
            for err in errors:
                print(f"        {err}")

    checked = len(recipes)
    print(f"\n{checked - failed - len(debt)} passed, {failed} failed, "
          f"{len(debt)} awaiting a checksum  ({checked} recipes)")

    if debt:
        print(f"\nchecksum debt: {len(debt)} recipe(s) still have `sha256 = \"\"` "
              f"(baseline {CHECKSUM_DEBT_BASELINE})")
        if len(debt) > CHECKSUM_DEBT_BASELINE:
            print(f"FAIL  checksum debt grew past the baseline -- new recipes must "
                  f"ship a checksum")
            failed += 1
        elif len(debt) < CHECKSUM_DEBT_BASELINE:
            print(f"      down from {CHECKSUM_DEBT_BASELINE}; lower "
                  f"CHECKSUM_DEBT_BASELINE to {len(debt)} to lock the gain in")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
