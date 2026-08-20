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
        "boolean schema versions are rejected",
        config_error("schema_version = true\n[architectures]\n"),
        "schema_version must be the integer 1",
    )
    failed += not check(
        "missing architecture fields are rejected",
        config_error("schema_version = 1\n[architectures.aarch64]\ngnu_triplet = \"a\"\n"),
        "[architectures.aarch64] missing field(s): dynamic_linker, efi_boot_name, "
        "kernel_arch, kernel_image, qemu_machine, qemu_system",
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

    total = 9
    print(f"\n{total - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
