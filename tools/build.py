#!/usr/bin/env python3
"""Shared entry point for ShenChen Linux architecture-aware builds."""

from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
import tomllib
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = REPO / "config" / "architectures.toml"
ARCH_NAME = re.compile(r"^[a-z0-9_]+$")
ARCH_FIELDS = (
    "gnu_triplet",
    "kernel_arch",
    "kernel_image",
    "efi_boot_name",
    "dynamic_linker",
    "qemu_system",
    "qemu_machine",
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


def shell_environment(architecture: dict[str, str]) -> str:
    variables = {f"SC_{key.upper()}": value for key, value in architecture.items()}
    return "\n".join(f"{key}={shlex.quote(value)}" for key, value in variables.items())


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
    return 0


if __name__ == "__main__":
    sys.exit(main())
