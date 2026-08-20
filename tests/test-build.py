#!/usr/bin/env python3
"""Tests for the architecture-aware build entry point."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "tools" / "build.py"
spec = importlib.util.spec_from_file_location("shenchen_build", BUILD)
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


def check(description: str, actual: object, expected: object) -> bool:
    if actual == expected:
        print(f"ok    {description}")
        return True
    print(f"FAIL  {description}\n        expected {expected!r}, got {actual!r}")
    return False


def config_error(text: str) -> str:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "architectures.toml"
        path.write_text(text)
        try:
            build.load_architectures(path)
        except build.ConfigError as exc:
            return str(exc)
    return "no error"


def main() -> int:
    failed = 0
    architectures = build.load_architectures()
    failed += not check(
        "repository declares exactly the two target architectures",
        set(architectures),
        {"x86_64", "aarch64"},
    )
    failed += not check(
        "aarch64 uses the arm64 kernel image",
        architectures["aarch64"]["kernel_image"],
        "arch/arm64/boot/Image",
    )
    failed += not check(
        "x86_64 uses the standard removable-media EFI name",
        architectures["x86_64"]["efi_boot_name"],
        "BOOTX64.EFI",
    )
    failed += not check(
        "architectures map to distinct OCI platforms",
        {values["oci_platform"] for values in architectures.values()},
        {"linux/amd64", "linux/arm64"},
    )
    failed += not check(
        "boolean schema versions are rejected",
        config_error("schema_version = true\n[architectures]\n"),
        "schema_version must be the integer 1",
    )
    failed += not check(
        "missing architecture fields are rejected",
        config_error("schema_version = 1\n[architectures.aarch64]\ngnu_triplet = \"a\"\n"),
        "[architectures.aarch64] missing field(s): dynamic_linker, efi_boot_name, "
        "kernel_arch, kernel_image, oci_platform, qemu_machine, qemu_system",
    )

    result = subprocess.run(
        [sys.executable, BUILD, "--arch", "aarch64", "arch-info"],
        check=False,
        capture_output=True,
        text=True,
    )
    failed += not check("arch-info exits successfully", result.returncode, 0)
    if result.returncode == 0:
        info = json.loads(result.stdout)
        failed += not check("arch-info identifies its target", info["arch"], "aarch64")

    result = subprocess.run(
        [sys.executable, BUILD, "--arch", "riscv64", "arch-info"],
        check=False,
        capture_output=True,
        text=True,
    )
    failed += not check("unknown architectures fail", result.returncode, 2)
    failed += not check(
        "unknown architecture error lists supported targets",
        "aarch64, x86_64" in result.stderr,
        True,
    )

    seed = build.load_seed_lock()
    failed += not check(
        "Stage0 locks one manifest per architecture",
        set(seed["manifests"]),
        set(architectures),
    )
    failed += not check(
        "Stage0 package list remains sorted",
        seed["packages"],
        sorted(seed["packages"]),
    )
    containerfile = (REPO / "Stage0" / "Containerfile").read_text()
    forbidden = [line for line in containerfile.splitlines() if line.startswith(("COPY ", "ADD "))]
    failed += not check("Stage0 never copies the repository into its seed", forbidden, [])
    failed += not check(
        "Stage0 allows xmake inside the root-owned build container",
        "XMAKE_ROOT=y" in containerfile,
        True,
    )
    failed += not check(
        "Stage0 disables xmake network statistics",
        "XMAKE_STATS=false" in containerfile,
        True,
    )
    failed += not check(
        "Stage0 verifies the Sage source before building it",
        "sha256sum --check --strict" in containerfile,
        True,
    )

    command = build.stage0_command(
        "aarch64",
        build.resolve_architecture("aarch64"),
        seed,
        build.stage0_tag("aarch64", seed),
    )
    failed += not check("Stage0 selects the ARM64 OCI platform", "linux/arm64" in command, True)
    failed += not check(
        "Stage0 disables timestamped BuildKit provenance",
        "--provenance=false" in command,
        True,
    )
    failed += not check(
        "Stage0 pins the OCI index digest",
        any(seed["index_digest"] in argument for argument in command),
        True,
    )
    sage_source = build.source_for_package("sage")
    failed += not check(
        "Stage0 receives the locked Sage source identity",
        {
            f"SAGE_URL={sage_source['url']}",
            f"SAGE_SHA256={sage_source['sha256']}",
        }.issubset(command),
        True,
    )

    stage1_packages = build.load_stage1_manifest()
    failed += not check(
        "Stage1 manifest covers every canonical recipe",
        len(stage1_packages),
        107,
    )
    failed += not check(
        "Stage1 dependency order leaves the base meta-package last",
        stage1_packages[-1],
        "base",
    )
    stage1_sources = build.collect_stage1_sources()
    failed += not check(
        "Stage1 source lock deduplicates canonical URLs",
        len(stage1_sources),
        90,
    )
    failed += not check(
        "Stage1 source lock retains every package reference",
        sum(len(source["packages"]) for source in stage1_sources),
        103,
    )
    rewrites = build.parse_url_rewrites(
        ["https://github.com/=https://mirror.invalid/https://github.com/"]
    )
    failed += not check(
        "Stage1 fetch rewrites transport URLs without changing the lock",
        build.rewrite_url("https://github.com/org/repo/archive/v1.tar.gz", rewrites),
        "https://mirror.invalid/https://github.com/org/repo/archive/v1.tar.gz",
    )

    with tempfile.TemporaryDirectory() as directory:
        output = Path(directory) / "sage" / "recipe.toml"
        build.render_stage1_recipe(
            "sage", build.resolve_architecture("aarch64"), output
        )
        rendered = build.tomllib.loads(output.read_text())
        canonical = build.tomllib.loads(
            (REPO / "Stage1" / "recipes" / "sage" / "recipe.toml").read_text()
        )
        failed += not check(
            "Stage1 renderer writes the target architecture",
            rendered["package"]["arch"],
            "aarch64",
        )
        failed += not check(
            "Stage1 renderer preserves the locked Sage checksum",
            rendered["source"]["sha256"],
            canonical["source"]["sha256"],
        )
        failed += not check(
            "canonical Stage1 recipes remain architecture-neutral",
            "arch" in canonical["package"],
            False,
        )

        all_output = Path(directory) / "all-recipes"
        all_rendered = build.render_stage1_recipes(
            build.resolve_architecture("aarch64"), all_output
        )
        failed += not check(
            "Stage1 renderer emits every manifest package",
            len(all_rendered),
            107,
        )
        failed += not check(
            "Stage1 renderer assigns one target architecture to every package",
            {
                build.tomllib.loads(path.read_text())["package"]["arch"]
                for path in all_rendered
            },
            {"aarch64"},
        )

        cache = Path(directory) / "sources"
        payload = b"locked source fixture\n"
        digest = build.hashlib.sha256(payload).hexdigest()
        cache.mkdir()
        (cache / digest).write_bytes(payload)
        fixture_source = {
            "url": "https://example.invalid/source.tar",
            "sha256": digest,
            "packages": ["fixture"],
        }
        locked = build.fetch_stage1_sources([fixture_source], cache, [], offline=True)
        failed += not check(
            "Stage1 offline fetch accepts a verified content-addressed source",
            locked[0]["cache"],
            digest,
        )
        lock_path = build.write_sources_lock(locked, Path(directory) / "sources.lock")
        failed += not check(
            "Stage1 source lock records the verified cache identity",
            json.loads(lock_path.read_text())["sources"][0]["sha256"],
            digest,
        )

        failed += not check(
            "Stage1 source filenames ignore URL query parameters",
            build.source_filename("https://example.invalid/source.tar.gz?download=1"),
            "source.tar.gz",
        )
        failed += not check(
            "Stage1 package ranges include both endpoints",
            build.select_stage1_packages(["one", "two", "three"], "two", "three"),
            ["two", "three"],
        )
        try:
            build.select_stage1_packages(["one", "two"], "two", "one")
        except build.ConfigError as exc:
            range_error = str(exc)
        else:
            range_error = "no error"
        failed += not check(
            "Stage1 rejects reversed package ranges",
            range_error,
            "Stage1 package range is reversed",
        )

        fixture_recipe = Path(directory) / "fixture" / "recipe.toml"
        fixture_recipe.parent.mkdir()
        fixture_recipe.write_text(
            "[package]\n"
            'name = "fixture"\n'
            'version = "1.0"\n'
            'arch = "aarch64"\n\n'
            "[source]\n"
            'url = "https://example.invalid/source.tar?download=1"\n'
            f'sha256 = "{digest}"\n'
        )
        (cache / digest).write_bytes(payload)
        staged = build.stage_recipe_source(fixture_recipe, cache)
        failed += not check(
            "Stage1 stages a verified source under its upstream filename",
            staged.relative_to(fixture_recipe.parent).as_posix(),
            "distfiles/source.tar",
        )
        staged.write_bytes(b"changed build copy")
        failed += not check(
            "Stage1 build copies cannot mutate the content-addressed cache",
            (cache / digest).read_bytes(),
            payload,
        )

        package_lock = build.write_packages_lock(
            [
                {
                    "name": "fixture",
                    "version": "1.0",
                    "release": "1",
                    "arch": "aarch64",
                    "sha256": digest,
                    "recipe_sha256": digest,
                    "artifact": "fixture-1.0-1-aarch64.pkg.tar.zst",
                }
            ],
            Path(directory) / "packages.lock",
        )
        failed += not check(
            "Stage1 package lock records the built architecture",
            json.loads(package_lock.read_text())["packages"][0]["arch"],
            "aarch64",
        )

        (cache / digest).write_bytes(b"corrupt")
        try:
            build.fetch_stage1_sources([fixture_source], cache, [], offline=True)
        except build.ConfigError as exc:
            offline_error = str(exc)
        else:
            offline_error = "no error"
        failed += not check(
            "Stage1 offline fetch rejects a corrupt cache entry",
            "absent or corrupt" in offline_error,
            True,
        )

        linux_output = Path(directory) / "linux-zen" / "recipe.toml"
        build.render_stage1_recipe(
            "linux-zen", build.resolve_architecture("aarch64"), linux_output
        )
        linux_text = linux_output.read_text()
        failed += not check(
            "Stage1 renderer resolves the ARM kernel build architecture",
            "ARCH=arm64" in linux_text,
            True,
        )
        failed += not check(
            "Stage1 renderer selects the ARM kernel image",
            "arch/arm64/boot/Image" in linux_text,
            True,
        )
        failed += not check(
            "Stage1 renderer selects the ARM kernel config",
            "config.aarch64" in linux_text,
            True,
        )
        failed += not check(
            "Stage1 renderer copies recipe helper files",
            (linux_output.parent / "config.aarch64").is_file(),
            True,
        )

        x86_linux_output = Path(directory) / "linux-zen-x86_64" / "recipe.toml"
        build.render_stage1_recipe(
            "linux-zen", build.resolve_architecture("x86_64"), x86_linux_output
        )
        x86_linux_text = x86_linux_output.read_text()
        failed += not check(
            "Stage1 renderer resolves the x86 kernel build architecture",
            "ARCH=x86" in x86_linux_text,
            True,
        )
        failed += not check(
            "Stage1 renderer selects the x86 kernel image",
            "arch/x86/boot/bzImage" in x86_linux_text,
            True,
        )

        gmp_output = Path(directory) / "gmp" / "recipe.toml"
        build.render_stage1_recipe(
            "gmp", build.resolve_architecture("x86_64"), gmp_output
        )
        failed += not check(
            "Stage1 renderer resolves the GNU build triplet",
            "--build=x86_64-pc-linux-gnu" in gmp_output.read_text(),
            True,
        )

        glibc_output = Path(directory) / "glibc" / "recipe.toml"
        build.render_stage1_recipe(
            "glibc", build.resolve_architecture("aarch64"), glibc_output
        )
        failed += not check(
            "Stage1 renderer resolves the dynamic linker capability",
            "so:ld-linux-aarch64.so.1" in glibc_output.read_text(),
            True,
        )

    hardcoded_arches = [
        path.relative_to(REPO).as_posix()
        for path in (REPO / "Stage1" / "recipes").glob("*/recipe.toml")
        if any(name in path.read_text() for name in architectures)
    ]
    failed += not check(
        "canonical Stage1 recipes do not hardcode a target architecture",
        hardcoded_arches,
        [],
    )

    total = 48
    print(f"\n{total - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
