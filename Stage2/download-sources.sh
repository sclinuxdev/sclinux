#!/bin/bash
# sclinux stage2 host-side source completion
# Downloads every recipe source into its recipe-local distfiles directory.
set -euo pipefail

STAGE2_ROOT="${1:-}"
if [[ -z "${STAGE2_ROOT}" ]]; then
    for candidate in \
        /run/media/ir/d507609e-0eea-4910-b65f-aedf9d208a34/stage2 \
        /stage2 \
        /mnt/stage2; do
        if [[ -d "${candidate}/recipes" ]]; then
            STAGE2_ROOT="${candidate}"
            break
        fi
    done
fi
STAGE2_ROOT="${STAGE2_ROOT:-/mnt/stage2}"
RECIPES_DIR="${STAGE2_ROOT}/recipes"

if [[ ! -d "${RECIPES_DIR}" ]]; then
    printf 'error: recipes directory not found: %s\n' "${RECIPES_DIR}" >&2
    exit 1
fi

printf '==> Completing stage2 source cache on the host\n'
printf '    recipes: %s\n' "${RECIPES_DIR}"

# Complete the official Bash patchset separately because it is not represented
# as a source archive URL in recipe.toml.
BASH_PATCH_FILE="${RECIPES_DIR}/bash/bash-5.3.15.patch"
if [[ ! -s "${BASH_PATCH_FILE}" ]]; then
    patch_tmp="${BASH_PATCH_FILE}.tmp.$$"
    rm -f "${patch_tmp}"
    trap 'rm -f "${patch_tmp}"' EXIT
    mkdir -p "$(dirname "${BASH_PATCH_FILE}")"
    printf '==> Downloading Bash 5.3 patches 001..015\n'
    for i in $(seq -w 1 15); do
        url="https://ftp.gnu.org/gnu/bash/bash-5.3-patches/bash53-${i}"
        curl --fail --location --retry 3 --connect-timeout 15 \
            --silent --show-error "${url}" >> "${patch_tmp}"
        printf '\n' >> "${patch_tmp}"
    done
    [[ -s "${patch_tmp}" ]]
    mv -f "${patch_tmp}" "${BASH_PATCH_FILE}"
    trap - EXIT
fi

# tomllib reads the actual recipe schema instead of relying on a fragile regex.
# Downloads run in parallel; each archive is written to a .part file and moved
# into place only after an optional SHA256 check succeeds.
python3 - "${RECIPES_DIR}" <<'PYEOF'
from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import threading
import tomllib
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import unquote, urlparse

recipes_dir = Path(sys.argv[1])
jobs = max(1, int(os.environ.get("SOURCE_JOBS", "8")))
axel_threads = max(1, int(os.environ.get("AXEL_THREADS", "16")))
print_lock = threading.Lock()


def log(message: str) -> None:
    with print_lock:
        print(message, flush=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source_filename(package: str, url: str) -> str:
    name = Path(unquote(urlparse(url).path)).name
    return name or f"{package}-source.tar.gz"


def download_one(item: tuple[str, str, str, Path]) -> tuple[str, str]:
    package, url, expected, destination = item
    destination.parent.mkdir(parents=True, exist_ok=True)

    if destination.is_file() and destination.stat().st_size > 0:
        if expected and sha256(destination) != expected:
            log(f"  ! {package}: checksum mismatch, replacing {destination.name}")
            destination.unlink()
        else:
            return package, "cached"

    if url.startswith("file://"):
        source = Path(unquote(urlparse(url).path))
        if not source.is_file():
            return package, f"missing local source: {source}"
        shutil.copyfile(source, destination)
    else:
        partial = destination.with_name(destination.name + f".part.{os.getpid()}")
        partial.unlink(missing_ok=True)
        try:
            axel = shutil.which("axel")
            if axel:
                command = [axel, f"-n{axel_threads}", "-a", "-o", str(partial), url]
                result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            else:
                result = subprocess.CompletedProcess([], 1, b"axel unavailable")
            if result.returncode != 0 or not partial.is_file() or partial.stat().st_size == 0:
                curl = shutil.which("curl")
                if not curl:
                    return package, "neither axel nor curl is installed"
                command = [
                    curl, "--fail", "--location", "--retry", "3",
                    "--connect-timeout", "15", "--silent", "--show-error",
                    "--output", str(partial), url,
                ]
                result = subprocess.run(command, stderr=subprocess.PIPE)
                if result.returncode != 0:
                    detail = result.stderr.decode(errors="replace").strip()
                    return package, f"download failed: {detail or result.returncode}"
            if not partial.is_file() or partial.stat().st_size == 0:
                return package, "download produced an empty file"
            partial.replace(destination)
        finally:
            partial.unlink(missing_ok=True)

    if expected:
        actual = sha256(destination)
        if actual != expected:
            destination.unlink(missing_ok=True)
            return package, f"SHA256 mismatch (expected {expected}, got {actual})"
    return package, "downloaded"


items: list[tuple[str, str, str, Path]] = []
skipped = 0
for recipe_dir in sorted(p for p in recipes_dir.iterdir() if p.is_dir()):
    recipe_file = recipe_dir / "recipe.toml"
    if not recipe_file.is_file():
        continue
    try:
        with recipe_file.open("rb") as stream:
            data = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        print(f"error: {recipe_file}: {exc}", file=sys.stderr)
        sys.exit(1)

    source = data.get("source") or {}
    url = str(source.get("url", "")).strip()
    if not url or url.startswith(("git+", "local:")):
        skipped += 1
        continue
    package = str((data.get("package") or {}).get("name", recipe_dir.name))
    expected = str(source.get("sha256", "")).strip().lower()
    filename = source_filename(package, url)
    items.append((package, url, expected, recipe_dir / "distfiles" / filename))

print(f"==> Found {len(items)} source archives ({skipped} recipes skipped)")
failures: list[str] = []
with ThreadPoolExecutor(max_workers=jobs) as executor:
    futures = [executor.submit(download_one, item) for item in items]
    for future in as_completed(futures):
        package, status = future.result()
        if status in {"cached", "downloaded"}:
            log(f"  ✓ {package}: {status}")
        else:
            log(f"  ✗ {package}: {status}")
            failures.append(f"{package}: {status}")

if failures:
    print("\nsource completion failed:", file=sys.stderr)
    for failure in sorted(failures):
        print(f"  - {failure}", file=sys.stderr)
    sys.exit(1)
print("==> All recipe sources are present and verified")
PYEOF

printf '==> Source cache ready: %s\n' "${RECIPES_DIR}"
