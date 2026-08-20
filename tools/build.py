#!/usr/bin/env python3
"""Shared entry point for ShenChen Linux architecture-aware builds."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 on the x86_64 build host
    import tomli as tomllib


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
RAW_SHA256 = re.compile(r"^[0-9a-f]{64}$")
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


def collect_stage1_sources(manifest: Path = DEFAULT_MANIFEST) -> list[dict]:
    by_url: dict[str, dict] = {}
    for name in load_stage1_manifest(manifest):
        path = DEFAULT_RECIPES / name / "recipe.toml"
        try:
            source = tomllib.loads(path.read_text()).get("source")
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"invalid TOML in {path}: {exc}") from exc
        if source is None:
            continue
        if not isinstance(source, dict):
            raise ConfigError(f"Stage1 recipe source must be a table: {name}")
        url = source.get("url")
        digest = source.get("sha256")
        if not isinstance(url, str) or not url:
            raise ConfigError(f"Stage1 recipe source URL is invalid: {name}")
        if not isinstance(digest, str) or not RAW_SHA256.fullmatch(digest):
            raise ConfigError(f"Stage1 recipe source SHA-256 is invalid: {name}")
        existing = by_url.get(url)
        if existing is not None:
            if existing["sha256"] != digest:
                raise ConfigError(f"Stage1 source URL has conflicting checksums: {url}")
            existing["packages"].append(name)
        else:
            by_url[url] = {"url": url, "sha256": digest, "packages": [name]}
    return [by_url[url] for url in sorted(by_url)]


def source_for_package(package: str, manifest: Path = DEFAULT_MANIFEST) -> dict:
    for source in collect_stage1_sources(manifest):
        if package in source["packages"]:
            return source
    raise ConfigError(f"Stage1 package has no locked source: {package}")


def parse_url_rewrites(values: list[str]) -> list[tuple[str, str]]:
    rewrites = []
    for value in values:
        old, separator, new = value.partition("=")
        if not separator or not old or not new or "://" not in old or "://" not in new:
            raise ConfigError(f"invalid URL rewrite {value!r}; expected OLD_URL_PREFIX=NEW_URL_PREFIX")
        rewrites.append((old, new))
    return rewrites


def rewrite_url(url: str, rewrites: list[tuple[str, str]]) -> str:
    for old, new in rewrites:
        if url.startswith(old):
            return new + url[len(old):]
    return url


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch_stage1_sources(
    sources: list[dict],
    output: Path,
    rewrites: list[tuple[str, str]],
    offline: bool = False,
) -> list[dict]:
    output.mkdir(parents=True, exist_ok=True)
    locked = []
    for source in sources:
        destination = output / source["sha256"]
        if not destination.is_file() or sha256_file(destination) != source["sha256"]:
            if offline:
                raise ConfigError(f"source is absent or corrupt in offline mode: {source['url']}")
            temporary = output / f".{source['sha256']}.part"
            url = rewrite_url(source["url"], rewrites)
            try:
                subprocess.run(
                    [
                        "curl",
                        "--fail",
                        "--location",
                        "--retry",
                        "4",
                        "--retry-all-errors",
                        "--connect-timeout",
                        "15",
                        "--output",
                        str(temporary),
                        url,
                    ],
                    check=True,
                )
            except (OSError, subprocess.CalledProcessError) as exc:
                raise ConfigError(f"failed to fetch Stage1 source: {source['url']}: {exc}") from exc
            actual = sha256_file(temporary)
            if actual != source["sha256"]:
                raise ConfigError(
                    f"Stage1 source checksum mismatch for {source['url']}: "
                    f"expected {source['sha256']}, got {actual}"
                )
            temporary.replace(destination)
        locked.append({**source, "cache": destination.name})
    return locked


def write_sources_lock(sources: list[dict], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": 1, "sources": sources}, indent=2, sort_keys=True) + "\n")
    return path


def source_filename(url: str) -> str:
    filename = Path(urlsplit(url).path).name
    return filename or "source.tar.gz"


def clean_stage1_recipe(recipe_dir: Path, package: str) -> None:
    for directory in ("distfiles", "src", "pkg"):
        shutil.rmtree(recipe_dir / directory, ignore_errors=True)
    for artifact in recipe_dir.glob(f"{package}-*.pkg.tar.zst"):
        artifact.unlink()


def clean_stage1_workdirs(recipe_dir: Path) -> None:
    for directory in ("distfiles", "src", "pkg"):
        shutil.rmtree(recipe_dir / directory, ignore_errors=True)


def stage_recipe_source(recipe_path: Path, source_cache: Path) -> Path | None:
    data = tomllib.loads(recipe_path.read_text())
    source = data.get("source")
    if source is None:
        return None
    digest = source["sha256"]
    cached = source_cache / digest
    if not cached.is_file() or sha256_file(cached) != digest:
        raise ConfigError(f"source is absent or corrupt for {data['package']['name']}: {cached}")
    destination = recipe_path.parent / "distfiles" / source_filename(source["url"])
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(cached, destination)
    return destination


def select_stage1_packages(
    packages: list[str], first: str | None = None, last: str | None = None
) -> list[str]:
    for name, label in ((first, "first"), (last, "last")):
        if name is not None and name not in packages:
            raise ConfigError(f"unknown {label} Stage1 package: {name}")
    start = packages.index(first) if first is not None else 0
    stop = packages.index(last) + 1 if last is not None else len(packages)
    if start >= stop:
        raise ConfigError("Stage1 package range is reversed")
    return packages[start:stop]


def prepend_environment(
    environment: dict[str, str], name: str, values: list[str], separator: str = ":"
) -> None:
    current = environment.get(name)
    environment[name] = separator.join(values + ([current] if current else []))


def stage1_runtime_library_paths(sysroot: Path) -> list[Path]:
    paths = [sysroot / "usr/lib", sysroot / "usr/lib64", sysroot / "lib", sysroot / "lib64"]
    perl_root = sysroot / "usr/lib/perl5"
    if perl_root.is_dir():
        paths.extend(sorted(perl_root.glob("*/core_perl/CORE")))
    return paths


def stage1_build_environment(
    seed: dict | None = None, sysroot: Path | None = None
) -> dict[str, str]:
    seed = seed or load_seed_lock()
    environment = os.environ.copy()
    environment.update(
        {
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "SOURCE_DATE_EPOCH": str(seed["source_date_epoch"]),
            "TZ": "UTC",
            "XMAKE_ROOT": "y",
            "XMAKE_STATS": "false",
        }
    )
    if sysroot is not None:
        usr = sysroot / "usr"
        wrappers = sysroot / ".stage1-tool-wrappers"
        environment["SC_BUILD_SYSROOT"] = str(sysroot.resolve())
        libraries = [str(usr / "lib"), str(sysroot / "lib")]
        includes = [str(usr / "include")]
        prepend_environment(
            environment,
            "PATH",
            [
                str(wrappers / "usr-bin"),
                str(wrappers / "usr-sbin"),
                str(usr / "bin"),
                str(usr / "sbin"),
                str(sysroot / "bin"),
                str(sysroot / "sbin"),
            ],
        )
        prepend_environment(environment, "LIBRARY_PATH", libraries)
        prepend_environment(environment, "CPATH", includes)
        prepend_environment(
            environment,
            "PKG_CONFIG_PATH",
            [str(usr / "lib/pkgconfig"), str(usr / "share/pkgconfig")],
        )
        prepend_environment(environment, "ACLOCAL_PATH", [str(usr / "share/aclocal")])
        prepend_environment(environment, "CMAKE_PREFIX_PATH", [str(usr)])
        perl_root = usr / "lib/perl5"
        if perl_root.is_dir():
            perl_paths = [
                path
                for version in sorted(perl_root.iterdir())
                for path in (
                    version / "core_perl",
                    version / "vendor_perl",
                    version / "site_perl",
                )
                if path.is_dir()
            ]
            prepend_environment(environment, "PERL5LIB", [str(path) for path in perl_paths])
        autoconf_modules = usr / "share/autoconf"
        if autoconf_modules.is_dir():
            environment["autom4te_perllibdir"] = str(autoconf_modules)
        prepend_environment(
            environment,
            "CPPFLAGS",
            [f"-I{usr / 'include'}"],
            separator=" ",
        )
        prepend_environment(
            environment,
            "LDFLAGS",
            [f"-L{usr / 'lib'}", f"-Wl,-rpath-link,{usr / 'lib'}"],
            separator=" ",
        )
    return environment


def stage1_package_entry(
    name: str, recipe_path: Path, architecture: dict[str, str]
) -> dict | None:
    data = tomllib.loads(recipe_path.read_text())
    package = data["package"]
    if package.get("arch") != architecture["arch"]:
        raise ConfigError(f"rendered Stage1 recipe has wrong architecture: {name}")
    artifact = recipe_path.parent / (
        f"{name}-{package['version']}-{package.get('release', '1')}-"
        f"{architecture['arch']}.pkg.tar.zst"
    )
    if not artifact.is_file():
        return None
    return {
        "name": name,
        "version": package["version"],
        "release": package.get("release", "1"),
        "arch": architecture["arch"],
        "sha256": sha256_file(artifact),
        "recipe_sha256": sha256_file(recipe_path),
        "artifact": artifact.name,
    }


def stage_stage1_package(
    entry: dict, recipe_path: Path, sysroot: Path, environment: dict[str, str]
) -> None:
    stamps = sysroot / ".stage1-build-packages"
    stamp = stamps / entry["name"]
    if stamp.is_file() and stamp.read_text().strip() == entry["sha256"]:
        return
    artifact = recipe_path.parent / entry["artifact"]
    sysroot.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "tar",
            "--zstd",
            "--extract",
            "--file",
            str(artifact),
            "--directory",
            str(sysroot),
            "--strip-components=1",
            "data",
        ],
        check=True,
        env=environment,
    )
    stamps.mkdir(parents=True, exist_ok=True)
    stamp.write_text(entry["sha256"] + "\n")


def clean_stage1_build_sysroot(sysroot: Path) -> None:
    library_root = sysroot / "usr/lib"
    if not library_root.is_dir():
        return
    for archive in library_root.rglob("*.la"):
        archive.unlink()


def validate_stage1_package_shebangs(recipe_dir: Path, forbidden_roots: list[Path]) -> None:
    package_root = recipe_dir / "pkg"
    forbidden = {
        os.fsencode(str(candidate))
        for root in forbidden_roots
        for candidate in (root, root.resolve())
    }
    if not package_root.is_dir():
        return
    for path in package_root.rglob("*"):
        if path.is_symlink() or not path.is_file():
            continue
        with path.open("rb") as stream:
            first_line = stream.readline(4096)
        if first_line.startswith(b"#!") and any(root in first_line for root in forbidden):
            raise ConfigError(f"Stage1 package shebang contains a build path: {path}")


def stage1_dynamic_loader(architecture: dict[str, str]) -> str:
    directory = "/lib64" if architecture["arch"] == "x86_64" else "/lib"
    return f"{directory}/{architecture['dynamic_linker']}"


def refresh_stage1_tool_wrappers(sysroot: Path, architecture: dict[str, str]) -> None:
    wrapper_root = sysroot / ".stage1-tool-wrappers"
    shutil.rmtree(wrapper_root, ignore_errors=True)
    resolved_sysroot = sysroot.resolve()
    library_path = ":".join(str(path) for path in stage1_runtime_library_paths(sysroot))
    for relative, wrapper_name in (("usr/bin", "usr-bin"), ("usr/sbin", "usr-sbin")):
        source = sysroot / relative
        if not source.is_dir():
            continue
        destination = wrapper_root / wrapper_name
        destination.mkdir(parents=True, exist_ok=True)
        for executable in source.iterdir():
            try:
                resolved = executable.resolve(strict=True)
                resolved.relative_to(resolved_sysroot)
                with resolved.open("rb") as stream:
                    is_elf = stream.read(4) == b"\x7fELF"
            except (FileNotFoundError, OSError, ValueError):
                continue
            if not is_elf:
                continue
            wrapper = destination / executable.name
            wrapper.write_text(
                "#!/bin/sh\n"
                f"exec {shlex.quote(stage1_dynamic_loader(architecture))} "
                f"--library-path {shlex.quote(library_path)} "
                f"{shlex.quote(str(executable))} \"$@\"\n"
            )
            wrapper.chmod(0o755)


def run_stage1_packages(
    architecture: dict[str, str],
    workspace: Path,
    first: str | None = None,
    last: str | None = None,
    sage: str = "sage",
    sysroot: Path | None = None,
) -> list[dict]:
    manifest = load_stage1_manifest()
    packages = select_stage1_packages(manifest, first, last)
    recipes = workspace / "recipes"
    sources = workspace / "sources"
    locked_by_name = {}
    for name in manifest:
        recipe_path = recipes / name / "recipe.toml"
        if recipe_path.is_file():
            entry = stage1_package_entry(name, recipe_path, architecture)
            if entry is not None:
                locked_by_name[name] = entry
    built = []
    sysroot = sysroot or workspace / "build-sysroot"
    environment = stage1_build_environment(sysroot=sysroot)
    selected = set(packages)
    for name in manifest:
        if name in selected or name not in locked_by_name:
            continue
        stage_stage1_package(
            locked_by_name[name], recipes / name / "recipe.toml", sysroot, environment
        )
    clean_stage1_build_sysroot(sysroot)
    refresh_stage1_tool_wrappers(sysroot, architecture)
    environment = stage1_build_environment(sysroot=sysroot)
    for name in packages:
        recipe_path = recipes / name / "recipe.toml"
        if not recipe_path.is_file():
            raise ConfigError(f"rendered Stage1 recipe does not exist: {recipe_path}")
        clean_stage1_recipe(recipe_path.parent, name)
        stage_recipe_source(recipe_path, sources)
        try:
            subprocess.run(
                [sage, "build", str(recipe_path.parent)], check=True, env=environment
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            raise ConfigError(f"Stage1 package build failed: {name}: {exc}") from exc
        validate_stage1_package_shebangs(recipe_path.parent, [workspace, sysroot])
        entry = stage1_package_entry(name, recipe_path, architecture)
        if entry is None:
            raise ConfigError(f"Stage1 package artifact is missing after build: {name}")
        built.append(entry)
        locked_by_name[name] = entry
        stage_stage1_package(entry, recipe_path, sysroot, environment)
        clean_stage1_build_sysroot(sysroot)
        refresh_stage1_tool_wrappers(sysroot, architecture)
        environment = stage1_build_environment(sysroot=sysroot)
        clean_stage1_workdirs(recipe_path.parent)
        locked = [locked_by_name[name] for name in manifest if name in locked_by_name]
        write_packages_lock(locked, workspace / "packages.lock")
    return built


def write_packages_lock(packages: list[dict], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": 1, "packages": packages}, indent=2, sort_keys=True) + "\n")
    return path


def stage0_command(name: str, architecture: dict[str, str], seed: dict, tag: str) -> list[str]:
    try:
        manifest_digest = seed["manifests"][name]
    except KeyError as exc:
        raise ConfigError(f"Stage0 seed has no manifest for {name}") from exc

    image = f"{seed['image']}@{seed['index_digest']}"
    packages = " ".join(seed["packages"])
    sage = source_for_package("sage")
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
        "--build-arg",
        f"SAGE_URL={sage['url']}",
        "--build-arg",
        f"SAGE_SHA256={sage['sha256']}",
        str(REPO),
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
        "bootstrap_sage": source_for_package("sage"),
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
    fetch = commands.add_parser("fetch", help="download and verify every locked Stage1 source")
    fetch.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    fetch.add_argument("--output-dir", type=Path, help="override the content-addressed cache")
    fetch.add_argument(
        "--rewrite",
        action="append",
        default=[],
        metavar="OLD=NEW",
        help="rewrite a URL prefix for transport; may be repeated",
    )
    fetch.add_argument("--offline", action="store_true", help="verify cache without downloading")
    stage1_run = commands.add_parser(
        "stage1-run", help="run rendered Stage1 recipes inside an architecture-matched Stage0"
    )
    stage1_run.add_argument("--workspace", type=Path, required=True)
    stage1_run.add_argument("--first", help="start at this manifest package")
    stage1_run.add_argument("--last", help="stop after this manifest package")
    stage1_run.add_argument("--sage", default="sage", help="Sage executable inside Stage0")
    stage1_run.add_argument(
        "--sysroot", type=Path, help="isolated build dependency sysroot (default: WORKSPACE/build-sysroot)"
    )
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
    elif args.command == "fetch":
        output = args.output_dir or REPO / "out" / args.arch / "sources"
        try:
            sources = collect_stage1_sources(args.manifest)
            rewrites = parse_url_rewrites(args.rewrite)
            locked = fetch_stage1_sources(sources, output, rewrites, args.offline)
            lock = write_sources_lock(locked, output.parent / "sources.lock")
        except (ConfigError, OSError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(f"verified {len(locked)} Stage1 sources; wrote {lock}")
    elif args.command == "stage1-run":
        try:
            locked = run_stage1_packages(
                architecture, args.workspace, args.first, args.last, args.sage, args.sysroot
            )
        except (ConfigError, OSError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        print(f"built {len(locked)} Stage1 package(s); wrote {args.workspace / 'packages.lock'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
