#!/bin/bash
# ==============================================================================
#  sclinux 1.0 - Stage 2 Native Self-Hosted Rebuild Engine
#  (Executes inside sclinux chroot or booted native environment)
# ==============================================================================
set -euo pipefail

STAGE2_ROOT="${1:-/stage2}"
if [ ! -d "${STAGE2_ROOT}/recipes" ]; then
    STAGE2_ROOT="/run/media/ir/d507609e-0eea-4910-b65f-aedf9d208a34/stage2"
fi
if [ ! -d "${STAGE2_ROOT}/recipes" ]; then
    STAGE2_ROOT="/mnt/stage2"
fi

RECIPES_DIR="${STAGE2_ROOT}/recipes"
REPO_DIR="${STAGE2_ROOT}/repo"
LOGS_DIR="${STAGE2_ROOT}/logs"

mkdir -p "${REPO_DIR}" "${LOGS_DIR}"

log_info()    { echo -e "\033[34;1m::\033[0m \033[1m$*\033[0m"; }
log_success() { echo -e "\033[32;1m✓\033[0m \033[1m$*\033[0m"; }
log_warn()    { echo -e "\033[33;1m!\033[0m \033[1m$*\033[0m"; }
log_error()   { echo -e "\033[31;1m✗\033[0m \033[1m$*\033[0m"; }

echo "================================================================="
echo "        sclinux 1.0 (Rolling) - Stage 2 Native Full Rebuild"
echo "================================================================="
log_info "Target Workspace:   ${STAGE2_ROOT}"
log_info "Recipes Directory:  ${RECIPES_DIR}"
log_info "Repository Output:  ${REPO_DIR}"
log_info "Build Logs Output:  ${LOGS_DIR}"

# ------------------------------------------------------------------------------
# 1. Release / Revision Strategy Handler
# ------------------------------------------------------------------------------
if [ "${BUMP_RELEASE:-0}" = "1" ]; then
    log_info "Incrementing package revisions to release = \"2\" (Stage 2 Marker)..."
    for r in "${RECIPES_DIR}"/*/recipe.toml; do
        sed -i 's/release = "1"/release = "2"/' "$r"
    done
fi

# ------------------------------------------------------------------------------
# 2. Stage 2 Topological Build Queue (178 Fine-Grained, Split & Toolchain Packages)
# ------------------------------------------------------------------------------
BUILD_ORDER=(
    "linux-zen-headers"
    "glibc"
    "glibc-dev"
    "zlib"
    "zlib-dev"
    "bzip2-libs"
    "bzip2"
    "bzip2-dev"
    "xz-libs"
    "xz"
    "xz-dev"
    "zstd-libs"
    "zstd"
    "zstd-dev"
    "lz4-libs"
    "lz4"
    "lz4-dev"
    "lzo"
    "lzo-dev"
    "m4"
    "file-libs"
    "file"
    "file-dev"
    "gmp"
    "gmp-dev"
    "mpfr"
    "mpfr-dev"
    "mpc"
    "mpc-dev"
    "isl"
    "isl-dev"
    "binutils"
    "gcc"
    "gcc-libs"
    "ncurses-libs"
    "ncurses-dev"
    "ncurses-terminfo"
    "ncurses"
    "readline"
    "readline-dev"
    "bash"
    "attr-libs"
    "attr"
    "attr-dev"
    "acl-libs"
    "acl"
    "acl-dev"
    "libcap-libs"
    "libcap"
    "libcap-dev"
    "libxcrypt"
    "libxcrypt-dev"
    "pkgconf"
    "gperf"
    "libseccomp"
    "libseccomp-dev"
    "pcre2-libs"
    "pcre2"
    "pcre2-dev"
    "openssl-libs"
    "openssl"
    "openssl-dev"
    "diffutils"
    "gawk"
    "grep"
    "sed"
    "patch"
    "make"
    "tar"
    "gzip"
    "findutils"
    "coreutils"
    "curl-libs"
    "curl"
    "curl-dev"
    "expat-libs"
    "expat"
    "expat-dev"
    "libffi"
    "libffi-dev"
    "libelf"
    "libelf-dev"
    "util-linux-libs"
    "util-linux"
    "util-linux-dev"
    "e2fsprogs-libs"
    "e2fsprogs"
    "e2fsprogs-dev"
    "lmdb"
    "lmdb-dev"
    "tomlplusplus"
    "tomlplusplus-dev"
    "libarchive-libs"
    "libarchive"
    "libarchive-dev"
    "libpipeline"
    "libpipeline-dev"
    "libtool"
    "gdbm-libs"
    "gdbm"
    "gdbm-dev"
    "sqlite-libs"
    "sqlite"
    "sqlite-dev"
    "mpdecimal"
    "mpdecimal-dev"
    "tcl-libs"
    "tcl"
    "tcl-dev"
    "expect"
    "less"
    "nano"
    "bc"
    "iana-etc"
    "man-pages"
    "perl-libs"
    "perl"
    "gettext-libs"
    "gettext"
    "gettext-dev"
    "bison"
    "flex"
    "texinfo"
    "autoconf"
    "automake"
    "which"
    "python-libs"
    "python"
    "python-dev"
    "flit-core"
    "packaging"
    "wheel"
    "setuptools"
    "markupsafe"
    "jinja2"
    "ninja"
    "meson"
    "cmake"
    "xmake"
    "kmod-libs"
    "kmod"
    "kmod-dev"
    "groff"
    "man-db"
    "psmisc"
    "inetutils"
    "iproute2"
    "kbd"
    "shadow"
    "systemd-libs"
    "systemd-udev"
    "systemd"
    "systemd-networkd"
    "systemd-resolved"
    "systemd-timesyncd"
    "systemd-dev"
    "procps-ng-libs"
    "procps-ng"
    "procps-ng-dev"
    "dbus-libs"
    "dbus"
    "dbus-dev"
    "libinih"
    "libinih-dev"
    "userspace-rcu"
    "userspace-rcu-dev"
    "xfsprogs"
    "dosfstools"
    "btrfs-progs"
    "dejagnu"
    "os-release"
    "base-files"
    "sage"
    "mkinitcpio"
    "grub"
    "linux-zen"
    "fastfetch"
    "base"
)

TOTAL=${#BUILD_ORDER[@]}
CURRENT=0
FAILED_PKGS=()
SUCCESS_PKGS=()

log_info "Starting Stage 2 Native Build Queue (${TOTAL} packages in total)..."

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
        log_success "[${CURRENT}/${TOTAL}] ${pkg} built successfully -> archived to ${REPO_DIR}"
        SUCCESS_PKGS+=("${pkg}")
    else
        log_error "[${CURRENT}/${TOTAL}] ${pkg} build failed! Log: ${LOGS_DIR}/${pkg}.log"
        FAILED_PKGS+=("${pkg}")
    fi
done

# ------------------------------------------------------------------------------
# 3. Generate Stage 2 Repository Index
# ------------------------------------------------------------------------------
echo "================================================================="
log_info "Generating index.toml for Stage 2 repository..."
sage repo index "${REPO_DIR}" "core"

# ------------------------------------------------------------------------------
# 4. Build Summary & Diagnostics Report
# ------------------------------------------------------------------------------
echo "================================================================="
log_info "Stage 2 Build Summary:"
log_info "  - Total Targets:      ${TOTAL}"
log_info "  - Succeeded:          ${#SUCCESS_PKGS[@]}"
log_info "  - Failed:             ${#FAILED_PKGS[@]}"
log_info "  - Repository:         ${REPO_DIR}"
log_info "  - Archived Packages:  $(ls -1 "${REPO_DIR}"/*.pkg.tar.zst 2>/dev/null | wc -l)"

if [ ${#FAILED_PKGS[@]} -eq 0 ]; then
    echo "================================================================="
    log_success "🎉 Stage 2 Full Native Rebuild Completed Successfully (100% Pass)!"
    echo "================================================================="
else
    echo "================================================================="
    log_error "⚠️  Some packages failed to build. Failed list:"
    for f in "${FAILED_PKGS[@]}"; do
        echo "   - ${f} (Log: ${LOGS_DIR}/${f}.log)"
    done
    echo "================================================================="
fi
