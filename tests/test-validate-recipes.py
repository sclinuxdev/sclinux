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
        "[package] is mandatory -- sage rejects the recipe outright without it",
        False,
        'schema_version = 1\ninstall = [\'echo hi\']\n',
    ),
]


def main() -> int:
    failed = 0
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

    print(f"\n{len(CASES) - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
