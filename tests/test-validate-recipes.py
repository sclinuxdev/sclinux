#!/usr/bin/env python3
"""Fixture tests for tests/validate-recipes.py.

The validator's first version checked only the [package] scope while sage
merges three, so it rejected valid recipes and missed typos elsewhere. These
fixtures pin the behaviour that was wrong, plus the meta-package case that
`base` will depend on.

    python3 tests/test-validate-recipes.py
"""

import importlib.util
import pathlib
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("validate_recipes", HERE / "validate-recipes.py")
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)

MINIMAL = 'schema_version = 1\n[package]\nname = "x"\nversion = "1.0"\n'

CASES: list[tuple[str, bool, str]] = [
    (
        "phases declared at the top level are valid -- sage merges them",
        True,
        'schema_version = 1\ninstall = [\'echo hi\']\n[package]\nname = "x"\nversion = "1.0"\n',
    ),
    (
        "phases declared in [source] are valid -- sage merges those too",
        True,
        MINIMAL + '[source]\nurl = "http://e/x.tar"\nsha256 = "abc"\ninstall = [\'echo hi\']\n',
    ),
    (
        "a meta-package has no phases, only dependencies (this is `base`)",
        True,
        'schema_version = 1\n[package]\nname = "base"\nversion = "1.0"\ndependencies = ["linux"]\n',
    ),
    (
        "a typo at the top level must not slip through",
        False,
        'schema_version = 1\ndependecies = ["libc"]\n[package]\nname = "x"\nversion = "1.0"\ninstall = [\'echo hi\']\n',
    ),
    (
        "a typo in [source] must not slip through",
        False,
        MINIMAL + 'install = [\'echo hi\']\n[source]\nurl = "http://e/x.tar"\nsha256 = "abc"\nshasum = "typo"\n',
    ),
    (
        "url must be a string -- an int makes sage skip fetching silently",
        False,
        MINIMAL + 'install = [\'echo hi\']\n[source]\nurl = 123\nsha256 = "abc"\n',
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
        "a source url without a checksum is rejected by policy",
        False,
        MINIMAL + 'install = [\'echo hi\']\n[source]\nurl = "http://e/x.tar"\n',
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

    total = len(CASES) + len(DEBT_CASES)
    print(f"\n{total - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
