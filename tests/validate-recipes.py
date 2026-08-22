#!/usr/bin/env python3
"""Validate every repository recipe.toml against what sage actually parses.

Mirrors sage::package::Recipe::parse_toml upstream. The important subtlety is
scope: sage reads the dependency and build-phase arrays from THREE places and
merges them -- the top level, [package], and [source] -- while the scalar
metadata comes only from [package] and the source fields only from [source].
Validating just one scope both rejects valid recipes and misses typos in the
others. See docs/SAGE_DESIGN.md §4.

    python3 tests/validate-recipes.py
"""

import re
import sys
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Directories `sage build` creates inside a recipe directory. A recipe.toml
# unpacked from a source tarball would otherwise be validated as if it were
# ours.
BUILD_ARTIFACT_DIRS = {"pkg", "src", "distfiles"}
GENERATED_TOP_LEVEL_DIRS = {"out"}

MISSING_CHECKSUM = "[source] sha256 is required whenever url is set"
INVALID_CHECKSUM = "[source] sha256 must be exactly 64 lowercase hexadecimal characters"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
PACKAGE_ARCHITECTURES = {"x86_64", "aarch64", "any"}

# Recipes grandfathered in with a `sha256 = ""` placeholder, pinned BY PATH.
#
# A count is not enough: adding one checksumless recipe while resolving one
# legacy placeholder leaves the total unchanged, which is exactly the case the
# pin exists to reject. Listing paths means a new unverified source can never
# take a resolved one's place, and a resolved entry must be removed from the
# file, so the debt cannot quietly grow back.
#
# DISTRO_POLICY §5.1 is unchanged -- the checksum is still required. This file
# only records which recipes predate the check.
CHECKSUM_DEBT_FILE = Path(__file__).resolve().parent / "checksum-debt.txt"

DEBT_HEADER = """\
# Recipes still carrying `sha256 = ""`, pinned by path.
#
# Adding a line here is not routine: it means shipping a source that sage will
# download and build without verifying. Fix the checksum instead.
#
# Remove resolved entries:  python3 tests/validate-recipes.py --update-debt
"""


def load_debt() -> set[str]:
    if not CHECKSUM_DEBT_FILE.exists():
        return set()
    return {
        line.strip()
        for line in CHECKSUM_DEBT_FILE.read_text().splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def write_debt(paths: set[str]) -> None:
    CHECKSUM_DEBT_FILE.write_text(DEBT_HEADER + "".join(f"{q}\n" for q in sorted(paths)))

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
    "": {"schema_version", "package", "source", "capability_hooks", *MERGED},
    "package": {*PACKAGE_STRINGS, "capability_hooks", *MERGED},
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


def check_capability_hooks(table: dict, scope: str, errors: list[str]) -> None:
    hooks = table.get("capability_hooks")
    if hooks is None:
        return
    if not isinstance(hooks, list):
        errors.append(f"{scope_label(scope)} capability_hooks must be an array of tables")
        return
    for index, hook in enumerate(hooks):
        label = f"{scope_label(scope)} capability_hooks[{index}]"
        if not isinstance(hook, dict):
            errors.append(f"{label} must be a table")
            continue
        unknown = sorted(set(hook) - {"capability", "exec", "args"})
        if unknown:
            errors.append(f"{label} unknown key(s): {', '.join(unknown)}")
        for field in ("capability", "exec"):
            if not isinstance(hook.get(field), str) or not hook[field]:
                errors.append(f"{label} {field} must be a non-empty string")
        args = hook.get("args", [])
        if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
            errors.append(f"{label} args must be an array of strings")


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
    if isinstance(pkg.get("arch"), str) and pkg["arch"] not in PACKAGE_ARCHITECTURES:
        errors.append(
            "[package] arch must be one of: " + ", ".join(sorted(PACKAGE_ARCHITECTURES))
        )

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
        if scope != "source":
            check_capability_hooks(table, scope, errors)

    if src is not None:
        for field in SOURCE_STRINGS:
            require_string(src, field, "source", errors)
        if not src.get("url"):
            errors.append("[source] present but url is empty")
        # Policy: docs/DISTRO_POLICY.md §5.1 -- sage skips verification entirely
        # when sha256 is absent, which makes the download unverified.
        checksum = src.get("sha256")
        if not isinstance(checksum, str) or not checksum:
            errors.append(MISSING_CHECKSUM)
        elif not SHA256.fullmatch(checksum):
            errors.append(INVALID_CHECKSUM)

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


def classify_debt(grandfathered: set[str], still_owed: set[str]) -> tuple[set[str], set[str]]:
    """Split checksum debt into what must fail and what must be de-listed.

    Returns (violations, resolved).

    Comparing totals instead of paths is not equivalent: adding one
    checksumless recipe while resolving one legacy placeholder leaves the count
    unchanged, so a count-based rule accepts exactly the change it exists to
    reject. Both halves are therefore set differences, and both are failures --
    a resolved entry left in the file would free a slot the debt could grow
    back into.
    """
    return still_owed - grandfathered, grandfathered - still_owed


def is_build_artifact(path: Path) -> bool:
    """True when this recipe.toml came out of a build rather than the tree.

    `sage build <dir>` creates <dir>/pkg, <dir>/src and <dir>/distfiles, so an
    unpacked source tarball can leave a recipe.toml under one of them. Matching
    on the directory name alone would be too broad: it would also skip a
    package legitimately named `src`, or an unrelated src/recipes/foo/. The
    directory only counts as an artifact when it sits beside a recipe.toml --
    that is, inside an actual recipe directory.
    """
    for ancestor in path.parents:
        if ancestor == REPO:
            break
        if (ancestor.name in BUILD_ARTIFACT_DIRS
                and (ancestor.parent / "recipe.toml").is_file()):
            return True
    return False


def discover_recipes() -> list[Path]:
    """Every recipe.toml in the tree, not just one hardcoded root.

    Enumerating a single directory is what let the Stage1 tree bypass this
    validator entirely while CI stayed green, so discovery is recursive: a
    recipe tree added later cannot go unchecked by default.
    """
    found = []
    for path in REPO.rglob("recipe.toml"):
        rel = path.relative_to(REPO)
        if rel.parts[0] in {".git", *GENERATED_TOP_LEVEL_DIRS} or is_build_artifact(path):
            continue
        found.append(path)
    return sorted(found)


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    unknown = sorted(set(args) - {"--strict", "--update-debt"})
    if unknown:
        print(f"unknown argument(s): {' '.join(unknown)}", file=sys.stderr)
        return 2

    strict = "--strict" in args
    update = "--update-debt" in args
    if strict and update:
        print("--strict and --update-debt are mutually exclusive", file=sys.stderr)
        return 2

    recipes = discover_recipes()
    if not recipes:
        print("no recipe.toml found anywhere in the tree", file=sys.stderr)
        return 1

    grandfathered = load_debt()
    invalid: set[str] = set()
    still_owed: set[str] = set()

    for path in recipes:
        rel = path.relative_to(REPO).as_posix()
        errors = validate(path)

        if MISSING_CHECKSUM in errors:
            still_owed.add(rel)
            if not strict:
                errors = [error for error in errors if error != MISSING_CHECKSUM]

        if errors:
            invalid.add(rel)
            print(f"FAIL  {rel}")
            for err in errors:
                print(f"        {err}")

    violations, resolved = classify_debt(grandfathered, still_owed)

    if violations:
        print(f"FAIL  {len(violations)} recipe(s) ship a source with no checksum and are not grandfathered:")
        for rel in sorted(violations):
            print(f"        {rel}")
        print("      a newly added source must ship a checksum -- do not add it to "
              f"{CHECKSUM_DEBT_FILE.name}")

    failed_paths = invalid | violations

    if update:
        if failed_paths:
            print("FAIL  refusing to update checksum debt while recipe errors or new debt exist")
            return 1
        updated = grandfathered & still_owed
        write_debt(updated)
        print(f"wrote {CHECKSUM_DEBT_FILE.relative_to(REPO)}: "
              f"{len(updated)} recipe(s) awaiting a checksum")
        return 0

    waiting = (still_owed & grandfathered) - invalid
    clean = len(recipes) - len(failed_paths) - len(waiting)
    print(f"\n{clean} passed, {len(failed_paths)} failed, {len(waiting)} awaiting a checksum  "
          f"({len(recipes)} recipes)")

    if resolved:
        print(f"\nFAIL  {len(resolved)} entry(ies) in {CHECKSUM_DEBT_FILE.name} no longer owe a checksum:")
        for rel in sorted(resolved):
            print(f"        {rel}")
        print("      remove them, so the debt cannot grow back into the freed slots:")
        print("        python3 tests/validate-recipes.py --update-debt")

    return 1 if failed_paths or resolved else 0


if __name__ == "__main__":
    sys.exit(main())
