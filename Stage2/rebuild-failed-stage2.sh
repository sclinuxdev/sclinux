#!/usr/bin/env bash
# sclinux stage2 incremental rebuild for failed packages. Must run inside the
# target chroot (e.g. `chroot /mnt /bin/bash`) so the build sees the target's
# installed toolchain and libraries, not the host's.
set -uo pipefail

# Non-login shells (e.g. `chroot /mnt sh -c '...'`) don't source /etc/profile,
# so the active toolchain channel (gcc, etc.) would be missing from PATH.
if [[ -f /etc/profile ]]; then
    # shellcheck disable=SC1091
    source /etc/profile
fi

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
REPO_DIR="${STAGE2_ROOT}/repo"
LOGS_DIR="${STAGE2_ROOT}/logs/incremental"

FAILED_PACKAGES=(
    gcc
    gcc-libs
    btrfs-progs
)

if [[ ! -d "${RECIPES_DIR}" ]]; then
    printf 'error: recipes directory not found: %s\n' "${RECIPES_DIR}" >&2
    exit 1
fi
if ! command -v sage >/dev/null 2>&1; then
    printf 'error: sage is not available in PATH (are you inside the target chroot?)\n' >&2
    exit 1
fi
if ! command -v gcc >/dev/null 2>&1; then
    printf 'error: gcc is not available in PATH (toolchain channel not activated)\n' >&2
    exit 1
fi

mkdir -p "${REPO_DIR}" "${LOGS_DIR}"
failed=()
succeeded=()

printf '==> Incremental stage2 rebuild\n'
printf '    root: %s\n' "${STAGE2_ROOT}"
printf '    packages: %s\n' "${#FAILED_PACKAGES[@]}"

for package in "${FAILED_PACKAGES[@]}"; do
    recipe_dir="${RECIPES_DIR}/${package}"
    log_file="${LOGS_DIR}/${package}.log"
    if [[ ! -f "${recipe_dir}/recipe.toml" ]]; then
        printf '  ! %s: recipe not found\n' "${package}" | tee "${log_file}"
        failed+=("${package}")
        continue
    fi

    printf '==> Building %s\n' "${package}"
    # Keep each package isolated in its own log, while continuing so all failed
    # packages are retried in one host invocation.
    if sage build "${recipe_dir}" >"${log_file}" 2>&1; then
        archives=("${recipe_dir}"/*.pkg.tar.zst)
        copied=0
        for archive in "${archives[@]}"; do
            [[ -f "${archive}" ]] || continue
            cp -f "${archive}" "${REPO_DIR}/"
            copied=$((copied + 1))
        done
        if (( copied == 0 )); then
            printf '  ! %s: build succeeded but no package archive was produced\n' "${package}" | tee -a "${log_file}"
            failed+=("${package}")
        else
            printf '  ✓ %s: rebuilt and copied %d archive(s)\n' "${package}" "${copied}"
            succeeded+=("${package}")
        fi
    else
        printf '  ✗ %s: build failed; see %s\n' "${package}" "${log_file}"
        failed+=("${package}")
    fi
done

printf '==> Regenerating stage2 repository index\n'
if ! sage repo index "${REPO_DIR}" core; then
    printf 'error: failed to regenerate %s/index.toml\n' "${REPO_DIR}" >&2
    exit 1
fi

printf '\n==> Incremental rebuild summary\n'
printf '    succeeded: %d\n' "${#succeeded[@]}"
printf '    failed:    %d\n' "${#failed[@]}"
if ((${#succeeded[@]})); then
    printf '    rebuilt: '
    printf '%s ' "${succeeded[@]}"
    printf '\n'
fi
if ((${#failed[@]})); then
    printf '    still failing: '
    printf '%s ' "${failed[@]}"
    printf '\n'
    exit 1
fi
printf '    repository: %s\n' "${REPO_DIR}"
