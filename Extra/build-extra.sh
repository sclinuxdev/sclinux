#!/bin/bash
# ==============================================================================
#  sclinux -- Extra package set build engine
#
#  Extra/ holds two kinds of recipe:
#
#    * rebuilds  -- corrected versions of Stage2 recipes, same upstream version
#                   with `release` advanced one step, so the repository can
#                   supersede a deployed package without a version bump.
#    * additions -- packages Stage2 never had (tzdata, busybox, sudo,
#                   device-mapper, icu), several of which Stage2 packages were
#                   silently linking against from the build host.
#
#  This is NOT a full rebuild. Only the packages listed in BUILD_ORDER below
#  are built; everything else in the repository stays as Stage2 produced it.
#
#  Usage:  ./Extra/build-extra.sh [WORKSPACE]
#
#  WORKSPACE defaults to the Extra directory this script lives in. Built
#  packages are copied into $WORKSPACE/repo and indexed there; per-package
#  logs land in $WORKSPACE/logs.
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
WORKSPACE="${1:-${SCRIPT_DIR}}"

RECIPES_DIR="${WORKSPACE}/recipes"
REPO_DIR="${WORKSPACE}/repo"
LOGS_DIR="${WORKSPACE}/logs"

if [ ! -d "${RECIPES_DIR}" ]; then
    echo "No recipes directory at ${RECIPES_DIR}" >&2
    exit 1
fi

mkdir -p "${REPO_DIR}" "${LOGS_DIR}"

log_info()    { echo -e "\033[34;1m::\033[0m \033[1m$*\033[0m"; }
log_success() { echo -e "\033[32;1m✓\033[0m \033[1m$*\033[0m"; }
log_warn()    { echo -e "\033[33;1m!\033[0m \033[1m$*\033[0m"; }
log_error()   { echo -e "\033[31;1m✗\033[0m \033[1m$*\033[0m"; }

echo "================================================================="
echo "        sclinux -- Extra package set (rebuilds + additions)"
echo "================================================================="
log_info "Workspace:   ${WORKSPACE}"
log_info "Recipes:     ${RECIPES_DIR}"
log_info "Repository:  ${REPO_DIR}"
log_info "Logs:        ${LOGS_DIR}"

# ------------------------------------------------------------------------------
# Out-of-band distfiles
#
# sage fetches and verifies exactly one [source] url per recipe. linux-zen's
# prepare phase additionally applies a zen patch that has to be placed by hand,
# so check for it up front rather than letting the kernel fail 40 minutes in.
# ------------------------------------------------------------------------------
ZEN_PATCH="${RECIPES_DIR}/linux-zen/distfiles/linux-v7.1.9-zen1.patch.zst"
ZEN_PATCH_SHA256="bdd01b28231ae1e2b5804c28d0917af44e5f72ee35ff8bdd1406b8c40126d009"

check_zen_patch() {
    if [ ! -f "${ZEN_PATCH}" ]; then
        log_warn "linux-zen: ${ZEN_PATCH} is missing"
        log_warn "           the kernel will build UNPATCHED (prepare tolerates a failed patch)"
        return 0
    fi
    local got
    got="$(sha256sum "${ZEN_PATCH}" | cut -d' ' -f1)"
    if [ "${got}" != "${ZEN_PATCH_SHA256}" ]; then
        log_error "linux-zen: zen patch checksum mismatch"
        log_error "           expected ${ZEN_PATCH_SHA256}"
        log_error "           got      ${got}"
        exit 1
    fi
    log_success "linux-zen: zen patch verified"
}
check_zen_patch

# ------------------------------------------------------------------------------
# Topological build order
#
# Dependencies first. The additions come before the rebuilds that need them:
# xfsprogs links device-mapper and icu, mkinitcpio pulls busybox, and linux-zen
# depends on virtual/initramfs-generator, which mkinitcpio provides.
# ------------------------------------------------------------------------------
BUILD_ORDER=(
    # -- additions: time zones --
    "tzcode"
    "tzdata"

    # -- additions: libraries Stage2 was linking from the build host --
    "icu-libs"
    "icu-dev"
    "icu"
    "device-mapper"
    "device-mapper-dev"

    # -- additions: userland --
    "busybox"
    "sudo"

    # -- rebuilds: merged-/usr install path corrections --
    "util-linux"
    "dosfstools"
    "xfsprogs"
    "inetutils"

    # -- rebuilds: boot chain --
    "mkinitcpio"
    "grub"
    "linux-zen"
)

TOTAL=${#BUILD_ORDER[@]}
CURRENT=0
FAILED_PKGS=()
SUCCESS_PKGS=()

log_info "Starting Extra build queue (${TOTAL} packages)..."

for pkg in "${BUILD_ORDER[@]}"; do
    CURRENT=$((CURRENT + 1))
    RECIPE_PATH="${RECIPES_DIR}/${pkg}"

    if [ ! -d "${RECIPE_PATH}" ]; then
        log_warn "[${CURRENT}/${TOTAL}] Recipe directory not found, skipping: ${pkg}"
        continue
    fi

    echo "-----------------------------------------------------------------"
    log_info "[${CURRENT}/${TOTAL}] Building package: ${pkg} ..."
    echo "-----------------------------------------------------------------"

    if sage build "${RECIPE_PATH}" > "${LOGS_DIR}/${pkg}.log" 2>&1; then
        find "${RECIPE_PATH}" -maxdepth 1 -name "*.pkg.tar.zst" -exec cp -f {} "${REPO_DIR}/" \;
        log_success "[${CURRENT}/${TOTAL}] ${pkg} built -> ${REPO_DIR}"
        SUCCESS_PKGS+=("${pkg}")
    else
        log_error "[${CURRENT}/${TOTAL}] ${pkg} FAILED. Log: ${LOGS_DIR}/${pkg}.log"
        # Keep going: one failure should not hide the state of the other
        # fifteen. The summary below lists everything that failed.
        FAILED_PKGS+=("${pkg}")
    fi
done

# ------------------------------------------------------------------------------
# Repository index
#
# The index and the package archives must come out of the same build -- sage
# resolves against index.toml and then installs the file it names, so a stale
# index paired with fresh packages resolves to archives that are not there.
# ------------------------------------------------------------------------------
echo "================================================================="
if [ ${#SUCCESS_PKGS[@]} -gt 0 ]; then
    log_info "Generating index.toml for the Extra repository..."
    sage repo index "${REPO_DIR}" "core"
else
    log_warn "Nothing built successfully -- skipping index generation."
fi

# ------------------------------------------------------------------------------
# Summary
# ------------------------------------------------------------------------------
echo "================================================================="
log_info "Extra build summary:"
log_info "  - Targets:            ${TOTAL}"
log_info "  - Succeeded:          ${#SUCCESS_PKGS[@]}"
log_info "  - Failed:             ${#FAILED_PKGS[@]}"
log_info "  - Repository:         ${REPO_DIR}"
log_info "  - Archived packages:  $(find "${REPO_DIR}" -maxdepth 1 -name '*.pkg.tar.zst' | wc -l)"

if [ ${#FAILED_PKGS[@]} -eq 0 ]; then
    echo "================================================================="
    log_success "Extra package set built successfully."
    echo "================================================================="
    echo
    log_info "To deploy into an installed root (never test against /):"
    echo "    sage --root /mnt install ${SUCCESS_PKGS[*]}"
    echo
    log_info "Installing linux-zen fires the initramfs trigger, which runs"
    log_info "whichever package provides virtual/initramfs-generator, then the"
    log_info "bootloader trigger via virtual/bootloader. Both need the target"
    log_info "root's /etc/sage/channels.toml to point at ${REPO_DIR}."
else
    echo "================================================================="
    log_error "Some packages failed to build:"
    for f in "${FAILED_PKGS[@]}"; do
        echo "   - ${f} (log: ${LOGS_DIR}/${f}.log)"
    done
    echo "================================================================="
    exit 1
fi
