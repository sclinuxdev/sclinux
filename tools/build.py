#!/usr/bin/env python3
"""Shared entry point for ShenChen Linux architecture-aware builds."""

from __future__ import annotations

import argparse
import json
import re
import shlex
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = REPO / "config" / "architectures.toml"
DEFAULT_SEED_LOCK = REPO / "Stage0" / "seed.lock.toml"
DEFAULT_MANIFEST = REPO / "Stage1" / "manifest.toml"
DEFAULT_RECIPES = REPO / "Stage1" / "recipes"
ARCH_NAME = re.compile(r"^[a-z0-9_]+$")
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
SNAPSHOT = re.compile(r"^[0-9]{8}T[0-9]{6}Z$")
PACKAGE_NAME = re.compile(r"^[a-z0-9][a-z0-9+.-]*$")
ARCH_TOKEN = re.compile(r"@SC_[A-Z0-9_]+@")
ARCH_FIELDS = (
    "gnu_triplet",
    "kernel_arch",
    "kernel_image",
    "efi_boot_name",
    "dynamic_linker",
    "qemu_system",
    "qemu_machine",
    "oci_platform",
)


class ConfigError(ValueError):
    """The architecture configuration cannot safely drive a build."""


def load_architectures(path: Path = DEFAULT_CONFIG) -> dict[str, dict[str, str]]:
    try:
        data = tomllib.loads(path.read_text())
    except FileNotFoundError as exc:
        raise ConfigError(f"architecture config does not exist: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {path}: {exc}") from exc

    unknown_top = set(data) - {"schema_version", "architectures"}
    if unknown_top:
        raise ConfigError(f"unknown top-level key(s): {', '.join(sorted(unknown_top))}")
    if type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        raise ConfigError("schema_version must be the integer 1")

    architectures = data.get("architectures")
    if not isinstance(architectures, dict) or not architectures:
        raise ConfigError("[architectures] must contain at least one architecture")

    checked: dict[str, dict[str, str]] = {}
    required = set(ARCH_FIELDS)
    for name, values in architectures.items():
        if not ARCH_NAME.fullmatch(name):
            raise ConfigError(f"invalid architecture name: {name!r}")
        if not isinstance(values, dict):
            raise ConfigError(f"[architectures.{name}] must be a table")

        missing = required - set(values)
        unknown = set(values) - required
        if missing:
            raise ConfigError(
                f"[architectures.{name}] missing field(s): {', '.join(sorted(missing))}"
            )
        if unknown:
            raise ConfigError(
                f"[architectures.{name}] unknown field(s): {', '.join(sorted(unknown))}"
            )
        for field in ARCH_FIELDS:
            value = values[field]
            if not isinstance(value, str) or not value:
                raise ConfigError(f"[architectures.{name}] {field} must be a non-empty string")
        checked[name] = {field: values[field] for field in ARCH_FIELDS}

    return checked


def resolve_architecture(name: str, path: Path = DEFAULT_CONFIG) -> dict[str, str]:
    architectures = load_architectures(path)
    try:
        values = architectures[name]
    except KeyError as exc:
        supported = ", ".join(sorted(architectures))
        raise ConfigError(f"unsupported architecture {name!r}; choose one of: {supported}") from exc
    return {"arch": name, **values}


def load_seed_lock(path: Path = DEFAULT_SEED_LOCK) -> dict:
    try:
        data = tomllib.loads(path.read_text())
    except FileNotFoundError as exc:
        raise ConfigError(f"Stage0 seed lock does not exist: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {path}: {exc}") from exc

    allowed = {
        "schema_version",
        "image",
        "suite",
        "snapshot",
        "source_date_epoch",
        "index_digest",
        "packages",
        "manifests",
    }
    unknown = set(data) - allowed
    if unknown:
        raise ConfigError(f"unknown Stage0 key(s): {', '.join(sorted(unknown))}")
    if type(data.get("schema_version")) is not int or data["schema_version"] != 1:
        raise ConfigError("Stage0 schema_version must be the integer 1")

    for field in ("image", "suite"):
        if not isinstance(data.get(field), str) or not data[field]:
            raise ConfigError(f"Stage0 {field} must be a non-empty string")
    if not isinstance(data.get("snapshot"), str) or not SNAPSHOT.fullmatch(data["snapshot"]):
        raise ConfigError("Stage0 snapshot must use YYYYMMDDTHHMMSSZ")
    if type(data.get("source_date_epoch")) is not int or data["source_date_epoch"] <= 0:
        raise ConfigError("Stage0 source_date_epoch must be a positive integer")
    if not isinstance(data.get("index_digest"), str) or not DIGEST.fullmatch(data["index_digest"]):
        raise ConfigError("Stage0 index_digest must be a sha256 OCI digest")

    packages = data.get("packages")
    if not isinstance(packages, list) or not packages:
        raise ConfigError("Stage0 packages must be a non-empty array")
    if not all(isinstance(package, str) and PACKAGE_NAME.fullmatch(package) for package in packages):
        raise ConfigError("Stage0 packages contains an invalid package name")
    if packages != sorted(set(packages)):
        raise ConfigError("Stage0 packages must be unique and sorted")

    manifests = data.get("manifests")
    if not isinstance(manifests, dict) or not manifests:
        raise ConfigError("[manifests] must contain architecture digests")
    for name, digest in manifests.items():
        if not ARCH_NAME.fullmatch(name) or not isinstance(digest, str) or not DIGEST.fullmatch(digest):
            raise ConfigError(f"invalid Stage0 manifest entry for {name!r}")
    return data


def shell_environment(architecture: dict[str, str]) -> str:
    variables = {f"SC_{key.upper()}": value for key, value in architecture.items()}
    return "\n".join(f"{key}={shlex.quote(value)}" for key, value in variables.items())


def stage0_tag(name: str, seed: dict) -> str:
    return f"shenchen-stage0:{name}-{seed['index_digest'][7:19]}"


def render_stage1_recipe(recipe: str, architecture: dict[str, str], output: Path) -> Path:
    if not PACKAGE_NAME.fullmatch(recipe):
        raise ConfigError(f"invalid Stage1 recipe name: {recipe!r}")
    source = DEFAULT_RECIPES / recipe / "recipe.toml"
    try:
        text = source.read_text()
        data = tomllib.loads(text)
    except FileNotFoundError as exc:
        raise ConfigError(f"Stage1 recipe does not exist: {recipe}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {source}: {exc}") from exc

    package = data.get("package")
    if not isinstance(package, dict) or package.get("name") != recipe:
        raise ConfigError(f"Stage1 recipe package name must be {recipe!r}")

    for field, value in architecture.items():
        text = text.replace(f"@SC_{field.upper()}@", value)
    unresolved = sorted(set(ARCH_TOKEN.findall(text)))
    if unresolved:
        raise ConfigError(
            f"unknown architecture token(s) in {recipe}: {', '.join(unresolved)}"
        )

    lines = text.splitlines()
    try:
        package_line = next(i for i, line in enumerate(lines) if line.strip() == "[package]")
    except StopIteration as exc:
        raise ConfigError(f"Stage1 recipe has no [package] table: {recipe}") from exc

    end = next(
        (i for i in range(package_line + 1, len(lines)) if lines[i].lstrip().startswith("[")),
        len(lines),
    )
    arch_line = next(
        (i for i in range(package_line + 1, end) if re.match(r"^\s*arch\s*=", lines[i])),
        None,
    )
    rendered_arch = f'arch = "{architecture["arch"]}"'
    if arch_line is None:
        lines.insert(package_line + 1, rendered_arch)
    else:
        lines[arch_line] = rendered_arch
    rendered = "\n".join(lines) + "\n"

    if tomllib.loads(rendered)["package"]["arch"] != architecture["arch"]:
        raise ConfigError(f"failed to render architecture for Stage1 recipe: {recipe}")
    output.parent.mkdir(parents=True, exist_ok=True)
    for helper in source.parent.iterdir():
        if helper.is_file() and helper.name != source.name:
            shutil.copy2(helper, output.parent / helper.name)
    output.write_text(rendered)
    return output


def recipe_dependencies(path: Path) -> set[str]:
    try:
        data = tomllib.loads(path.read_text())
    except FileNotFoundError as exc:
        raise ConfigError(f"Stage1 recipe does not exist: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {path}: {exc}") from exc

    dependencies: set[str] = set()
    for table in (data, data.get("package", {}), data.get("source", {})):
        if not isinstance(table, dict):
            continue
        for field in ("dependencies", "build_dependencies"):
            values = table.get(field, [])
            if isinstance(values, list):
                dependencies.update(value.split(maxsplit=1)[0] for value in values)
    return dependencies


def load_stage1_manifest(path: Path = DEFAULT_MANIFEST) -> list[str]:
    try:
        data = tomllib.loads(path.read_text())
    except FileNotFoundError as exc:
        raise ConfigError(f"Stage1 manifest does not exist: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML in {path}: {exc}") from exc

    if set(data) != {"schema_version", "stage", "packages"}:
        raise ConfigError("Stage1 manifest must contain only schema_version, stage, and packages")
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise ConfigError("Stage1 schema_version must be the integer 1")
    if type(data["stage"]) is not int or data["stage"] != 1:
        raise ConfigError("Stage1 stage must be the integer 1")
    packages = data["packages"]
    if not isinstance(packages, list) or not packages:
        raise ConfigError("Stage1 packages must be a non-empty array of tables")

    names: list[str] = []
    allowed_fields = {"name", "batch", "action", "note"}
    for index, package in enumerate(packages):
        if not isinstance(package, dict) or set(package) - allowed_fields:
            raise ConfigError(f"invalid Stage1 package entry at index {index}")
        name = package.get("name")
        if not isinstance(name, str) or not PACKAGE_NAME.fullmatch(name):
            raise ConfigError(f"invalid Stage1 package name at index {index}")
        if not isinstance(package.get("batch"), str) or not package["batch"]:
            raise ConfigError(f"Stage1 package {name} must have a batch")
        if package.get("action") != "build":
            raise ConfigError(f"Stage1 package {name} action must be 'build'")
        if "note" in package and not isinstance(package["note"], str):
            raise ConfigError(f"Stage1 package {name} note must be a string")
        names.append(name)

    if len(names) != len(set(names)):
        duplicates = sorted(name for name in set(names) if names.count(name) > 1)
        raise ConfigError(f"duplicate Stage1 package(s): {', '.join(duplicates)}")

    recipe_names = {path.parent.name for path in DEFAULT_RECIPES.glob("*/recipe.toml")}
    missing = recipe_names - set(names)
    unknown = set(names) - recipe_names
    if missing or unknown:
        details = []
        if missing:
            details.append(f"missing from manifest: {', '.join(sorted(missing))}")
        if unknown:
            details.append(f"missing recipe: {', '.join(sorted(unknown))}")
        raise ConfigError("Stage1 manifest and recipes differ; " + "; ".join(details))

    positions = {name: index for index, name in enumerate(names)}
    for name in names:
        dependencies = recipe_dependencies(DEFAULT_RECIPES / name / "recipe.toml")
        unresolved = sorted(
            dependency
            for dependency in dependencies
            if dependency not in recipe_names
            and not dependency.startswith(("virtual/", "so:"))
        )
        if unresolved:
            raise ConfigError(f"Stage1 package {name} has no recipe for: {', '.join(unresolved)}")
        late = sorted(
            dependency
            for dependency in dependencies & recipe_names
            if positions[dependency] > positions[name]
        )
        if late:
            raise ConfigError(f"Stage1 package {name} precedes dependency: {', '.join(late)}")

    base_dependencies = recipe_dependencies(DEFAULT_RECIPES / "base" / "recipe.toml")
    expected_base = recipe_names - {"base"}
    if base_dependencies != expected_base:
        missing_base = expected_base - base_dependencies
        extra_base = base_dependencies - expected_base
        details = []
        if missing_base:
            details.append(f"missing: {', '.join(sorted(missing_base))}")
        if extra_base:
            details.append(f"unknown: {', '.join(sorted(extra_base))}")
        raise ConfigError("Stage1 base dependency set differs; " + "; ".join(details))
    return names


def render_stage1_recipes(
    architecture: dict[str, str], output: Path, manifest: Path = DEFAULT_MANIFEST
) -> list[Path]:
    names = load_stage1_manifest(manifest)
    return [
        render_stage1_recipe(name, architecture, output / name / "recipe.toml")
        for name in names
    ]


def stage0_command(name: str, architecture: dict[str, str], seed: dict, tag: str) -> list[str]:
    try:
        manifest_digest = seed["manifests"][name]
    except KeyError as exc:
        raise ConfigError(f"Stage0 seed has no manifest for {name}") from exc

    image = f"{seed['image']}@{seed['index_digest']}"
    packages = " ".join(seed["packages"])
    return [
        "docker",
        "buildx",
        "build",
        "--provenance=false",
        "--load",
        "--platform",
        architecture["oci_platform"],
        "--file",
        str(REPO / "Stage0" / "Containerfile"),
        "--tag",
        tag,
        "--label",
        f"org.shenchen.stage0.index-digest={seed['index_digest']}",
        "--label",
        f"org.shenchen.stage0.manifest-digest={manifest_digest}",
        "--build-arg",
        f"SEED_IMAGE={image}",
        "--build-arg",
        f"DEBIAN_SUITE={seed['suite']}",
        "--build-arg",
        f"DEBIAN_SNAPSHOT={seed['snapshot']}",
        "--build-arg",
        f"SOURCE_DATE_EPOCH={seed['source_date_epoch']}",
        "--build-arg",
        f"APT_PACKAGES={packages}",
        str(REPO / "Stage0"),
    ]


def write_stage0_metadata(
    name: str, architecture: dict[str, str], seed: dict, tag: str
) -> Path:
    query = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--platform",
            architecture["oci_platform"],
            tag,
            "dpkg-query",
            "-W",
            "-f=${binary:Package}\\t${Version}\\n",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    image_id = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", tag],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    packages = dict(line.split("\t", 1) for line in query.stdout.splitlines())
    metadata = {
        "architecture": name,
        "oci_platform": architecture["oci_platform"],
        "image": tag,
        "image_id": image_id,
        "seed_index_digest": seed["index_digest"],
        "seed_manifest_digest": seed["manifests"][name],
        "snapshot": seed["snapshot"],
        "packages": packages,
    }
    output = REPO / "out" / name / "stage0.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")
    return output


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--arch", required=True, help="target architecture")
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG,
        help=f"architecture config (default: {DEFAULT_CONFIG.relative_to(REPO)})",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    info = commands.add_parser("arch-info", help="print resolved target architecture values")
    info.add_argument("--format", choices=("json", "shell"), default="json")
    stage0 = commands.add_parser("stage0", help="build the locked Stage0 seed image")
    stage0.add_argument("--seed-lock", type=Path, default=DEFAULT_SEED_LOCK)
    stage0.add_argument("--tag", help="override the local container image tag")
    stage0.add_argument("--dry-run", action="store_true", help="print the docker command")
    recipe = commands.add_parser(
        "stage1-recipe", help="render one canonical recipe for the target architecture"
    )
    recipe.add_argument("recipe", help="recipe name under Stage1/recipes")
    recipe.add_argument("--output", type=Path, help="override the rendered recipe path")
    recipes = commands.add_parser(
        "stage1-recipes", help="validate the Stage1 manifest and render every recipe"
    )
    recipes.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    recipes.add_argument("--output-dir", type=Path, help="override the rendered recipe directory")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    try:
        architecture = resolve_architecture(args.arch, args.config)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.command == "arch-info":
        if args.format == "shell":
            print(shell_environment(architecture))
        else:
            print(json.dumps(architecture, indent=2, sort_keys=True))
    elif args.command == "stage0":
        try:
            seed = load_seed_lock(args.seed_lock)
            if set(seed["manifests"]) != set(load_architectures(args.config)):
                raise ConfigError("Stage0 manifests must match the configured architectures")
            tag = args.tag or stage0_tag(args.arch, seed)
            command = stage0_command(args.arch, architecture, seed, tag)
        except ConfigError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        if args.dry_run:
            print(shlex.join(command))
            return 0
        try:
            subprocess.run(command, check=True)
            output = write_stage0_metadata(args.arch, architecture, seed, tag)
        except (OSError, subprocess.CalledProcessError) as exc:
            print(f"error: Stage0 build failed: {exc}", file=sys.stderr)
            return 1
        print(f"wrote {output.relative_to(REPO)}")
    elif args.command == "stage1-recipe":
        output = args.output or REPO / "out" / args.arch / "recipes" / args.recipe / "recipe.toml"
        try:
            rendered = render_stage1_recipe(args.recipe, architecture, output)
        except (ConfigError, OSError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        try:
            print(f"wrote {rendered.relative_to(REPO)}")
        except ValueError:
            print(f"wrote {rendered}")
    elif args.command == "stage1-recipes":
        output = args.output_dir or REPO / "out" / args.arch / "recipes"
        try:
            rendered = render_stage1_recipes(architecture, output, args.manifest)
        except (ConfigError, OSError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(f"wrote {len(rendered)} Stage1 recipes under {output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
