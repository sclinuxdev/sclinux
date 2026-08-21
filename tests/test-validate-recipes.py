#!/usr/bin/env python3
"""Fixture tests for tests/validate-recipes.py.

The validator's first version checked only the [package] scope while sage
merges three, so it rejected valid recipes and missed typos elsewhere. These
fixtures pin the behaviour that was wrong, plus the meta-package case that
`base` will depend on.

    python3 tests/test-validate-recipes.py
"""

import contextlib
import importlib.util
import io
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("validate_recipes", HERE / "validate-recipes.py")
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)

MINIMAL = 'schema_version = 1\n[package]\nname = "x"\nversion = "1.0"\n'
VALID_CHECKSUM = '"' + "a" * 64 + '"'

CASES: list[tuple[str, bool, str]] = [
    (
        "phases declared at the top level are valid -- sage merges them",
        True,
        'schema_version = 1\ninstall = [\'echo hi\']\n[package]\nname = "x"\nversion = "1.0"\n',
    ),
    (
        "phases declared in [source] are valid -- sage merges those too",
        True,
        MINIMAL + f'[source]\nurl = "http://e/x.tar"\nsha256 = {VALID_CHECKSUM}\ninstall = [\'echo hi\']\n',
    ),
    (
        "a meta-package has no phases, only dependencies (this is `base`)",
        True,
        'schema_version = 1\n[package]\nname = "base"\nversion = "1.0"\ndependencies = ["linux"]\n',
    ),
    (
        "a top-level capability hook is valid",
        True,
        'schema_version = 1\ninstall = ["true"]\n'
        '[[capability_hooks]]\ncapability = "virtual/bootloader"\n'
        'exec = "/usr/bin/grub-mkconfig"\nargs = ["-o", "/boot/grub/grub.cfg"]\n'
        '[package]\nname = "x"\nversion = "1.0"\n',
    ),
    (
        "a package-scoped capability hook is valid",
        True,
        'schema_version = 1\n[package]\nname = "x"\nversion = "1.0"\ninstall = ["true"]\n'
        '[[package.capability_hooks]]\ncapability = "virtual/initramfs-generator"\n'
        'exec = "/usr/bin/mkinitcpio"\nargs = ["-P"]\n',
    ),
    (
        "a capability hook with a missing executable is rejected",
        False,
        'schema_version = 1\ninstall = ["true"]\n'
        '[[capability_hooks]]\ncapability = "virtual/bootloader"\n'
        '[package]\nname = "x"\nversion = "1.0"\n',
    ),
    (
        "a capability hook with non-string arguments is rejected",
        False,
        'schema_version = 1\ninstall = ["true"]\n'
        '[[capability_hooks]]\ncapability = "virtual/bootloader"\n'
        'exec = "/usr/bin/grub-mkconfig"\nargs = [1]\n'
        '[package]\nname = "x"\nversion = "1.0"\n',
    ),
    (
        "a typo at the top level must not slip through",
        False,
        'schema_version = 1\ndependecies = ["libc"]\n[package]\nname = "x"\nversion = "1.0"\ninstall = [\'echo hi\']\n',
    ),
    (
        "a typo in [source] must not slip through",
        False,
        MINIMAL + f'install = [\'echo hi\']\n[source]\nurl = "http://e/x.tar"\nsha256 = {VALID_CHECKSUM}\nshasum = "typo"\n',
    ),
    (
        "url must be a string -- an int makes sage skip fetching silently",
        False,
        MINIMAL + f'install = [\'echo hi\']\n[source]\nurl = 123\nsha256 = {VALID_CHECKSUM}\n',
    ),
    (
        "sha256 must be a string -- an int makes sage skip verification",
        False,
        MINIMAL + 'install = [\'echo hi\']\n[source]\nurl = "http://e/x.tar"\nsha256 = 456\n',
    ),
    (
        "release must be quoted -- an int changes the archive name",
        False,
        'schema_version = 1\n[package]\nname = "x"\nversion = "1.0"\nrelease = 1\ninstall = [\'echo hi\']\n',
    ),
    (
        "an architecture-independent package may use arch any",
        True,
        'schema_version = 1\n[package]\nname = "x"\nversion = "1.0"\narch = "any"\ninstall = [\'echo hi\']\n',
    ),
    (
        "an unknown package architecture is rejected",
        False,
        'schema_version = 1\n[package]\nname = "x"\nversion = "1.0"\narch = "mips64"\ninstall = [\'echo hi\']\n',
    ),
    (
        "a source url without a checksum is rejected by policy",
        False,
        MINIMAL + 'install = [\'echo hi\']\n[source]\nurl = "http://e/x.tar"\n',
    ),
    (
        "a malformed source checksum is rejected",
        False,
        MINIMAL + 'install = [\'echo hi\']\n[source]\nurl = "http://e/x.tar"\nsha256 = "abc"\n',
    ),
    (
        "neither phases nor dependencies means the package does nothing",
        False,
        MINIMAL,
    ),
    (
        "a phase array containing a non-string is rejected",
        False,
        'schema_version = 1\n[package]\nname = "x"\nversion = "1.0"\ninstall = [42]\n',
    ),
    (
        "a scalar phase is rejected without crashing the validator",
        False,
        'schema_version = 1\n[package]\nname = "x"\nversion = "1.0"\ninstall = 42\n',
    ),
    (
        "scalar dependencies are rejected without crashing the validator",
        False,
        'schema_version = 1\n[package]\nname = "x"\nversion = "1.0"\ndependencies = "linux"\n',
    ),
    (
        "a boolean is not an integer schema version",
        False,
        'schema_version = true\n[package]\nname = "x"\nversion = "1.0"\ninstall = [\'echo hi\']\n',
    ),
    (
        "a float is not an integer schema version",
        False,
        'schema_version = 1.0\n[package]\nname = "x"\nversion = "1.0"\ninstall = [\'echo hi\']\n',
    ),
    (
        "[package] is mandatory -- sage rejects the recipe outright without it",
        False,
        'schema_version = 1\ninstall = [\'echo hi\']\n',
    ),
]


# The count-based version of this rule accepted a change that added one
# checksumless recipe while resolving one legacy placeholder, because the total
# stayed the same. These pin the set semantics that replaced it.
DEBT_CASES: list[tuple[str, set[str], set[str], set[str], set[str]]] = [
    (
        "steady state: everything owed is grandfathered",
        {"a", "b"}, {"a", "b"},
        set(), set(),
    ),
    (
        "one added and one resolved -- the count is unchanged, both must surface",
        {"a", "b"}, {"a", "c"},
        {"c"}, {"b"},
    ),
    (
        "a newly added checksumless recipe is a violation",
        {"a"}, {"a", "b"},
        {"b"}, set(),
    ),
    (
        "a resolved entry left in the file must be de-listed",
        {"a", "b"}, {"a"},
        set(), {"b"},
    ),
    (
        "an empty file means every placeholder is a violation",
        set(), {"a", "b"},
        {"a", "b"}, set(),
    ),
    (
        "debt fully paid off",
        {"a"}, set(),
        set(), {"a"},
    ),
]


def write_recipe(path: pathlib.Path, checksum: str, package_extra: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        'schema_version = 1\n'
        '[package]\n'
        'name = "x"\n'
        'version = "1.0"\n'
        'install = ["true"]\n'
        f'{package_extra}'
        '[source]\n'
        'url = "https://example.invalid/x.tar"\n'
        f'sha256 = {checksum}\n'
    )


def debt_entries(text: str) -> set[str]:
    return {
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }


def run_main(root: pathlib.Path, debt: set[str], args: list[str]) -> tuple[int, str, str, str]:
    old_repo = validator.REPO
    old_debt_file = validator.CHECKSUM_DEBT_FILE
    try:
        validator.REPO = root
        validator.CHECKSUM_DEBT_FILE = root / "tests" / "checksum-debt.txt"
        validator.CHECKSUM_DEBT_FILE.parent.mkdir(parents=True, exist_ok=True)
        validator.write_debt(debt)
        before = validator.CHECKSUM_DEBT_FILE.read_text()
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            result = validator.main(args)
        after = validator.CHECKSUM_DEBT_FILE.read_text()
        return result, before, after, stdout.getvalue() + stderr.getvalue()
    finally:
        validator.REPO = old_repo
        validator.CHECKSUM_DEBT_FILE = old_debt_file


def check_integration_rules() -> tuple[int, int]:
    checks: list[tuple[str, object, object]] = []

    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        old = "recipes/old/recipe.toml"
        new = "recipes/new/recipe.toml"
        write_recipe(root / old, VALID_CHECKSUM)
        write_recipe(root / new, '""')
        result, before, after, _ = run_main(root, {old}, ["--update-debt"])
        checks.append(("updater rejects swapping old debt for new debt", (result, after), (1, before)))

    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        old = "recipes/old/recipe.toml"
        remaining = "recipes/remaining/recipe.toml"
        write_recipe(root / old, VALID_CHECKSUM)
        write_recipe(root / remaining, '""')
        result, _, after, _ = run_main(root, {old, remaining}, ["--update-debt"])
        checks.append((
            "updater removes resolved debt and preserves unpaid old debt",
            (result, debt_entries(after)),
            (0, {remaining}),
        ))

    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        new = "recipes/new/recipe.toml"
        write_recipe(root / new, '""')
        result, before, after, _ = run_main(root, set(), ["--update-debt"])
        checks.append(("updater rejects new debt without changing the file", (result, after), (1, before)))

    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        old = "recipes/old/recipe.toml"
        write_recipe(root / old, '""', "dependecies = []\n")
        result, before, after, _ = run_main(root, {old}, ["--update-debt"])
        checks.append(("recipe errors prevent updater writes", (result, after), (1, before)))

    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        old = "recipes/old/recipe.toml"
        path = root / old
        write_recipe(path, "123")
        checks.append((
            "a non-string checksum remains checksum debt",
            validator.MISSING_CHECKSUM in validator.validate(path),
            True,
        ))
        result, before, after, _ = run_main(root, {old}, [])
        checks.append(("non-string checksum does not remove its debt entry", (result, after), (1, before)))

    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        old = "recipes/old/recipe.toml"
        write_recipe(root / old, '""')
        result, before, after, _ = run_main(root, {old}, ["--strict", "--update-debt"])
        checks.append(("strict and update modes cannot be combined", (result, after), (2, before)))
        result, before, after, _ = run_main(root, {old}, ["--strcit"])
        checks.append(("unknown arguments fail instead of weakening validation", (result, after), (2, before)))

    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        clean = "recipes/clean/recipe.toml"
        old = "recipes/old/recipe.toml"
        new = "recipes/new/recipe.toml"
        write_recipe(root / clean, VALID_CHECKSUM)
        write_recipe(root / old, '""')
        write_recipe(root / new, '""')
        result, _, _, output = run_main(root, {old}, [])
        checks.append((
            "summary categories stay disjoint",
            (result, "1 passed, 1 failed, 1 awaiting a checksum  (3 recipes)" in output),
            (1, True),
        ))

    with tempfile.TemporaryDirectory() as tmp:
        root = pathlib.Path(tmp)
        expected = {
            "packages/src/recipe.toml",
            "src/recipes/foo/recipe.toml",
            ".stage/recipes/hidden/recipe.toml",
            "packages/shc/recipe.toml",
        }
        for rel in expected:
            write_recipe(root / rel, VALID_CHECKSUM)
        write_recipe(root / "packages/shc/src/vendor/recipe.toml", VALID_CHECKSUM)
        write_recipe(root / "out/aarch64/recipes/shc/recipe.toml", VALID_CHECKSUM)
        write_recipe(root / ".git/fixtures/recipe.toml", VALID_CHECKSUM)

        old_repo = validator.REPO
        try:
            validator.REPO = root
            discovered = {
                path.relative_to(root).as_posix()
                for path in validator.discover_recipes()
            }
        finally:
            validator.REPO = old_repo
        checks.append(("discovery includes authored trees and excludes build artifacts", discovered, expected))

    failed = 0
    for description, actual, expected in checks:
        if actual == expected:
            print(f"ok    {description}")
        else:
            failed += 1
            print(f"FAIL  {description}\n        expected {expected!r}, got {actual!r}")
    return failed, len(checks)


def check_debt_rule() -> int:
    failed = 0
    for description, grandfathered, owed, want_violations, want_resolved in DEBT_CASES:
        violations, resolved = validator.classify_debt(grandfathered, owed)
        if (violations, resolved) == (want_violations, want_resolved):
            print(f"ok    {description}")
        else:
            failed += 1
            print(f"FAIL  {description}")
            print(f"        expected violations={want_violations!r} resolved={want_resolved!r}")
            print(f"        got      violations={violations!r} resolved={resolved!r}")
    return failed


def main() -> int:
    failed = check_debt_rule()
    integration_failed, integration_total = check_integration_rules()
    failed += integration_failed
    for description, should_pass, toml in CASES:
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "recipe.toml"
            path.write_text(toml)
            errors = validator.validate(path)

        if (not errors) == should_pass:
            print(f"ok    {description}")
        else:
            failed += 1
            verdict = "accepted" if not errors else "rejected"
            print(f"FAIL  {description}\n        unexpectedly {verdict}")
            for err in errors:
                print(f"        {err}")

    total = len(CASES) + len(DEBT_CASES) + integration_total
    print(f"\n{total - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
