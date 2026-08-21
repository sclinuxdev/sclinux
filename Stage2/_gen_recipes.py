#!/usr/bin/env python3
"""Generate stage2 recipe.toml files: split policy, channels, deps.

Split policy
------------
1. Pure library (no real CLI product, or only incidental helpers):
   ``name`` (SONAME runtime, plus any tiny bins) + ``name-dev`` (headers / pc /
   unversioned .so). The runtime package is NOT named ``-libs``.
   Examples: zlib, lzo, gmp, mpfr, mpc, isl, readline, libffi, libelf,
   libxcrypt, libseccomp, libpipeline, tomlplusplus, mpdecimal, libinih,
   userspace-rcu, lmdb.

2. Library + independent CLI product: 3-way
   ``name`` (programs) / ``name-libs`` (SONAME) / ``name-dev``.
   Examples: xz, zstd, lz4, bzip2, file, curl, openssl, sqlite, kmod,
   util-linux, e2fsprogs, gettext, libarchive, libcap, attr, acl, libtool,
   pcre2, gdbm, expat, procps-ng, dbus, tcl, python, perl.

3. Large multi-function components: split by *function*, not just CLI/libs/dev.
   systemd -> libs, udev, core PID1/journal/logind, networkd, resolved, timesyncd, dev.

4. Build tools that are not independent runtime libraries stay in one package.
   libtool includes libltdl, headers, macros and its CLI; autoconf and automake
   likewise do not get artificial ``-libs``/``-dev`` siblings.

5. Virtual interfaces and meta packages use stable capability names.
   Variant packages provide both their concrete name and the selected virtual
   capability (for example ``linux``/``virtual/linux`` and
   ``linux-headers``/``virtual/linux-headers``).

4. Kernel UAPI headers are bound to the kernel package version *and* variant:
   ``linux-zen-headers`` matches ``linux-zen`` (same tarball / version) and
   provides ``linux-headers``.
"""
from __future__ import annotations

import os
import shutil

ROOT = "/mnt/stage2/recipes"

STRIP_CLI = [
    "rm -rf $DESTDIR/usr/include $DESTDIR/usr/lib $DESTDIR/usr/lib64 2>/dev/null || true",
]
STRIP_LIBS = [
    "rm -rf $DESTDIR/usr/include $DESTDIR/usr/bin $DESTDIR/usr/sbin $DESTDIR/usr/share $DESTDIR/etc $DESTDIR/var $DESTDIR/usr/lib/pkgconfig $DESTDIR/usr/lib/cmake 2>/dev/null || true",
    "find $DESTDIR/usr/lib $DESTDIR/usr/lib64 -type f \\( -name '*.a' -o -name '*.la' \\) -delete 2>/dev/null || true",
    "find $DESTDIR/usr/lib $DESTDIR/usr/lib64 \\( -type l -o -type f \\) -name '*.so' ! -name '*.so.*' -delete 2>/dev/null || true",
]
STRIP_DEV = [
    "rm -rf $DESTDIR/usr/bin $DESTDIR/usr/sbin $DESTDIR/usr/share $DESTDIR/etc $DESTDIR/var 2>/dev/null || true",
    "find $DESTDIR/usr/lib $DESTDIR/usr/lib64 -type f -name '*.so.*' -delete 2>/dev/null || true",
]
# Pure-lib runtime that also ships incidental CLI helpers (e.g. lmdb mdb_*).
STRIP_LIBS_KEEP_CLI = [
    "rm -rf $DESTDIR/usr/include $DESTDIR/usr/lib/pkgconfig $DESTDIR/usr/lib/cmake 2>/dev/null || true",
    "find $DESTDIR/usr/lib $DESTDIR/usr/lib64 -type f \\( -name '*.a' -o -name '*.la' \\) -delete 2>/dev/null || true",
    "find $DESTDIR/usr/lib $DESTDIR/usr/lib64 \\( -type l -o -type f \\) -name '*.so' ! -name '*.so.*' -delete 2>/dev/null || true",
]


def sh_quote(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


def sd_keep_install(globs: list[str]) -> list[str]:
    """One install command: ninja-install then keep only matching DESTDIR paths.

    sage runs each install[] entry in a fresh shell, so KEEP/prune must be
    a single command. Globs are expanded relative to DESTDIR.
    """
    glist = " ".join(sh_quote(g) for g in globs)
    return [
        "set -e; cd build && DESTDIR=$DESTDIR ninja install; "
        "KEEP=$(mktemp -d); "
        "( cd \"$DESTDIR\" && for g in " + glist + "; do "
        "for p in $g; do [ -e \"$p\" ] || continue; "
        "tar -cf - \"$p\" | tar -C \"$KEEP\" -xf -; "
        "done; done ); "
        "find \"$DESTDIR\" -mindepth 1 -maxdepth 1 -exec rm -rf {} +; "
        "mkdir -p \"$DESTDIR\"; "
        "if [ -z \"$(ls -A \"$KEEP\" 2>/dev/null)\" ]; then "
        "echo 'ERROR: systemd split keep-set empty' >&2; rm -rf \"$KEEP\"; exit 1; fi; "
        "cp -a \"$KEEP\"/. \"$DESTDIR\"/; rm -rf \"$KEEP\""
    ]


def sd_core_install(remove_globs: list[str]) -> list[str]:
    """Install systemd's core manager, tools and boot unit graph.

    The function split is removed after installation, then the required core
    paths are checked so the core package can never be emitted with only
    libraries or split-provider files.
    """
    rms = "; ".join(f"rm -rf $DESTDIR/{g}" for g in remove_globs)
    return [
        "set -e; cd build && DESTDIR=$DESTDIR ninja install; "
        + rms + "; "
        "rm -rf $DESTDIR/usr/include $DESTDIR/usr/lib/pkgconfig "
        "$DESTDIR/usr/share/pkgconfig $DESTDIR/usr/share/doc 2>/dev/null || true; "
        "rm -f $DESTDIR/usr/lib/libsystemd.so $DESTDIR/usr/lib/libudev.so; "
        "for p in usr/lib/systemd/systemd usr/bin/systemctl usr/bin/journalctl "
        "usr/lib/systemd/system/basic.target usr/lib/systemd/system/sysinit.target "
        "usr/lib/systemd/system/default.target; do "
        "[ -e \"$DESTDIR/$p\" ] || { echo \"ERROR: systemd core missing $p\" >&2; exit 1; }; "
        "done"
    ]


def toml_str(s: str) -> str:
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def qlist(items: list[str], indent: str = "") -> str:
    if not items:
        return "[]"
    inner = ",\n".join(f"{indent}    {toml_str(x)}" for x in items)
    return "[\n" + inner + "\n" + indent + "]"


def emit(pkg: dict) -> str:
    lines = ["schema_version = 1", "", "[package]"]
    for k in ("name", "version", "release", "description", "license", "channel"):
        lines.append(f'{k} = "{pkg[k]}"')
    if pkg.get("url"):
        lines += ["", "[source]", f'url = "{pkg["url"]}"', f'sha256 = "{pkg.get("sha256", "")}"']
    lines += ["", f"dependencies = {qlist(pkg.get('deps', []))}"]
    if "bdeps" in pkg:
        lines += ["", f"build_dependencies = {qlist(pkg.get('bdeps', []))}"]
    lines += ["", f"provides = {qlist(pkg.get('provides', [pkg['name']]))}"]
    for phase in ("prepare", "build", "install"):
        cmds = pkg.get(phase, [])
        lines += ["", f"{phase} = {qlist(cmds)}"]
    lines.append("")
    return "\n".join(lines)


def write_pkg(pkg: dict) -> None:
    d = os.path.join(ROOT, pkg["name"])
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "recipe.toml")
    with open(path, "w") as f:
        f.write(emit(pkg))
    print("wrote", path)


def copy_dist(src_pkg: str, *dst_pkgs: str) -> None:
    src = os.path.join(ROOT, src_pkg, "distfiles")
    if not os.path.isdir(src):
        return
    for dst_pkg in dst_pkgs:
        dst = os.path.join(ROOT, dst_pkg, "distfiles")
        os.makedirs(dst, exist_ok=True)
        for fn in os.listdir(src):
            s = os.path.join(src, fn)
            d = os.path.join(dst, fn)
            if os.path.isfile(s) and not os.path.isfile(d):
                shutil.copy2(s, d)


# ---------------------------------------------------------------------------
# Shared snippets
# ---------------------------------------------------------------------------
AT_BUILD = [
    "./configure --prefix=/usr --disable-static",
    "make -j$(nproc)",
]
AT_INSTALL = ["make install DESTDIR=$DESTDIR"]

# ===========================================================================
PKGS: list[dict] = []

def P(**kw):
    kw.setdefault("release", "2")
    kw.setdefault("channel", "system")
    kw.setdefault("prepare", [])
    PKGS.append(kw)

# ---------- zlib (library-only: runtime + dev) ----------
P(name="zlib", version="1.3.2", description="zlib compression runtime library",
  license="Zlib", url="https://zlib.net/zlib-1.3.2.tar.gz",
  deps=["glibc >= 2.44"], provides=["zlib", "so:libz.so.1"],
  build=["./configure --prefix=/usr", "make -j$(nproc)"],
  install=AT_INSTALL + STRIP_LIBS)
P(name="zlib-dev", version="1.3.2", description="zlib headers, pkg-config and linker symlink",
  license="Zlib", url="https://zlib.net/zlib-1.3.2.tar.gz",
  deps=["zlib >= 1.3.2"], provides=["zlib-dev"],
  build=["./configure --prefix=/usr", "make -j$(nproc)"],
  install=AT_INSTALL + STRIP_DEV)

# ---------- bzip2 ----------
P(name="bzip2-libs", version="1.0.8", description="libbz2 shared runtime library",
  license="bzip2-1.0.6", url="https://sourceware.org/pub/bzip2/bzip2-1.0.8.tar.gz",
  deps=["glibc >= 2.44"], provides=["bzip2-libs", "so:libbz2.so.1", "so:libbz2.so.1.0"],
  build=["make -f Makefile-libbz2_so -j$(nproc)"],
  install=[
      "mkdir -p $DESTDIR/usr/lib",
      "cp -a libbz2.so.1.0.8 $DESTDIR/usr/lib/",
      "ln -sf libbz2.so.1.0.8 $DESTDIR/usr/lib/libbz2.so.1.0",
      "ln -sf libbz2.so.1.0.8 $DESTDIR/usr/lib/libbz2.so.1",
  ])
P(name="bzip2", version="1.0.8", description="bzip2/bunzip2/bzcat compression tools",
  license="bzip2-1.0.6", url="https://sourceware.org/pub/bzip2/bzip2-1.0.8.tar.gz",
  deps=["bzip2-libs >= 1.0.8"], provides=["bzip2"],
  build=["make -j$(nproc)"],
  install=[
      "make PREFIX=$DESTDIR/usr install",
      "rm -f $DESTDIR/usr/lib/libbz2.a $DESTDIR/usr/lib/libbz2.so* $DESTDIR/usr/include/bzlib.h",
      "rmdir $DESTDIR/usr/lib $DESTDIR/usr/include 2>/dev/null || true",
  ])
P(name="bzip2-dev", version="1.0.8", description="libbz2 headers, static library and linker symlink",
  license="bzip2-1.0.6", url="https://sourceware.org/pub/bzip2/bzip2-1.0.8.tar.gz",
  deps=["bzip2-libs >= 1.0.8"], provides=["bzip2-dev"],
  build=["make -f Makefile-libbz2_so -j$(nproc)", "make libbz2.a -j$(nproc)"],
  install=[
      "mkdir -p $DESTDIR/usr/include $DESTDIR/usr/lib",
      "cp -a bzlib.h $DESTDIR/usr/include/",
      "cp -a libbz2.a $DESTDIR/usr/lib/",
      "ln -sf libbz2.so.1.0.8 $DESTDIR/usr/lib/libbz2.so",
  ])

# ---------- xz ----------
XZ_URL = "https://tukaani.org/xz/xz-5.8.3.tar.xz"
P(name="xz-libs", version="5.8.3", description="liblzma shared runtime library",
  license="0BSD AND LGPL-2.1-or-later AND GPL-2.0-or-later", url=XZ_URL,
  deps=["glibc >= 2.44"], provides=["xz-libs", "so:liblzma.so.5"],
  build=["./configure --prefix=/usr --disable-static --disable-xz --disable-xzdec --disable-lzmadec --disable-lzmainfo --disable-lzma-links --disable-scripts --disable-doc", "make -j$(nproc)"],
  install=AT_INSTALL + STRIP_LIBS)
P(name="xz", version="5.8.3", description="XZ-format compression utilities",
  license="GPL-2.0-or-later", url=XZ_URL,
  deps=["xz-libs >= 5.8.3"], provides=["xz"],
  build=["./configure --prefix=/usr --disable-static --docdir=/usr/share/doc/xz-5.8.3", "make -j$(nproc)"],
  install=AT_INSTALL + STRIP_CLI)
P(name="xz-dev", version="5.8.3", description="liblzma headers, pkg-config and linker symlink",
  license="0BSD AND LGPL-2.1-or-later", url=XZ_URL,
  deps=["xz-libs >= 5.8.3"], provides=["xz-dev"],
  build=AT_BUILD, install=AT_INSTALL + STRIP_DEV)

# ---------- zstd ----------
ZSTD_URL = "https://github.com/facebook/zstd/releases/download/v1.5.7/zstd-1.5.7.tar.gz"
P(name="zstd-libs", version="1.5.7", description="libzstd shared runtime library",
  license="BSD-3-Clause OR GPL-2.0-only", url=ZSTD_URL,
  deps=["glibc >= 2.44"], provides=["zstd-libs", "so:libzstd.so.1"],
  build=["make -C lib prefix=/usr -j$(nproc)"],
  install=["make -C lib prefix=/usr install DESTDIR=$DESTDIR"] + STRIP_LIBS)
P(name="zstd", version="1.5.7", description="Zstandard compression tool",
  license="BSD-3-Clause OR GPL-2.0-only", url=ZSTD_URL,
  deps=["zstd-libs >= 1.5.7"], provides=["zstd"],
  build=["make prefix=/usr -j$(nproc)"],
  install=["make prefix=/usr install DESTDIR=$DESTDIR"] + STRIP_CLI)
P(name="zstd-dev", version="1.5.7", description="libzstd headers, pkg-config and linker symlink",
  license="BSD-3-Clause OR GPL-2.0-only", url=ZSTD_URL,
  deps=["zstd-libs >= 1.5.7"], provides=["zstd-dev"],
  build=["make -j$(nproc) -C lib"],
  install=["make -C lib install DESTDIR=$DESTDIR PREFIX=/usr"] + STRIP_DEV)

# ---------- lz4 ----------
LZ4_URL = "https://github.com/lz4/lz4/archive/refs/tags/v1.10.0.tar.gz"
P(name="lz4-libs", version="1.10.0", description="liblz4 shared runtime library",
  license="BSD-2-Clause", url=LZ4_URL,
  deps=["glibc >= 2.44"], provides=["lz4-libs", "so:liblz4.so.1"],
  build=["make -C lib prefix=/usr -j$(nproc)"],
  install=["make -C lib prefix=/usr install DESTDIR=$DESTDIR"] + STRIP_LIBS)
P(name="lz4", version="1.10.0", description="lz4 extremely fast compression tool",
  license="BSD-2-Clause", url=LZ4_URL,
  deps=["lz4-libs >= 1.10.0"], provides=["lz4"],
  build=["make prefix=/usr -j$(nproc)"],
  install=["make prefix=/usr install DESTDIR=$DESTDIR"] + STRIP_CLI)
P(name="lz4-dev", version="1.10.0", description="liblz4 headers, pkg-config and linker symlink",
  license="BSD-2-Clause", url=LZ4_URL,
  deps=["lz4-libs >= 1.10.0"], provides=["lz4-dev"],
  build=["make -j$(nproc) -C lib"],
  install=["make -C lib install DESTDIR=$DESTDIR PREFIX=/usr"] + STRIP_DEV)

# ---------- lzo (new) ----------
P(name="lzo", version="2.10", description="LZO real-time data compression runtime library",
  license="GPL-2.0-or-later", url="https://www.oberhumer.com/opensource/lzo/download/lzo-2.10.tar.gz",
  sha256="c0f892943208266f9b6543b3ae308fab6284c5c90e627931446fb49b4221a072",
  deps=["glibc >= 2.44"], provides=["lzo", "so:liblzo2.so.2"],
  build=["./configure --prefix=/usr --enable-shared --disable-static", "make -j$(nproc)"],
  install=AT_INSTALL + STRIP_LIBS)
P(name="lzo-dev", version="2.10", description="LZO headers, pkg-config and linker symlink",
  license="GPL-2.0-or-later", url="https://www.oberhumer.com/opensource/lzo/download/lzo-2.10.tar.gz",
  sha256="c0f892943208266f9b6543b3ae308fab6284c5c90e627931446fb49b4221a072",
  deps=["lzo >= 2.10"], provides=["lzo-dev"],
  build=["./configure --prefix=/usr --enable-shared --disable-static", "make -j$(nproc)"],
  install=AT_INSTALL + STRIP_DEV)

# ---------- file ----------
FILE_URL = "https://astron.com/pub/file/file-5.48.tar.gz"
P(name="file-libs", version="5.48", description="libmagic shared runtime library",
  license="BSD-2-Clause", url=FILE_URL,
  deps=["zlib >= 1.3.2", "bzip2-libs >= 1.0.8", "xz-libs >= 5.8.3"],
  bdeps=["zlib-dev >= 1.3.2", "bzip2-dev >= 1.0.8", "xz-dev >= 5.8.3"],
  provides=["file-libs", "so:libmagic.so.1"],
  build=AT_BUILD, install=AT_INSTALL + STRIP_LIBS)
P(name="file", version="5.48", description="File type identification utility",
  license="BSD-2-Clause", url=FILE_URL,
  deps=["file-libs >= 5.48"], provides=["file"],
  build=AT_BUILD, install=AT_INSTALL + [
      "rm -rf $DESTDIR/usr/lib/libmagic* $DESTDIR/usr/include/magic.h $DESTDIR/usr/lib/pkgconfig/libmagic.pc $DESTDIR/usr/include",
  ])
P(name="file-dev", version="5.48", description="libmagic headers, pkg-config and linker symlink",
  license="BSD-2-Clause", url=FILE_URL,
  deps=["file-libs >= 5.48"], provides=["file-dev"],
  build=AT_BUILD, install=AT_INSTALL + STRIP_DEV)

# ---------- gmp / mpfr / mpc / isl ----------
P(name="gmp", version="6.3.0", description="GNU MP runtime library",
  license="LGPL-3.0-or-later OR GPL-2.0-or-later",
  url="https://mirrors.kernel.org/gnu/gmp/gmp-6.3.0.tar.xz",
  deps=["glibc >= 2.44"], provides=["gmp", "so:libgmp.so.10", "so:libgmpxx.so.4"],
  build=['CFLAGS="${CFLAGS:--O2} -std=gnu17" ./configure --prefix=/usr --enable-cxx --disable-static --build=x86_64-pc-linux-gnu', "make -j$(nproc)"],
  install=AT_INSTALL + STRIP_LIBS)
P(name="gmp-dev", version="6.3.0", description="GNU MP headers, pkg-config and linker symlink",
  license="LGPL-3.0-or-later OR GPL-2.0-or-later",
  url="https://mirrors.kernel.org/gnu/gmp/gmp-6.3.0.tar.xz",
  deps=["gmp >= 6.3.0"], provides=["gmp-dev"],
  build=['CFLAGS="${CFLAGS:--O2} -std=gnu17" ./configure --prefix=/usr --enable-cxx --disable-static --build=x86_64-pc-linux-gnu', "make -j$(nproc)"],
  install=AT_INSTALL + STRIP_DEV)
P(name="mpfr", version="4.2.2", description="MPFR runtime library",
  license="LGPL-3.0-or-later", url="https://mirrors.kernel.org/gnu/mpfr/mpfr-4.2.2.tar.xz",
  deps=["gmp >= 6.3.0"], provides=["mpfr", "so:libmpfr.so.6"],
  build=["./configure --prefix=/usr --disable-static --enable-thread-safe", "make -j$(nproc)"],
  install=AT_INSTALL + STRIP_LIBS)
P(name="mpfr-dev", version="4.2.2", description="MPFR headers, pkg-config and linker symlink",
  license="LGPL-3.0-or-later", url="https://mirrors.kernel.org/gnu/mpfr/mpfr-4.2.2.tar.xz",
  deps=["mpfr >= 4.2.2", "gmp-dev >= 6.3.0"], provides=["mpfr-dev"],
  build=["./configure --prefix=/usr --disable-static --enable-thread-safe", "make -j$(nproc)"],
  install=AT_INSTALL + STRIP_DEV)
P(name="mpc", version="1.4.1", description="MPC runtime library",
  license="LGPL-3.0-or-later", url="https://mirrors.kernel.org/gnu/mpc/mpc-1.4.1.tar.xz",
  deps=["gmp >= 6.3.0", "mpfr >= 4.2.2"], provides=["mpc", "so:libmpc.so.3"],
  build=AT_BUILD, install=AT_INSTALL + STRIP_LIBS)
P(name="mpc-dev", version="1.4.1", description="MPC headers, pkg-config and linker symlink",
  license="LGPL-3.0-or-later", url="https://mirrors.kernel.org/gnu/mpc/mpc-1.4.1.tar.xz",
  deps=["mpc >= 1.4.1", "gmp-dev >= 6.3.0", "mpfr-dev >= 4.2.2"], provides=["mpc-dev"],
  build=AT_BUILD, install=AT_INSTALL + STRIP_DEV)
P(name="isl", version="0.27", description="Integer Set Library runtime (GCC Graphite)",
  license="MIT", url="https://libisl.sourceforge.io/isl-0.27.tar.xz",
  sha256="6d8babb59e7b672e8cb7870e874f3f7b813b6e00e6af3f8b04f7579965643d5c",
  deps=["gmp >= 6.3.0"], provides=["isl", "so:libisl.so.23"],
  build=AT_BUILD, install=AT_INSTALL + STRIP_LIBS)
P(name="isl-dev", version="0.27", description="ISL headers, pkg-config and linker symlink",
  license="MIT", url="https://libisl.sourceforge.io/isl-0.27.tar.xz",
  sha256="6d8babb59e7b672e8cb7870e874f3f7b813b6e00e6af3f8b04f7579965643d5c",
  deps=["isl >= 0.27", "gmp-dev >= 6.3.0"], provides=["isl-dev"],
  build=AT_BUILD, install=AT_INSTALL + STRIP_DEV)

# ---------- ncurses ----------
NC_URL = "https://invisible-island.net/archives/ncurses/ncurses-6.6.tar.gz"
NC_CFG = "./configure --prefix=/usr --mandir=/usr/share/man --with-shared --without-debug --without-ada --enable-widec --enable-pc-files --with-pkg-config-libdir=/usr/lib/pkgconfig"
P(name="ncurses-libs", version="6.6", description="ncurses widechar shared runtime libraries",
  license="MIT", url=NC_URL, deps=["glibc >= 2.44"],
  provides=["ncurses-libs", "so:libncursesw.so.6", "so:libncurses.so.6", "so:libtinfo.so.6",
            "so:libtinfow.so.6", "so:libformw.so.6", "so:libmenuw.so.6", "so:libpanelw.so.6"],
  build=[NC_CFG + " --without-progs --without-tests --without-manpages", "make -j$(nproc)"],
  install=AT_INSTALL + STRIP_LIBS + [
      "ln -sf libncursesw.so.6 $DESTDIR/usr/lib/libncurses.so.6",
      "ln -sf libncursesw.so.6 $DESTDIR/usr/lib/libtinfo.so.6",
      "ln -sf libncursesw.so.6 $DESTDIR/usr/lib/libtinfow.so.6",
  ])
P(name="ncurses-dev", version="6.6", description="ncurses headers, pkg-config, linker scripts and unversioned .so",
  license="MIT", url=NC_URL, deps=["ncurses-libs >= 6.6"], provides=["ncurses-dev"],
  build=[NC_CFG + " --without-progs --without-tests --without-manpages", "make -j$(nproc)"],
  install=AT_INSTALL + STRIP_DEV + [
      'for lib in ncurses form panel menu ; do echo "INPUT(-l${lib}w)" > $DESTDIR/usr/lib/lib${lib}.so; ln -sf ${lib}w.pc $DESTDIR/usr/lib/pkgconfig/${lib}.pc 2>/dev/null || true; done',
      'echo "INPUT(-lncursesw)" > $DESTDIR/usr/lib/libcursesw.so',
      'echo "INPUT(-lncursesw)" > $DESTDIR/usr/lib/libtinfo.so',
      'echo "INPUT(-lncursesw)" > $DESTDIR/usr/lib/libtinfow.so',
      "ln -sf libncurses.so $DESTDIR/usr/lib/libcurses.so 2>/dev/null || true",
  ])
P(name="ncurses-terminfo", version="6.6", description="Terminal terminfo database",
  license="MIT", url=NC_URL, deps=[], provides=["ncurses-terminfo"],
  build=[NC_CFG, "make -j$(nproc)"],
  install=AT_INSTALL + [
      "rm -rf $DESTDIR/usr/bin $DESTDIR/usr/lib $DESTDIR/usr/include $DESTDIR/usr/share/man $DESTDIR/usr/share/tabset $DESTDIR/usr/share/doc $DESTDIR/usr/share/pkgconfig $DESTDIR/usr/lib/pkgconfig",
  ])
P(name="ncurses", version="6.6", description="ncurses terminal programs (clear, tic, infocmp, tput, toe, tabs)",
  license="MIT", url=NC_URL,
  deps=["ncurses-libs >= 6.6", "ncurses-terminfo >= 6.6"], provides=["ncurses"],
  build=[NC_CFG, "make -j$(nproc)"],
  install=AT_INSTALL + STRIP_CLI + ["rm -rf $DESTDIR/usr/share/terminfo"])

# ---------- readline (library-only) ----------
RL_URL = "https://mirrors.kernel.org/gnu/readline/readline-8.3.tar.gz"
P(name="readline", version="8.3", description="GNU Readline shared runtime library",
  license="GPL-3.0-or-later", url=RL_URL,
  deps=["ncurses-libs >= 6.6"], bdeps=["ncurses-dev >= 6.6"],
  provides=["readline", "so:libreadline.so.8", "so:libhistory.so.8"],
  build=['./configure --prefix=/usr --disable-static --with-curses', 'make -j$(nproc) SHLIB_LIBS="-lncursesw"'],
  install=['make SHLIB_LIBS="-lncursesw" install DESTDIR=$DESTDIR'] + STRIP_LIBS)
P(name="readline-dev", version="8.3", description="GNU Readline headers, pkg-config and linker symlink",
  license="GPL-3.0-or-later", url=RL_URL,
  deps=["readline >= 8.3", "ncurses-dev >= 6.6"], provides=["readline-dev"],
  build=['./configure --prefix=/usr --disable-static --with-curses', 'make -j$(nproc) SHLIB_LIBS="-lncursesw"'],
  install=['make SHLIB_LIBS="-lncursesw" install DESTDIR=$DESTDIR'] + STRIP_DEV)

# ---------- attr / acl ----------
ATTR_URL = "https://download-mirror.savannah.gnu.org/releases/attr/attr-2.6.0.tar.xz"
P(name="attr-libs", version="2.6.0", description="libattr shared runtime library",
  license="LGPL-2.1-or-later", url=ATTR_URL, deps=["glibc >= 2.44"],
  provides=["attr-libs", "so:libattr.so.1"],
  build=["./configure --prefix=/usr --disable-static --sysconfdir=/etc", "make -j$(nproc)"],
  install=AT_INSTALL + STRIP_LIBS)
P(name="attr", version="2.6.0", description="Extended attribute tools (getfattr, setfattr)",
  license="GPL-2.0-or-later", url=ATTR_URL, deps=["attr-libs >= 2.6.0"], provides=["attr"],
  build=["./configure --prefix=/usr --disable-static --sysconfdir=/etc --docdir=/usr/share/doc/attr-2.6.0", "make -j$(nproc)"],
  install=AT_INSTALL + STRIP_CLI)
P(name="attr-dev", version="2.6.0", description="libattr headers, pkg-config and linker symlink",
  license="LGPL-2.1-or-later", url=ATTR_URL, deps=["attr-libs >= 2.6.0"], provides=["attr-dev"],
  build=["./configure --prefix=/usr --disable-static --sysconfdir=/etc", "make -j$(nproc)"],
  install=AT_INSTALL + STRIP_DEV)

ACL_URL = "https://download-mirror.savannah.gnu.org/releases/acl/acl-2.4.0.tar.xz"
P(name="acl-libs", version="2.4.0", description="libacl shared runtime library",
  license="LGPL-2.1-or-later", url=ACL_URL, deps=["attr-libs >= 2.6.0"],
  provides=["acl-libs", "so:libacl.so.1"],
  build=["./configure --prefix=/usr --disable-static --sysconfdir=/etc", "make -j$(nproc)"],
  install=AT_INSTALL + STRIP_LIBS)
P(name="acl", version="2.4.0", description="Access Control List tools (getfacl, setfacl)",
  license="GPL-2.0-or-later", url=ACL_URL, deps=["acl-libs >= 2.4.0", "attr-libs >= 2.6.0"], provides=["acl"],
  build=["./configure --prefix=/usr --disable-static --sysconfdir=/etc --docdir=/usr/share/doc/acl-2.4.0", "make -j$(nproc)"],
  install=AT_INSTALL + STRIP_CLI)
P(name="acl-dev", version="2.4.0", description="libacl headers, pkg-config and linker symlink",
  license="LGPL-2.1-or-later", url=ACL_URL, deps=["acl-libs >= 2.4.0", "attr-dev >= 2.6.0"], provides=["acl-dev"],
  build=["./configure --prefix=/usr --disable-static --sysconfdir=/etc", "make -j$(nproc)"],
  install=AT_INSTALL + STRIP_DEV)

# ---------- libcap ----------
CAP_URL = "https://cdn.kernel.org/pub/linux/libs/security/linux-privs/libcap2/libcap-2.78.tar.xz"
P(name="libcap-libs", version="2.78", description="libcap/libpsx shared runtime libraries",
  license="BSD-3-Clause", url=CAP_URL, deps=["glibc >= 2.44"],
  provides=["libcap-libs", "so:libcap.so.2", "so:libpsx.so.2"],
  prepare=["sed -i '/install -m 0644 doc/d' Makefile", "sed -i '/install -m.*STA/d' libcap/Makefile"],
  build=["make prefix=/usr lib=lib PAM_CAP=no GOLANG=no -j$(nproc)"],
  install=["make prefix=/usr lib=lib PAM_CAP=no GOLANG=no install DESTDIR=$DESTDIR"] + STRIP_LIBS)
P(name="libcap", version="2.78", description="POSIX capabilities tools (getcap, setcap, capsh)",
  license="BSD-3-Clause", url=CAP_URL, deps=["libcap-libs >= 2.78"], provides=["libcap"],
  prepare=["sed -i '/install -m 0644 doc/d' Makefile", "sed -i '/install -m.*STA/d' libcap/Makefile"],
  build=["make prefix=/usr lib=lib PAM_CAP=no GOLANG=no -j$(nproc)"],
  install=["make prefix=/usr lib=lib PAM_CAP=no GOLANG=no install DESTDIR=$DESTDIR"] + STRIP_CLI)
P(name="libcap-dev", version="2.78", description="libcap headers, pkg-config and linker symlink",
  license="BSD-3-Clause", url=CAP_URL, deps=["libcap-libs >= 2.78"], provides=["libcap-dev"],
  prepare=["sed -i '/install -m 0644 doc/d' Makefile", "sed -i '/install -m.*STA/d' libcap/Makefile"],
  build=["make prefix=/usr lib=lib PAM_CAP=no GOLANG=no -j$(nproc)"],
  install=["make prefix=/usr lib=lib PAM_CAP=no GOLANG=no install DESTDIR=$DESTDIR RAISE_SETFCAP=no"] + STRIP_DEV)

# ---------- libxcrypt ----------
XC_URL = "https://github.com/besser82/libxcrypt/releases/download/v4.5.2/libxcrypt-4.5.2.tar.xz"
P(name="libxcrypt", version="4.5.2", description="Extended crypt runtime library (libcrypt.so.2)",
  license="LGPL-2.1-or-later", url=XC_URL, deps=["glibc >= 2.44"],
  provides=["libxcrypt", "so:libcrypt.so.2"],
  build=['CFLAGS="-O2 -std=gnu17" ./configure --prefix=/usr --enable-hashes=strong,glibc --enable-obsolete-api=no --disable-static --disable-failure-tokens --disable-werror', "make -j$(nproc)"],
  install=AT_INSTALL + STRIP_LIBS)
P(name="libxcrypt-dev", version="4.5.2", description="libxcrypt headers, pkg-config and linker symlink",
  license="LGPL-2.1-or-later", url=XC_URL, deps=["libxcrypt >= 4.5.2"], provides=["libxcrypt-dev"],
  build=['CFLAGS="-O2 -std=gnu17" ./configure --prefix=/usr --enable-hashes=strong,glibc --enable-obsolete-api=no --disable-static --disable-failure-tokens --disable-werror', "make -j$(nproc)"],
  install=AT_INSTALL + STRIP_DEV)

# ---------- openssl ----------
SSL_URL = "https://www.openssl.org/source/openssl-4.0.1.tar.gz"
SSL_CFG = "./config --prefix=/usr --openssldir=/etc/ssl --libdir=lib shared zlib-dynamic"
P(name="openssl-libs", version="4.0.1", description="libssl/libcrypto shared runtime libraries",
  license="Apache-2.0", url=SSL_URL,
  deps=["zlib >= 1.3.2"], bdeps=["zlib-dev >= 1.3.2"],
  provides=["openssl-libs", "so:libssl.so.4", "so:libcrypto.so.4", "so:libssl.so.3", "so:libcrypto.so.3"],
  build=[SSL_CFG + " no-apps enable-deprecated enable-tls1-method enable-tls1_1-method", "make -j$(nproc)"],
  install=["make INSTALL_LIBS= MANSUFFIX=ssl install DESTDIR=$DESTDIR",
           "ln -sf libssl.so.4 $DESTDIR/usr/lib/libssl.so.3",
           "ln -sf libcrypto.so.4 $DESTDIR/usr/lib/libcrypto.so.3"] + STRIP_LIBS)
P(name="openssl", version="4.0.1", description="OpenSSL CLI tools and /etc/ssl configuration",
  license="Apache-2.0", url=SSL_URL,
  deps=["openssl-libs >= 4.0.1", "zlib >= 1.3.2"], bdeps=["zlib-dev >= 1.3.2"],
  provides=["openssl"],
  build=[SSL_CFG, "make -j$(nproc)"],
  install=["make INSTALL_LIBS= MANSUFFIX=ssl install DESTDIR=$DESTDIR"] + STRIP_CLI)
P(name="openssl-dev", version="4.0.1", description="OpenSSL headers, pkg-config and unversioned .so",
  license="Apache-2.0", url=SSL_URL,
  deps=["openssl-libs >= 4.0.1"], bdeps=["zlib-dev >= 1.3.2"],
  provides=["openssl-dev"],
  build=[SSL_CFG + " enable-deprecated enable-tls1-method enable-tls1_1-method", "make -j$(nproc)"],
  install=["make INSTALL_LIBS= MANSUFFIX=ssl install DESTDIR=$DESTDIR",
           "rm -rf $DESTDIR/usr/lib/engines-* $DESTDIR/usr/lib/ossl-modules"] + STRIP_DEV)

# ---------- pcre2 ----------
PCRE_URL = "https://github.com/PCRE2Project/pcre2/releases/download/pcre2-10.47/pcre2-10.47.tar.bz2"
PCRE_CFG = "./configure --prefix=/usr --enable-pcre2-16 --enable-pcre2-32 --enable-jit --disable-static"
P(name="pcre2-libs", version="10.47", description="libpcre2-8/16/32 and libpcre2-posix runtime libraries",
  license="BSD-3-Clause", url=PCRE_URL,
  deps=["bzip2-libs >= 1.0.8", "zlib >= 1.3.2"],
  bdeps=["bzip2-dev >= 1.0.8", "zlib-dev >= 1.3.2"],
  provides=["pcre2-libs", "so:libpcre2-8.so.0", "so:libpcre2-16.so.0", "so:libpcre2-32.so.0", "so:libpcre2-posix.so.3"],
  build=[PCRE_CFG + " --disable-pcre2grep --disable-pcre2test", "make -j$(nproc)"],
  install=AT_INSTALL + STRIP_LIBS)
P(name="pcre2", version="10.47", description="pcre2grep, pcre2test and pcre2-config tools",
  license="BSD-3-Clause", url=PCRE_URL,
  deps=["pcre2-libs >= 10.47", "bzip2-libs >= 1.0.8", "zlib >= 1.3.2"],
  bdeps=["bzip2-dev >= 1.0.8", "zlib-dev >= 1.3.2"],
  provides=["pcre2"],
  build=[PCRE_CFG, "make -j$(nproc)"],
  install=AT_INSTALL + STRIP_CLI)
P(name="pcre2-dev", version="10.47", description="PCRE2 headers, pkg-config and linker symlink",
  license="BSD-3-Clause", url=PCRE_URL, deps=["pcre2-libs >= 10.47"], provides=["pcre2-dev"],
  build=[PCRE_CFG, "make -j$(nproc)"],
  install=AT_INSTALL + STRIP_DEV)

# ---------- curl ----------
CURL_URL = "https://curl.se/download/curl-8.21.0.tar.xz"
CURL_CFG = "./configure --prefix=/usr --disable-static --with-openssl --with-zlib --with-zstd --without-brotli --without-nghttp2 --without-libpsl --without-libidn2"
P(name="curl-libs", version="8.21.0", description="libcurl shared runtime library",
  license="curl", url=CURL_URL,
  deps=["openssl-libs >= 4.0.1", "zlib >= 1.3.2", "zstd-libs >= 1.5.7"],
  bdeps=["openssl-dev >= 4.0.1", "zlib-dev >= 1.3.2", "zstd-dev >= 1.5.7", "pkgconf >= 3.0.5"],
  provides=["curl-libs", "so:libcurl.so.4"],
  build=[CURL_CFG, "make -j$(nproc)"],
  install=AT_INSTALL + STRIP_LIBS)
P(name="curl", version="8.21.0", description="curl command-line URL transfer tool",
  license="curl", url=CURL_URL,
  deps=["curl-libs >= 8.21.0"],
  bdeps=["openssl-dev >= 4.0.1", "zlib-dev >= 1.3.2", "zstd-dev >= 1.5.7", "pkgconf >= 3.0.5"],
  provides=["curl"],
  build=[CURL_CFG, "make -j$(nproc)"],
  install=AT_INSTALL + STRIP_CLI)
P(name="curl-dev", version="8.21.0", description="libcurl headers, pkg-config and linker symlink",
  license="curl", url=CURL_URL,
  deps=["curl-libs >= 8.21.0", "openssl-dev >= 4.0.1", "zlib-dev >= 1.3.2"],
  bdeps=["openssl-dev >= 4.0.1", "zlib-dev >= 1.3.2", "zstd-dev >= 1.5.7", "pkgconf >= 3.0.5"],
  provides=["curl-dev"],
  build=[CURL_CFG, "make -j$(nproc)"],
  install=AT_INSTALL + STRIP_DEV)

# ---------- libelf (elfutils libelf only) ----------
ELF_URL = "https://sourceware.org/elfutils/ftp/0.196/elfutils-0.196.tar.bz2"
P(name="libelf", version="0.196", description="libelf shared runtime library (from elfutils)",
  license="GPL-2.0-or-later OR LGPL-3.0-or-later", url=ELF_URL,
  deps=["zlib >= 1.3.2", "xz-libs >= 5.8.3", "zstd-libs >= 1.5.7", "bzip2-libs >= 1.0.8"],
  bdeps=["zlib-dev >= 1.3.2", "xz-dev >= 5.8.3", "zstd-dev >= 1.5.7", "bzip2-dev >= 1.0.8", "pkgconf >= 3.0.5"],
  provides=["libelf", "so:libelf.so.1"],
  build=["./configure --prefix=/usr --disable-debuginfod --enable-libdebuginfod=dummy --disable-static",
         "make -C lib -j$(nproc)", "make -C libelf -j$(nproc)"],
  install=["make -C libelf install DESTDIR=$DESTDIR",
           "mkdir -p $DESTDIR/usr/lib/pkgconfig",
           "install -vm644 config/libelf.pc $DESTDIR/usr/lib/pkgconfig/"] + STRIP_LIBS)
P(name="libelf-dev", version="0.196", description="libelf headers, pkg-config and linker symlink",
  license="GPL-2.0-or-later OR LGPL-3.0-or-later", url=ELF_URL,
  deps=["libelf >= 0.196", "zlib-dev >= 1.3.2"],
  bdeps=["zlib-dev >= 1.3.2", "pkgconf >= 3.0.5"],
  provides=["libelf-dev"],
  build=["./configure --prefix=/usr --disable-debuginfod --enable-libdebuginfod=dummy --disable-static",
         "make -C lib -j$(nproc)", "make -C libelf -j$(nproc)"],
  install=["make -C libelf install DESTDIR=$DESTDIR",
           "mkdir -p $DESTDIR/usr/lib/pkgconfig",
           "install -vm644 config/libelf.pc $DESTDIR/usr/lib/pkgconfig/"] + STRIP_DEV)

# ---------- kmod ----------
KMOD_URL = "https://mirrors.edge.kernel.org/pub/linux/utils/kernel/kmod/kmod-34.2.tar.xz"
P(name="kmod-libs", version="34.2", description="libkmod shared runtime library",
  license="LGPL-2.1-or-later", url=KMOD_URL,
  deps=["xz-libs >= 5.8.3", "zlib >= 1.3.2", "zstd-libs >= 1.5.7", "openssl-libs >= 4.0.1"],
  bdeps=["meson >= 1.12.0", "ninja >= 1.13.2", "pkgconf >= 3.0.5", "openssl-dev >= 4.0.1",
         "zlib-dev >= 1.3.2", "xz-dev >= 5.8.3", "zstd-dev >= 1.5.7"],
  provides=["kmod-libs", "so:libkmod.so.2"],
  build=["mkdir -p build", "cd build && meson setup --prefix=/usr --buildtype=release -D manpages=false ..",
         "cd build && ninja -j$(nproc)"],
  install=["cd build && DESTDIR=$DESTDIR ninja install"] + STRIP_LIBS)
P(name="kmod", version="34.2", description="Kernel module tools (modprobe, lsmod, depmod)",
  license="LGPL-2.1-or-later", url=KMOD_URL,
  deps=["kmod-libs >= 34.2"],
  bdeps=["meson >= 1.12.0", "ninja >= 1.13.2", "pkgconf >= 3.0.5", "openssl-dev >= 4.0.1",
         "zlib-dev >= 1.3.2", "xz-dev >= 5.8.3", "zstd-dev >= 1.5.7"],
  provides=["kmod"],
  build=["mkdir -p build", "cd build && meson setup --prefix=/usr --buildtype=release -D manpages=false ..",
         "cd build && ninja -j$(nproc)"],
  install=["cd build && DESTDIR=$DESTDIR ninja install"] + STRIP_CLI)
P(name="kmod-dev", version="34.2", description="libkmod headers, pkg-config and linker symlink",
  license="LGPL-2.1-or-later", url=KMOD_URL,
  deps=["kmod-libs >= 34.2"],
  bdeps=["meson >= 1.12.0", "ninja >= 1.13.2", "pkgconf >= 3.0.5", "openssl-dev >= 4.0.1",
         "zlib-dev >= 1.3.2", "xz-dev >= 5.8.3", "zstd-dev >= 1.5.7"],
  provides=["kmod-dev"],
  build=["mkdir -p build", "cd build && meson setup --prefix=/usr --buildtype=release -D manpages=false ..",
         "cd build && ninja -j$(nproc)"],
  install=["cd build && DESTDIR=$DESTDIR ninja install"] + STRIP_DEV)

# ---------- lmdb (pure library + incidental mdb_* tools: no -libs) ----------
LMDB_URL = "https://github.com/LMDB/lmdb/archive/refs/tags/LMDB_0.9.35.tar.gz"
LMDB_SHA = "18b021fd589d30cc08860a9550a30ae51637117451385e9581616da751326632"
P(name="lmdb", version="0.9.35", description="LMDB runtime library and tools (mdb_stat, mdb_copy, mdb_dump, mdb_load)",
  license="OpenLDAP", url=LMDB_URL, sha256=LMDB_SHA,
  deps=["glibc >= 2.44"], provides=["lmdb", "so:liblmdb.so.0"],
  build=["cd libraries/liblmdb && make -j$(nproc)"],
  install=["mkdir -p $DESTDIR/usr/lib $DESTDIR/usr/bin $DESTDIR/usr/share/man/man1",
           "cd libraries/liblmdb && make prefix=/usr install DESTDIR=$DESTDIR",
           "if [ -f $DESTDIR/usr/lib/liblmdb.so ] && [ ! -e $DESTDIR/usr/lib/liblmdb.so.0 ]; then "
           "cp -a $DESTDIR/usr/lib/liblmdb.so $DESTDIR/usr/lib/liblmdb.so.0.0.0; "
           "ln -sfn liblmdb.so.0.0.0 $DESTDIR/usr/lib/liblmdb.so.0; fi"] + STRIP_LIBS_KEEP_CLI)
P(name="lmdb-dev", version="0.9.35", description="LMDB headers, pkg-config and linker symlink",
  license="OpenLDAP", url=LMDB_URL, sha256=LMDB_SHA,
  deps=["lmdb >= 0.9.35"], provides=["lmdb-dev"],
  build=["cd libraries/liblmdb && make -j$(nproc)"],
  install=["mkdir -p $DESTDIR/usr/include $DESTDIR/usr/lib/pkgconfig",
           "cd libraries/liblmdb && make prefix=/usr install DESTDIR=$DESTDIR",
           "ln -sfn liblmdb.so.0 $DESTDIR/usr/lib/liblmdb.so",
           "printf 'prefix=/usr\\nexec_prefix=${prefix}\\nlibdir=${exec_prefix}/lib\\nincludedir=${prefix}/include\\n\\nName: lmdb\\nDescription: Lightning Memory-Mapped Database\\nVersion: 0.9.35\\nLibs: -L${libdir} -llmdb\\nCflags: -I${includedir}\\n' > $DESTDIR/usr/lib/pkgconfig/lmdb.pc"] + STRIP_DEV)

# ---------- tomlplusplus ----------
TOML_URL = "https://github.com/marzer/tomlplusplus/archive/refs/tags/v3.4.0.tar.gz"
P(name="tomlplusplus", version="3.4.0", description="toml++ compiled shared runtime library",
  license="MIT", url=TOML_URL, deps=["gcc-libs >= 15.3.0", "glibc >= 2.44"],
  provides=["tomlplusplus", "so:libtomlplusplus.so.3"],
  build=["g++ -fPIC -shared -O3 -std=c++20 -Iinclude src/toml.cpp -DTOML_SHARED_LIB=1 -DTOML_EXPORTING=1 -Wl,-soname,libtomlplusplus.so.3 -o libtomlplusplus.so.3.4.0",
         "ln -sf libtomlplusplus.so.3.4.0 libtomlplusplus.so.3"],
  install=["mkdir -p $DESTDIR/usr/lib", "cp -d libtomlplusplus.so.3.4.0 libtomlplusplus.so.3 $DESTDIR/usr/lib/"])
P(name="tomlplusplus-dev", version="3.4.0", description="toml++ headers, pkg-config and linker symlink",
  license="MIT", url=TOML_URL, deps=["tomlplusplus >= 3.4.0"], provides=["tomlplusplus-dev"],
  build=["g++ -fPIC -shared -O3 -std=c++20 -Iinclude src/toml.cpp -DTOML_SHARED_LIB=1 -DTOML_EXPORTING=1 -Wl,-soname,libtomlplusplus.so.3 -o libtomlplusplus.so.3.4.0",
         "ln -sf libtomlplusplus.so.3.4.0 libtomlplusplus.so.3",
         "ln -sf libtomlplusplus.so.3 libtomlplusplus.so"],
  install=["mkdir -p $DESTDIR/usr/include $DESTDIR/usr/lib/pkgconfig",
           "cp -r include/toml++ $DESTDIR/usr/include/",
           "ln -sf libtomlplusplus.so.3 $DESTDIR/usr/lib/libtomlplusplus.so",
           "printf 'prefix=/usr\\nincludedir=${prefix}/include\\nlibdir=${prefix}/lib\\n\\nName: tomlplusplus\\nDescription: TOML parser for C++20\\nVersion: 3.4.0\\nLibs: -L${libdir} -ltomlplusplus\\nCflags: -I${includedir}\\n' > $DESTDIR/usr/lib/pkgconfig/tomlplusplus.pc"])

# ---------- libarchive ----------
LA_URL = "https://github.com/libarchive/libarchive/releases/download/v3.8.9/libarchive-3.8.9.tar.xz"
LA_SHA = "888c934f9d95648ecb9163dc8e23ab80a476ecb81a8f1154704a227b5b676dde"  # verified 2026-08-21 after axel re-fetch
LA_CFG = "./configure --prefix=/usr --disable-static --without-xml2 --with-expat --without-cng --with-openssl --with-zlib --with-bz2lib --with-lzma --with-zstd"
P(name="libarchive-libs", version="3.8.9", description="libarchive shared runtime library",
  license="BSD-2-Clause", url=LA_URL, sha256=LA_SHA,
  deps=["openssl-libs >= 4.0.1", "zlib >= 1.3.2", "bzip2-libs >= 1.0.8", "xz-libs >= 5.8.3",
        "zstd-libs >= 1.5.7", "acl-libs >= 2.4.0", "attr-libs >= 2.6.0", "expat-libs >= 2.8.3"],
  bdeps=["pkgconf >= 3.0.5", "openssl-dev >= 4.0.1", "zlib-dev >= 1.3.2", "bzip2-dev >= 1.0.8",
         "xz-dev >= 5.8.3", "zstd-dev >= 1.5.7", "acl-dev >= 2.4.0", "attr-dev >= 2.6.0", "expat-dev >= 2.8.3"],
  provides=["libarchive-libs", "so:libarchive.so.13"],
  build=[LA_CFG, "make -j$(nproc)"],
  install=AT_INSTALL + STRIP_LIBS)
P(name="libarchive", version="3.8.9", description="bsdtar and bsdcpio archive tools",
  license="BSD-2-Clause", url=LA_URL, sha256=LA_SHA,
  deps=["libarchive-libs >= 3.8.9"],
  bdeps=["pkgconf >= 3.0.5", "openssl-dev >= 4.0.1", "zlib-dev >= 1.3.2"],
  provides=["libarchive"],
  build=[LA_CFG, "make -j$(nproc)"],
  install=AT_INSTALL + STRIP_CLI)
P(name="libarchive-dev", version="3.8.9", description="libarchive headers, pkg-config and linker symlink",
  license="BSD-2-Clause", url=LA_URL, sha256=LA_SHA,
  deps=["libarchive-libs >= 3.8.9", "openssl-dev >= 4.0.1", "zlib-dev >= 1.3.2", "zstd-dev >= 1.5.7",
        "xz-dev >= 5.8.3", "bzip2-dev >= 1.0.8", "acl-dev >= 2.4.0", "attr-dev >= 2.6.0"],
  bdeps=["pkgconf >= 3.0.5"],
  provides=["libarchive-dev"],
  build=[LA_CFG, "make -j$(nproc)"],
  install=AT_INSTALL + STRIP_DEV)

# ---------- libseccomp ----------
SC_URL = "https://github.com/seccomp/libseccomp/releases/download/v2.6.0/libseccomp-2.6.0.tar.gz"
SC_SHA = "83b6085232d1588c379dc9b9cae47bb37407cf262e6e74993c61ba72d2a784dc"
P(name="libseccomp", version="2.6.0", description="libseccomp shared runtime library",
  license="LGPL-2.1-only", url=SC_URL, sha256=SC_SHA,
  deps=["glibc >= 2.44"], bdeps=["gperf >= 3.3"],
  provides=["libseccomp", "so:libseccomp.so.2"],
  build=AT_BUILD, install=AT_INSTALL + STRIP_LIBS)
P(name="libseccomp-dev", version="2.6.0", description="libseccomp headers, pkg-config and linker symlink",
  license="LGPL-2.1-only", url=SC_URL, sha256=SC_SHA,
  deps=["libseccomp >= 2.6.0"], bdeps=["gperf >= 3.3"],
  provides=["libseccomp-dev"],
  build=AT_BUILD, install=AT_INSTALL + STRIP_DEV)

# ---------- libpipeline ----------
PIPE_URL = "https://deb.debian.org/debian/pool/main/libp/libpipeline/libpipeline_1.5.8.orig.tar.gz"
P(name="libpipeline", version="1.5.8", description="libpipeline shared runtime library",
  license="GPL-3.0-or-later", url=PIPE_URL, deps=["glibc >= 2.44"],
  provides=["libpipeline", "so:libpipeline.so.1"],
  build=AT_BUILD, install=AT_INSTALL + STRIP_LIBS)
P(name="libpipeline-dev", version="1.5.8", description="libpipeline headers, pkg-config and linker symlink",
  license="GPL-3.0-or-later", url=PIPE_URL, deps=["libpipeline >= 1.5.8"],
  provides=["libpipeline-dev"],
  build=AT_BUILD, install=AT_INSTALL + STRIP_DEV)

# ---------- libtool (single package: CLI, libltdl, headers and macros) ----------
LT_URL = "https://mirrors.kernel.org/gnu/libtool/libtool-2.6.2.tar.xz"
P(name="libtool", version="2.6.2", description="GNU libtool, libtoolize, libltdl and development macros",
  license="GPL-2.0-or-later AND LGPL-2.1-or-later", url=LT_URL, deps=["glibc >= 2.44"],
  provides=["libtool", "so:libltdl.so.7"],
  build=["./configure --prefix=/usr", "make -j$(nproc)"],
  install=AT_INSTALL)

# ---------- util-linux ----------
UL_URL = "https://mirrors.edge.kernel.org/pub/linux/utils/util-linux/v2.42/util-linux-2.42.2.tar.xz"
UL_CFG = "./configure --prefix=/usr --bindir=/usr/bin --sbindir=/usr/sbin --libdir=/usr/lib --sysconfdir=/etc --disable-static --without-python --without-systemd --disable-makeinstall-chown --without-bash-completion-dir"
P(name="util-linux-libs", version="2.42.2", description="libuuid/libblkid/libmount/libsmartcols runtime libraries",
  license="LGPL-2.1-or-later", url=UL_URL, deps=["glibc >= 2.44"],
  provides=["util-linux-libs", "so:libuuid.so.1", "so:libblkid.so.1", "so:libmount.so.1", "so:libsmartcols.so.1"],
  build=["./configure --prefix=/usr --libdir=/usr/lib --disable-static --without-python --without-systemd --disable-all-programs --enable-libmount --enable-libuuid --enable-libblkid --enable-libsmartcols",
         "make -j$(nproc)"],
  install=AT_INSTALL + STRIP_LIBS)
P(name="util-linux", version="2.42.2", description="Essential system utilities (mount, fdisk, login, su, ...)",
  license="GPL-2.0-or-later", url=UL_URL, deps=["util-linux-libs >= 2.42.2"], provides=["util-linux"],
  build=[UL_CFG + " --docdir=/usr/share/doc/util-linux-2.42.2", "make -j$(nproc)"],
  install=AT_INSTALL + [
      "rm -rf $DESTDIR/usr/include $DESTDIR/usr/lib/pkgconfig",
      "rm -f $DESTDIR/usr/lib/libuuid.so* $DESTDIR/usr/lib/libblkid.so* $DESTDIR/usr/lib/libmount.so* $DESTDIR/usr/lib/libsmartcols.so*",
  ])
P(name="util-linux-dev", version="2.42.2", description="util-linux library headers, pkg-config and linker symlink",
  license="LGPL-2.1-or-later", url=UL_URL, deps=["util-linux-libs >= 2.42.2"], provides=["util-linux-dev"],
  build=["./configure --prefix=/usr --libdir=/usr/lib --disable-static --without-python --without-systemd --disable-all-programs --enable-libmount --enable-libuuid --enable-libblkid --enable-libsmartcols --enable-libfdisk",
         "make -j$(nproc)"],
  install=AT_INSTALL + STRIP_DEV)

# ---------- e2fsprogs ----------
E2_URL = "https://mirrors.edge.kernel.org/pub/linux/kernel/people/tytso/e2fsprogs/v1.47.4/e2fsprogs-1.47.4.tar.xz"
E2_CFG = "./configure --prefix=/usr --sysconfdir=/etc --enable-elf-shlibs --disable-libblkid --disable-libuuid --disable-uuidd --disable-fsck"
P(name="e2fsprogs-libs", version="1.47.4", description="libext2fs/libcom_err/libss runtime libraries",
  license="LGPL-2.1-or-later", url=E2_URL, deps=["util-linux-libs >= 2.42.2"],
  bdeps=["pkgconf >= 3.0.5", "util-linux-dev >= 2.42.2"],
  provides=["e2fsprogs-libs", "so:libext2fs.so.2", "so:libcom_err.so.2", "so:libss.so.2"],
  build=[E2_CFG, "make -j$(nproc) libs"],
  install=["make install-libs DESTDIR=$DESTDIR"] + STRIP_LIBS)
P(name="e2fsprogs", version="1.47.4", description="Ext2/3/4 filesystem utilities",
  license="GPL-2.0-or-later", url=E2_URL, deps=["e2fsprogs-libs >= 1.47.4", "util-linux-libs >= 2.42.2"],
  bdeps=["pkgconf >= 3.0.5"], provides=["e2fsprogs"],
  build=[E2_CFG, "make -j$(nproc)"],
  install=AT_INSTALL + [
      "rm -f $DESTDIR/usr/lib/libext2fs.so* $DESTDIR/usr/lib/libcom_err.so* $DESTDIR/usr/lib/libss.so* $DESTDIR/usr/lib/libsupport.so*",
      "rm -rf $DESTDIR/usr/include $DESTDIR/usr/lib/et $DESTDIR/usr/lib/ss $DESTDIR/usr/lib/pkgconfig",
  ])
P(name="e2fsprogs-dev", version="1.47.4", description="e2fsprogs library headers, pkg-config and linker symlink",
  license="LGPL-2.1-or-later", url=E2_URL,
  deps=["e2fsprogs-libs >= 1.47.4", "util-linux-dev >= 2.42.2"],
  bdeps=["pkgconf >= 3.0.5"], provides=["e2fsprogs-dev"],
  prepare=["rm -rf build && mkdir -p build"],
  build=["cd build && ../configure --prefix=/usr --sysconfdir=/etc --enable-elf-shlibs --disable-libblkid --disable-libuuid --disable-fsck",
         "cd build && make -j$(nproc)"],
  install=["cd build && make install-libs DESTDIR=$DESTDIR"] + STRIP_DEV)

# ---------- gdbm ----------
GDBM_URL = "https://mirrors.kernel.org/gnu/gdbm/gdbm-1.26.tar.gz"
P(name="gdbm-libs", version="1.26", description="libgdbm/libgdbm_compat shared runtime libraries",
  license="GPL-3.0-or-later", url=GDBM_URL, deps=["glibc >= 2.44", "readline >= 8.3", "ncurses-libs >= 6.6"],
  bdeps=["readline-dev >= 8.3", "ncurses-dev >= 6.6"],
  provides=["gdbm-libs", "so:libgdbm.so.6", "so:libgdbm_compat.so.4"],
  build=["./configure --prefix=/usr --disable-static --enable-libgdbm-compat", "make -j$(nproc)"],
  install=AT_INSTALL + STRIP_LIBS)
P(name="gdbm", version="1.26", description="GNU dbm tools (gdbmtool, gdbm_dump, gdbm_load)",
  license="GPL-3.0-or-later", url=GDBM_URL, deps=["gdbm-libs >= 1.26"], provides=["gdbm"],
  build=["./configure --prefix=/usr --disable-static --enable-libgdbm-compat", "make -j$(nproc)"],
  install=AT_INSTALL + STRIP_CLI)
P(name="gdbm-dev", version="1.26", description="gdbm headers, pkg-config and linker symlink",
  license="GPL-3.0-or-later", url=GDBM_URL, deps=["gdbm-libs >= 1.26"], provides=["gdbm-dev"],
  build=["./configure --prefix=/usr --disable-static --enable-libgdbm-compat", "make -j$(nproc)"],
  install=AT_INSTALL + STRIP_DEV)

# ---------- gettext (keep 1.0 as requested) ----------
GT_URL = "https://ftp.gnu.org/gnu/gettext/gettext-1.0.tar.xz"
P(name="gettext-libs", version="1.0", description="libintl/libasprintf shared runtime libraries",
  license="LGPL-2.1-or-later", url=GT_URL, deps=["glibc >= 2.44", "ncurses-libs >= 6.6", "acl-libs >= 2.4.0"],
  provides=["gettext-libs", "so:libintl.so.8", "so:libasprintf.so.1"],
  build=["./configure --prefix=/usr --disable-static --docdir=/usr/share/doc/gettext-1.0", "make -j$(nproc)"],
  install=AT_INSTALL + STRIP_LIBS)
P(name="gettext", version="1.0", description="GNU i18n tools (gettext, msgfmt, xgettext, ...)",
  license="GPL-3.0-or-later", url=GT_URL, deps=["gettext-libs >= 1.0"], provides=["gettext"],
  build=["./configure --prefix=/usr --disable-static --docdir=/usr/share/doc/gettext-1.0", "make -j$(nproc)"],
  install=AT_INSTALL + STRIP_CLI)
P(name="gettext-dev", version="1.0", description="gettext/libintl headers, pkg-config and linker symlink",
  license="LGPL-2.1-or-later", url=GT_URL, deps=["gettext-libs >= 1.0"], provides=["gettext-dev"],
  build=["./configure --prefix=/usr --disable-static --docdir=/usr/share/doc/gettext-1.0", "make -j$(nproc)"],
  install=AT_INSTALL + STRIP_DEV)

# ---------- expat ----------
EXP_URL = "https://github.com/libexpat/libexpat/releases/download/R_2_8_3/expat-2.8.3.tar.xz"
P(name="expat-libs", version="2.8.3", description="libexpat shared runtime library",
  license="MIT", url=EXP_URL, deps=["glibc >= 2.44"],
  provides=["expat-libs", "so:libexpat.so.1"],
  build=AT_BUILD, install=AT_INSTALL + STRIP_LIBS)
P(name="expat", version="2.8.3", description="xmlwf XML well-formedness checker",
  license="MIT", url=EXP_URL, deps=["expat-libs >= 2.8.3"], provides=["expat"],
  build=AT_BUILD, install=AT_INSTALL + STRIP_CLI)
P(name="expat-dev", version="2.8.3", description="libexpat headers, pkg-config and linker symlink",
  license="MIT", url=EXP_URL, deps=["expat-libs >= 2.8.3"], provides=["expat-dev"],
  build=AT_BUILD, install=AT_INSTALL + STRIP_DEV)

# ---------- libffi ----------
FFI_URL = "https://github.com/libffi/libffi/releases/download/v3.8.0/libffi-3.8.0.tar.gz"
P(name="libffi", version="3.8.0", description="libffi shared runtime library",
  license="MIT", url=FFI_URL, deps=["glibc >= 2.44"],
  provides=["libffi", "so:libffi.so.8"],
  build=["./configure --prefix=/usr --disable-static --with-gcc-arch=native", "make -j$(nproc)"],
  install=AT_INSTALL + STRIP_LIBS)
P(name="libffi-dev", version="3.8.0", description="libffi headers, pkg-config and linker symlink",
  license="MIT", url=FFI_URL, deps=["libffi >= 3.8.0"], provides=["libffi-dev"],
  build=["./configure --prefix=/usr --disable-static --with-gcc-arch=native", "make -j$(nproc)"],
  install=AT_INSTALL + STRIP_DEV)

# ---------- sqlite ----------
SQL_URL = "https://www.sqlite.org/2026/sqlite-autoconf-3530400.tar.gz"
SQL_CFG = './configure --prefix=/usr --disable-static --enable-fts5 CFLAGS="-g -O2 -DSQLITE_ENABLE_FTS3=1 -DSQLITE_ENABLE_FTS4=1 -DSQLITE_ENABLE_COLUMN_METADATA=1 -DSQLITE_ENABLE_UNLOCK_NOTIFY=1 -DSQLITE_ENABLE_DBSTAT_VTAB=1 -DSQLITE_ENABLE_FTS3_TOKENIZER=1 -DSQLITE_SECURE_DELETE=1"'
P(name="sqlite-libs", version="3.53.4", description="libsqlite3 shared runtime library",
  license="blessing", url=SQL_URL,
  deps=["zlib >= 1.3.2"], bdeps=["zlib-dev >= 1.3.2"],
  provides=["sqlite-libs", "so:libsqlite3.so.0"],
  build=[SQL_CFG, "make -j$(nproc)"],
  install=AT_INSTALL + STRIP_LIBS)
P(name="sqlite", version="3.53.4", description="SQLite command-line shell",
  license="blessing", url=SQL_URL, deps=["sqlite-libs >= 3.53.4"], provides=["sqlite"],
  build=[SQL_CFG, "make -j$(nproc)"],
  install=AT_INSTALL + STRIP_CLI)
P(name="sqlite-dev", version="3.53.4", description="SQLite headers, pkg-config and linker symlink",
  license="blessing", url=SQL_URL, deps=["sqlite-libs >= 3.53.4"], provides=["sqlite-dev"],
  build=[SQL_CFG, "make -j$(nproc)"],
  install=AT_INSTALL + STRIP_DEV)

# ---------- mpdecimal ----------
MPD_URL = "https://www.bytereef.org/software/mpdecimal/releases/mpdecimal-4.0.1.tar.gz"
P(name="mpdecimal", version="4.0.1", description="libmpdec/libmpdec++ shared runtime libraries",
  license="BSD-2-Clause", url=MPD_URL, deps=["glibc >= 2.44"],
  provides=["mpdecimal", "so:libmpdec.so.4", "so:libmpdec++.so.4"],
  build=["./configure --prefix=/usr", "make -j$(nproc)"],
  install=AT_INSTALL + STRIP_LIBS)
P(name="mpdecimal-dev", version="4.0.1", description="mpdecimal headers, pkg-config and linker symlink",
  license="BSD-2-Clause", url=MPD_URL, deps=["mpdecimal >= 4.0.1"], provides=["mpdecimal-dev"],
  build=["./configure --prefix=/usr", "make -j$(nproc)"],
  install=AT_INSTALL + STRIP_DEV)

# ---------- tcl ----------
TCL_URL = "https://downloads.sourceforge.net/project/tcl/Tcl/8.6.18/tcl8.6.18-src.tar.gz"
P(name="tcl-libs", version="8.6.18", description="libtcl8.6 shared runtime library",
  license="TCL", url=TCL_URL, deps=["zlib >= 1.3.2"], bdeps=["zlib-dev >= 1.3.2"],
  provides=["tcl-libs", "so:libtcl8.6.so"],
  build=["cd unix && ./configure --prefix=/usr --mandir=/usr/share/man", "cd unix && make -j$(nproc)"],
  install=["cd unix && make install DESTDIR=$DESTDIR"] + STRIP_LIBS)
P(name="tcl", version="8.6.18", description="Tcl interpreter (tclsh)",
  license="TCL", url=TCL_URL, deps=["tcl-libs >= 8.6.18"], provides=["tcl"],
  build=["cd unix && ./configure --prefix=/usr --mandir=/usr/share/man", "cd unix && make -j$(nproc)"],
  install=["cd unix && make install DESTDIR=$DESTDIR",
           "ln -sfv tclsh8.6 $DESTDIR/usr/bin/tclsh"] + STRIP_CLI)
P(name="tcl-dev", version="8.6.18", description="Tcl headers, pkg-config and libtcl.so symlink",
  license="TCL", url=TCL_URL, deps=["tcl-libs >= 8.6.18"], provides=["tcl-dev"],
  build=["cd unix && ./configure --prefix=/usr --mandir=/usr/share/man", "cd unix && make -j$(nproc)"],
  install=["cd unix && make install DESTDIR=$DESTDIR",
           "cd unix && make install-private-headers DESTDIR=$DESTDIR",
           "ln -sfv libtcl8.6.so $DESTDIR/usr/lib/libtcl.so"] + STRIP_DEV)

# ---------- expect ----------
P(name="expect", version="5.45.4", description="Automate interactive applications",
  license="Public-domain", url="https://downloads.sourceforge.net/project/expect/Expect/5.45.4/expect5.45.4.tar.gz",
  deps=["tcl-libs >= 8.6.18"], bdeps=["tcl-dev >= 8.6.18"],
  provides=["expect", "so:libexpect5.45.4.so"],
  build=['CFLAGS="-O2 -std=gnu17 -Wno-implicit-function-declaration -Wno-implicit-int -Wno-incompatible-pointer-types" ./configure --prefix=/usr --with-tcl=/usr/lib --enable-shared --mandir=/usr/share/man --with-tclinclude=/usr/include',
         "make -j$(nproc)"],
  install=AT_INSTALL + ["ln -sfv expect5.45.4/libexpect5.45.4.so $DESTDIR/usr/lib/ 2>/dev/null || true"])

# ---------- python (runtime slot; libpython stays on system root) ----------
# Language interpreters are isolated. python-libs remains on /usr/lib so ELF
# DT_NEEDED can resolve libpython without a runtime profile. Modules
# (meson/setuptools/...) still install into /usr/lib/python3.14/site-packages
# and the isolated interpreter finds them via PYTHONPATH + shebang wrappers.
PY_URL = "https://www.python.org/ftp/python/3.14.7/Python-3.14.7.tar.xz"
PY_PREFIX = "/usr/lib/runtimes/python/3.14"
PY_RT_DEPS = [
    "libffi >= 3.8.0", "sqlite-libs >= 3.53.4", "mpdecimal >= 4.0.1", "expat-libs >= 2.8.3",
    "openssl-libs >= 4.0.1", "zlib >= 1.3.2", "bzip2-libs >= 1.0.8", "xz-libs >= 5.8.3",
    "zstd-libs >= 1.5.7", "lz4-libs >= 1.10.0", "ncurses-libs >= 6.6", "readline >= 8.3",
    "gdbm-libs >= 1.26", "libxcrypt >= 4.5.2",
]
PY_BDEPS = [
    "openssl-dev >= 4.0.1", "zlib-dev >= 1.3.2", "libffi-dev >= 3.8.0", "sqlite-dev >= 3.53.4",
    "expat-dev >= 2.8.3", "bzip2-dev >= 1.0.8", "xz-dev >= 5.8.3", "zstd-dev >= 1.5.7",
    "ncurses-dev >= 6.6", "readline-dev >= 8.3", "mpdecimal-dev >= 4.0.1", "pkgconf >= 3.0.5",
]
PY_CFLAGS = 'CFLAGS="$CFLAGS -DOPENSSL_NO_SSL3 -DOPENSSL_NO_SSL3_METHOD -DOPENSSL_NO_TLS1 -DOPENSSL_NO_TLS1_METHOD -DOPENSSL_NO_TLS1_1 -DOPENSSL_NO_TLS1_1_METHOD -DOPENSSL_NO_TLS1_2 -DOPENSSL_NO_TLS1_2_METHOD"'
PY_CFG = (
    PY_CFLAGS + " ./configure --prefix=" + PY_PREFIX
    + " --enable-shared --with-system-expat --with-system-libmpdec"
    + " --without-ensurepip --without-static-libpython"
)
PY3 = PY_PREFIX + "/bin/python3"
P(name="python-libs", version="3.14.7", description="libpython3.14 shared runtime library (system root)",
  license="PSF-2.0", url=PY_URL, deps=["glibc >= 2.44"] + PY_RT_DEPS, bdeps=PY_BDEPS,
  provides=["python-libs", "so:libpython3.14.so.1.0"],
  build=[PY_CFG, "make -j$(nproc)"],
  install=["make install DESTDIR=$DESTDIR",
           "mkdir -p $DESTDIR/usr/lib",
           "find $DESTDIR" + PY_PREFIX + " -name 'libpython3.14.so*' -exec cp -a {} $DESTDIR/usr/lib/ \\;",
           "rm -rf $DESTDIR" + PY_PREFIX + " $DESTDIR/usr/include $DESTDIR/usr/bin $DESTDIR/usr/sbin $DESTDIR/usr/share $DESTDIR/etc $DESTDIR/var $DESTDIR/usr/lib/pkgconfig $DESTDIR/usr/lib/cmake 2>/dev/null || true",
           "find $DESTDIR/usr/lib $DESTDIR/usr/lib64 -type f \\( -name '*.a' -o -name '*.la' \\) -delete 2>/dev/null || true",
           "find $DESTDIR/usr/lib $DESTDIR/usr/lib64 \\( -type l -o -type f \\) -name '*.so' ! -name '*.so.*' -delete 2>/dev/null || true"])
P(name="python", version="3.14.7",
  description="Python 3.14 isolated runtime slot (/usr/lib/runtimes/python/3.14)",
  license="PSF-2.0", url=PY_URL, channel="runtime/python:3.14",
  deps=["python-libs >= 3.14.7"] + PY_RT_DEPS, bdeps=PY_BDEPS,
  provides=["python"],
  build=[PY_CFG, "make -j$(nproc)"],
  install=["make install DESTDIR=$DESTDIR",
           "find $DESTDIR" + PY_PREFIX + " -name 'libpython3.14.so*' -delete",
           "ln -sf python3 $DESTDIR" + PY_PREFIX + "/bin/python",
           "mkdir -p $DESTDIR/usr/bin $DESTDIR/etc",
           "printf '#!/bin/sh\\nexport PYTHONPATH=/usr/lib/python3.14/site-packages${PYTHONPATH:+:$PYTHONPATH}\\nexec " + PY3 + " \"$@\"\\n' > $DESTDIR/usr/bin/python3",
           "chmod 755 $DESTDIR/usr/bin/python3",
           "ln -sfn python3 $DESTDIR/usr/bin/python",
           "printf '[global]\\nroot-user-action = ignore\\ndisable-pip-version-check = true\\n' > $DESTDIR/etc/pip.conf"])
P(name="python-dev", version="3.14.7", description="Python headers, pkg-config and libpython.so symlink",
  license="PSF-2.0", url=PY_URL, deps=["python-libs >= 3.14.7"], bdeps=PY_BDEPS,
  provides=["python-dev"],
  build=[PY_CFG, "make -j$(nproc)"],
  install=["make install DESTDIR=$DESTDIR",
           "mkdir -p $DESTDIR/usr/include $DESTDIR/usr/lib/pkgconfig $DESTDIR/usr/lib",
           "if [ -d $DESTDIR" + PY_PREFIX + "/include/python3.14 ]; then cp -a $DESTDIR" + PY_PREFIX + "/include/python3.14 $DESTDIR/usr/include/; elif [ -d $DESTDIR" + PY_PREFIX + "/include ]; then cp -a $DESTDIR" + PY_PREFIX + "/include/. $DESTDIR/usr/include/; fi",
           "if [ -d $DESTDIR" + PY_PREFIX + "/lib/pkgconfig ]; then cp -a $DESTDIR" + PY_PREFIX + "/lib/pkgconfig/. $DESTDIR/usr/lib/pkgconfig/; fi",
           "ln -sfn libpython3.14.so.1.0 $DESTDIR/usr/lib/libpython3.14.so",
           "ln -sfn libpython3.14.so.1.0 $DESTDIR/usr/lib/libpython3.so",
           "rm -rf $DESTDIR" + PY_PREFIX + " $DESTDIR/usr/bin $DESTDIR/usr/sbin $DESTDIR/usr/share $DESTDIR/etc $DESTDIR/var 2>/dev/null || true",
           "find $DESTDIR/usr/lib $DESTDIR/usr/lib64 -type f -name '*.so.*' -delete 2>/dev/null || true"])

# ---------- perl (toolchain slot; libperl stays on system root) ----------
PERL_URL = "https://www.cpan.org/src/5.0/perl-5.44.0.tar.xz"
PERL_PREFIX = "/opt/channels/perl/5.44"
PERL_CFG = (
    'BUILD_ZLIB=False BUILD_BZIP2=0 sh Configure -de'
    ' -D prefix=/opt/channels/perl/5.44'
    ' -D vendorprefix=/opt/channels/perl/5.44'
    ' -D privlib=/opt/channels/perl/5.44/lib/perl5/5.44/core_perl'
    ' -D archlib=/opt/channels/perl/5.44/lib/perl5/5.44/core_perl'
    ' -D sitelib=/opt/channels/perl/5.44/lib/perl5/5.44/site_perl'
    ' -D sitearch=/opt/channels/perl/5.44/lib/perl5/5.44/site_perl'
    ' -D vendorlib=/opt/channels/perl/5.44/lib/perl5/5.44/vendor_perl'
    ' -D vendorarch=/opt/channels/perl/5.44/lib/perl5/5.44/vendor_perl'
    ' -D man1dir=/opt/channels/perl/5.44/share/man/man1'
    ' -D man3dir=/opt/channels/perl/5.44/share/man/man3'
    ' -D pager="/usr/bin/cat" -D useshrplib -D usethreads'
    ' -D BUILD_ZLIB=0 -D BUILD_BZIP2=0'
)
P(name="perl-libs", version="5.44.0", description="libperl shared runtime library (system root)",
  license="GPL-1.0-or-later OR Artistic-1.0-Perl", url=PERL_URL,
  deps=["glibc >= 2.44", "gdbm-libs >= 1.26", "bzip2-libs >= 1.0.8", "zlib >= 1.3.2"],
  bdeps=["gdbm-dev >= 1.26", "bzip2-dev >= 1.0.8", "zlib-dev >= 1.3.2"],
  provides=["perl-libs", "so:libperl.so"],
  build=[PERL_CFG, "make -j$(nproc)"],
  install=["make install DESTDIR=$DESTDIR",
           "mkdir -p $DESTDIR/usr/lib",
           "find $DESTDIR" + PERL_PREFIX + " -name 'libperl.so*' -exec cp -a {} $DESTDIR/usr/lib/ \\;",
           "rm -rf $DESTDIR" + PERL_PREFIX + " $DESTDIR/usr/include $DESTDIR/usr/bin $DESTDIR/usr/sbin $DESTDIR/usr/share $DESTDIR/etc $DESTDIR/var $DESTDIR/usr/lib/pkgconfig 2>/dev/null || true",
           "find $DESTDIR/usr/lib $DESTDIR/usr/lib64 -type f \\( -name '*.a' -o -name '*.la' \\) -delete 2>/dev/null || true",
           "find $DESTDIR/usr/lib $DESTDIR/usr/lib64 \\( -type l -o -type f \\) -name '*.so' ! -name '*.so.*' -delete 2>/dev/null || true"])
P(name="perl", version="5.44.0",
  description="Perl 5.44 isolated language toolchain (/opt/channels/perl/5.44)",
  license="GPL-1.0-or-later OR Artistic-1.0-Perl", url=PERL_URL, channel="toolchain/perl:5.44",
  deps=["perl-libs >= 5.44.0", "gdbm-libs >= 1.26", "bzip2-libs >= 1.0.8", "zlib >= 1.3.2"],
  bdeps=["gdbm-dev >= 1.26", "bzip2-dev >= 1.0.8", "zlib-dev >= 1.3.2"],
  provides=["perl"],
  build=[PERL_CFG, "make -j$(nproc)"],
  install=["make install DESTDIR=$DESTDIR",
           "rm -f $DESTDIR/usr/lib/libperl.so $DESTDIR/usr/lib/libperl.so.*",
           "mkdir -p $DESTDIR/usr/bin",
           "ln -sfn " + PERL_PREFIX + "/bin/perl $DESTDIR/usr/bin/perl"])

# ---------- gcc / binutils / gcc-libs / xmake ----------
P(name="binutils", version="2.47", description="GNU binary utilities in gcc toolchain slot",
  license="GPL-3.0-or-later", url="https://mirrors.kernel.org/gnu/binutils/binutils-2.47.tar.xz",
  channel="toolchain/gcc:15",
  deps=["zlib >= 1.3.2", "zstd-libs >= 1.5.7", "gmp >= 6.3.0"],
  bdeps=["zlib-dev >= 1.3.2", "zstd-dev >= 1.5.7", "gmp-dev >= 6.3.0"],
  provides=["binutils", "so:libbfd-2.47.so", "so:libctf.so.0"],
  prepare=["mkdir -p build"],
  build=["cd build && ../configure --prefix=/opt/channels/gcc/15 --sysconfdir=/etc --enable-gold --enable-ld=default --enable-plugins --enable-shared --disable-werror --enable-64-bit-bfd --with-system-zlib --enable-default-hash-style=gnu --without-debuginfod --disable-gprofng",
         "cd build && make tooldir=/opt/channels/gcc/15 -j$(nproc)"],
  install=["cd build && make tooldir=/opt/channels/gcc/15 install DESTDIR=$DESTDIR",
           "rm -fv $DESTDIR/opt/channels/gcc/15/lib/lib{bfd,ctf,ctf-nobfd,gprofng,opcodes,sframe}.a 2>/dev/null || true"])
P(name="gcc", version="15.3.0", release="3", description="GNU Compiler Collection (C, C++) in toolchain/gcc:15",
  license="GPL-3.0-or-later", url="https://mirrors.kernel.org/gnu/gcc/gcc-15.3.0/gcc-15.3.0.tar.xz",
  channel="toolchain/gcc:15",
  deps=["binutils >= 2.47", "gmp >= 6.3.0", "mpfr >= 4.2.2", "mpc >= 1.4.1", "isl >= 0.27",
        "zstd-libs >= 1.5.7", "zlib >= 1.3.2"],
  bdeps=["gmp-dev >= 6.3.0", "mpfr-dev >= 4.2.2", "mpc-dev >= 1.4.1", "isl-dev >= 0.27",
         "zstd-dev >= 1.5.7", "zlib-dev >= 1.3.2"],
  provides=["gcc", "cc", "c++"],
  prepare=["rm -rf build && mkdir -p build"],
  build=["cd build && CPPFLAGS=\"-I/usr/include\" LDFLAGS=\"-L/usr/lib\" ../configure --prefix=/opt/channels/gcc/15 --enable-languages=c,c++ --enable-default-pie --enable-default-ssp --disable-multilib --disable-bootstrap --disable-fixincludes --with-system-zlib --with-isl=/usr",
         "cd build && make -j$(nproc)"],
  install=["cd build && make install DESTDIR=$DESTDIR",
           "ln -sf gcc $DESTDIR/opt/channels/gcc/15/bin/cc",
           "mkdir -p $DESTDIR/opt/channels/gcc/15/share/gdb/auto-load/usr/lib",
           "mv -v $DESTDIR/opt/channels/gcc/15/lib/*gdb.py $DESTDIR/opt/channels/gcc/15/share/gdb/auto-load/usr/lib 2>/dev/null || true"])
P(name="gcc-libs", version="15.3.0", description="GCC runtime libraries (libstdc++, libgcc_s) in /usr/lib",
  license="GPL-3.0-or-later WITH GCC-exception-3.1", channel="system",
  deps=["glibc >= 2.44"], bdeps=["gcc >= 15.0"],
  provides=["gcc-libs", "so:libstdc++.so.6", "so:libgcc_s.so.1"],
  build=[],
  install=[
      "GCC_PKG=\"$(ls -t ../gcc/gcc-*.pkg.tar.zst ../../repo/gcc-*.pkg.tar.zst 2>/dev/null | head -n1)\"; [ -n \"$GCC_PKG\" ] || { echo 'ERROR: gcc package artifact not found - build recipes/gcc first' >&2; exit 1; }; X=\"$(mktemp -d)\"; tar --zstd -xf \"$GCC_PKG\" -C \"$X\" \"data/opt/channels/gcc/15/lib64\" 2>/dev/null || zstd -dc \"$GCC_PKG\" 2>/dev/null | tar -xf - -C \"$X\" \"data/opt/channels/gcc/15/lib64\"; if [ -n \"$(ls -A \"$X\"/data/opt/channels/gcc/15/lib64/ 2>/dev/null)\" ]; then mkdir -p \"$DESTDIR/usr/lib\"; cp -av \"$X\"/data/opt/channels/gcc/15/lib64/libstdc++.so* \"$DESTDIR/usr/lib/\"; cp -av \"$X\"/data/opt/channels/gcc/15/lib64/libgcc_s.so* \"$DESTDIR/usr/lib/\"; cp -av \"$X\"/data/opt/channels/gcc/15/lib64/libatomic.so* \"$DESTDIR/usr/lib/\" 2>/dev/null || true; cp -av \"$X\"/data/opt/channels/gcc/15/lib64/libgomp.so* \"$DESTDIR/usr/lib/\" 2>/dev/null || true; if [ -e \"$DESTDIR/usr/lib/libstdc++.so.6\" ]; then RC=0; else echo 'ERROR: libstdc++.so.6 missing after extraction' >&2; RC=1; fi; else echo 'ERROR: failed to extract GCC runtime libraries from gcc package' >&2; RC=1; fi; rm -rf \"$X\"; exit $RC",
  ])
P(name="xmake", version="3.1.0", release="3", description="xmake C/C++ build utility in toolchain/xmake:3",
  license="Apache-2.0", url="https://github.com/xmake-io/xmake/releases/download/v3.1.0/xmake-v3.1.0.tar.gz",
  channel="toolchain/xmake:3", deps=["gcc-libs >= 15.3.0"], provides=["xmake"],
  build=["./configure --runtime=lua --prefix=/opt/channels/xmake/3", "make -j$(nproc)"],
  install=["make install DESTDIR=$DESTDIR PREFIX=/opt/channels/xmake/3",
           "if [ -e \"$DESTDIR/usr/bin/xmake\" ] && [ ! -e \"$DESTDIR/opt/channels/xmake/3/bin/xmake\" ]; then mkdir -p \"$DESTDIR/opt/channels/xmake/3\"; mv \"$DESTDIR/usr/bin\" \"$DESTDIR/opt/channels/xmake/3/bin\"; mkdir -p \"$DESTDIR/opt/channels/xmake/3/share\"; mv \"$DESTDIR/usr/share/xmake\" \"$DESTDIR/opt/channels/xmake/3/share/xmake\" 2>/dev/null || true; rmdir \"$DESTDIR/usr/share\" \"$DESTDIR/usr/bin\" 2>/dev/null || true; fi",
           "[ -x \"$DESTDIR/opt/channels/xmake/3/bin/xmake\" ] || { echo 'ERROR: xmake not installed at /opt/channels/xmake/3/bin/xmake' >&2; exit 1; }"])

# ---------- glibc / linux-zen-headers (UAPI bound to linux-zen 7.1.9) ----------
LINUX_ZEN_URL = "https://mirrors.tuna.tsinghua.edu.cn/kernel/v7.x/linux-7.1.9.tar.xz"
P(name="linux-zen-headers", version="7.1.9", description="Linux ZEN UAPI headers (bound to linux-zen 7.1.9)",
  license="GPL-2.0-only", url=LINUX_ZEN_URL,
  deps=[], provides=["linux-zen-headers", "linux-headers", "virtual/linux-headers"],
  build=["make headers"],
  install=["find usr/include -type f ! -name '*.h' -delete",
           "mkdir -p $DESTDIR/usr/include",
           "cp -rv usr/include/* $DESTDIR/usr/include/"])
P(name="glibc", version="2.44", description="GNU C Library runtime, loader and locale data",
  license="LGPL-2.1-or-later", url="https://mirrors.kernel.org/gnu/glibc/glibc-2.44.tar.xz",
  deps=["linux-headers >= 7.1.9"],
  bdeps=[],
  provides=["glibc", "virtual/libc", "so:libc.so.6", "so:ld-linux-x86-64.so.2"],
  prepare=["mkdir -p build"],
  build=["cd build && ../configure --prefix=/usr --disable-profile --enable-kernel=4.19 --enable-stack-protector=strong --disable-nscd libc_cv_slibdir=/usr/lib",
         "cd build && make -j$(nproc)"],
  install=["cd build && make install DESTDIR=$DESTDIR",
           "mkdir -p $DESTDIR/usr/lib/locale",
           "sed '/RTLDLIST/s@/usr@@g' -i $DESTDIR/usr/bin/ldd 2>/dev/null || true",
           "rm -rf $DESTDIR/usr/include $DESTDIR/usr/lib/*.a $DESTDIR/usr/lib/pkgconfig 2>/dev/null || true",
           "rm -f $DESTDIR/usr/lib/libc.so $DESTDIR/usr/lib/libm.so $DESTDIR/usr/lib/libpthread.so $DESTDIR/usr/lib/librt.so $DESTDIR/usr/lib/libdl.so $DESTDIR/usr/lib/libutil.so $DESTDIR/usr/lib/libresolv.so"])
P(name="glibc-dev", version="2.44", description="GNU C Library headers, static libraries and linker scripts",
  license="LGPL-2.1-or-later", url="https://mirrors.kernel.org/gnu/glibc/glibc-2.44.tar.xz",
  deps=["glibc >= 2.44", "linux-headers >= 7.1.9"],
  bdeps=[],
  provides=["glibc-dev"],
  prepare=["rm -rf build && mkdir -p build"],
  build=["cd build && ../configure --prefix=/usr --enable-kernel=4.19 --enable-stack-protector=strong --disable-profile --disable-werror --without-gd libc_cv_slibdir=/usr/lib",
         "cd build && make -j$(nproc)"],
  install=["cd build && make install DESTDIR=$DESTDIR",
           "rm -f $DESTDIR/usr/lib/libc.so.6 $DESTDIR/usr/lib/ld-linux*.so.*",
           "rm -rf $DESTDIR/usr/bin $DESTDIR/usr/sbin $DESTDIR/etc $DESTDIR/var $DESTDIR/usr/share"])

# ---------- core GNU userland ----------
def gnu_cli(name, ver, url, desc, license="GPL-3.0-or-later", extra_cfg="", extra_deps=None, extra_bdeps=None, extra_install=None, extra_provides=None, extra_prepare=None, extra_env="", sha256=""):
    cfg = f"./configure --prefix=/usr{extra_cfg}"
    if extra_env:
        cfg = extra_env + " " + cfg
    kw = dict(name=name, version=ver, description=desc, license=license, url=url,
              deps=extra_deps or [],
              bdeps=["gettext >= 1.0"] if extra_bdeps is None else extra_bdeps,
              provides=extra_provides or [name],
              prepare=extra_prepare or [],
              build=[cfg, "make -j$(nproc)"],
              install=AT_INSTALL + (extra_install or []))
    if sha256:
        kw["sha256"] = sha256
    P(**kw)

gnu_cli("m4", "1.4.21", "https://mirrors.kernel.org/gnu/m4/m4-1.4.21.tar.xz", "GNU macro processor", extra_bdeps=[])
gnu_cli("make", "4.4.1", "https://mirrors.kernel.org/gnu/make/make-4.4.1.tar.gz", "GNU make", extra_cfg=" --disable-nls", extra_bdeps=[])
gnu_cli("tar", "1.35", "https://mirrors.kernel.org/gnu/tar/tar-1.35.tar.xz", "GNU tar archiving utility", extra_cfg=" --without-posix-acls --disable-nls", extra_env="FORCE_UNSAFE_CONFIGURE=1", extra_bdeps=[])
gnu_cli("gzip", "1.14", "https://mirrors.kernel.org/gnu/gzip/gzip-1.14.tar.xz", "GNU gzip compression utilities", extra_cfg=" --disable-nls", extra_bdeps=[])
gnu_cli("findutils", "4.11.0", "https://mirrors.kernel.org/gnu/findutils/findutils-4.11.0.tar.xz", "GNU find and xargs", extra_cfg=" --localstatedir=/var/lib/locate --disable-nls", extra_bdeps=[])
gnu_cli("gawk", "5.4.1", "https://mirrors.kernel.org/gnu/gawk/gawk-5.4.1.tar.xz", "GNU awk", extra_cfg=" --disable-nls", extra_provides=["gawk", "awk"], extra_install=["ln -sf gawk $DESTDIR/usr/bin/awk"], extra_bdeps=[])
gnu_cli("grep", "3.12", "https://mirrors.kernel.org/gnu/grep/grep-3.12.tar.xz", "GNU grep", extra_cfg=" --disable-nls", extra_deps=["pcre2-libs >= 10.47"], extra_bdeps=["pcre2-dev >= 10.47"])
gnu_cli("sed", "4.10", "https://mirrors.kernel.org/gnu/sed/sed-4.10.tar.xz", "GNU stream editor", extra_cfg=" --disable-nls", extra_bdeps=[])
gnu_cli("patch", "2.8", "https://mirrors.kernel.org/gnu/patch/patch-2.8.tar.xz", "Apply a diff file to an original", extra_bdeps=[])
gnu_cli("diffutils", "3.12", "https://mirrors.kernel.org/gnu/diffutils/diffutils-3.12.tar.xz", "GNU diff and cmp", extra_cfg=" --disable-nls", extra_bdeps=[])
gnu_cli("coreutils", "9.11", "https://ftpmirror.gnu.org/coreutils/coreutils-9.11.tar.xz", "GNU core utilities",
        extra_cfg=" --enable-install-program=hostname --enable-no-install-program=kill,uptime --disable-nls",
        extra_env="FORCE_UNSAFE_CONFIGURE=1",
        extra_deps=["attr-libs >= 2.6.0", "acl-libs >= 2.4.0", "gmp >= 6.3.0", "libcap-libs >= 2.78", "openssl-libs >= 4.0.1"],
        extra_bdeps=["attr-dev >= 2.6.0", "acl-dev >= 2.4.0", "gmp-dev >= 6.3.0", "libcap-dev >= 2.78", "openssl-dev >= 4.0.1"])
gnu_cli("bison", "3.8.2", "https://mirrors.kernel.org/gnu/bison/bison-3.8.2.tar.xz", "GNU parser generator", extra_cfg=" --docdir=/usr/share/doc/bison-3.8.2", extra_deps=["m4 >= 1.4.21"], extra_bdeps=["gettext >= 1.0", "m4 >= 1.4.21"])
gnu_cli("gperf", "3.3", "https://mirrors.kernel.org/gnu/gperf/gperf-3.3.tar.gz", "Perfect hash function generator", extra_cfg=" --docdir=/usr/share/doc/gperf-3.3", extra_bdeps=[])
gnu_cli("texinfo", "7.3", "https://mirrors.kernel.org/gnu/texinfo/texinfo-7.3.tar.xz", "GNU documentation system", extra_deps=["perl >= 5.44.0", "ncurses-libs >= 6.6"], extra_bdeps=["perl >= 5.44.0", "ncurses-dev >= 6.6"])
gnu_cli("autoconf", "2.73", "https://mirrors.kernel.org/gnu/autoconf/autoconf-2.73.tar.xz", "GNU autoconf", extra_deps=["perl >= 5.44.0", "m4 >= 1.4.21"], extra_bdeps=["perl >= 5.44.0", "m4 >= 1.4.21"])
gnu_cli("automake", "1.18.1", "https://ftpmirror.gnu.org/automake/automake-1.18.1.tar.xz", "GNU automake", extra_cfg=" --docdir=/usr/share/doc/automake-1.18.1", extra_deps=["perl >= 5.44.0", "autoconf >= 2.73"], extra_bdeps=["perl >= 5.44.0", "autoconf >= 2.71"])
gnu_cli("which", "2.25", "https://ftp.gnu.org/gnu/which/which-2.25.tar.gz", "GNU which", extra_bdeps=[], sha256="1cb83e4f702e60b8211ab5ec4c2afbab1b1dec80209456a7d2faf7584ed225ea")
gnu_cli("groff", "1.24.1", "https://ftpmirror.gnu.org/groff/groff-1.24.1.tar.gz", "GNU troff", extra_bdeps=["perl >= 5.44.0"])
gnu_cli("nano", "9.2", "https://mirrors.kernel.org/gnu/nano/nano-9.2.tar.xz", "Pico editor clone", extra_cfg=" --sysconfdir=/etc --enable-utf8", extra_deps=["ncurses-libs >= 6.6", "file-libs >= 5.48"], extra_bdeps=["ncurses-dev >= 6.6", "file-dev >= 5.48"], extra_provides=["nano", "editor"], sha256="05ecb99247b782e8a5b3a25ed4101dd034b0236902f7449bc9795b717642f7e9")

P(name="bash", version="5.3.15", description="GNU Bourne Again SHell (patches 001-015)",
  license="GPL-3.0-or-later", url="https://mirrors.kernel.org/gnu/bash/bash-5.3.tar.gz",
  deps=["readline >= 8.3", "ncurses-libs >= 6.6"],
  bdeps=["readline-dev >= 8.3", "ncurses-dev >= 6.6"],
  provides=["bash", "sh"],
  prepare=['if [ -f "$RECIPE_DIR/bash-5.3.15.patch" ]; then patch -Np0 -i "$RECIPE_DIR/bash-5.3.15.patch"; fi'],
  build=["./configure --prefix=/usr --without-bash-malloc --with-installed-readline", "make -j$(nproc)"],
  install=AT_INSTALL + ["ln -sf bash $DESTDIR/usr/bin/sh"])

P(name="flex", version="2.6.4", description="Lexical analyzer generator",
  license="BSD-2-Clause", url="https://github.com/westes/flex/releases/download/v2.6.4/flex-2.6.4.tar.gz",
  deps=["m4 >= 1.4.21"], bdeps=["m4 >= 1.4.21", "bison >= 3.8.2"],
  provides=["flex"],
  build=AT_BUILD, install=AT_INSTALL + ["ln -sf flex $DESTDIR/usr/bin/lex"])

P(name="pkgconf", version="3.0.5", description="pkg-config compatible compiler/linker helper",
  license="ISC", url="https://github.com/pkgconf/pkgconf/releases/download/pkgconf-3.0.5/pkgconf-3.0.5.tar.xz",
  deps=["glibc >= 2.44"], provides=["pkgconf"],
  build=AT_BUILD, install=AT_INSTALL + [
      "ln -sf pkgconf $DESTDIR/usr/bin/pkg-config",
      "ln -sf pkgconf.1 $DESTDIR/usr/share/man/man1/pkg-config.1 2>/dev/null || true",
  ])

# ---------- python packaging stack ----------
P(name="flit-core", version="4.0.2", description="PEP 517 build backend (flit_core)",
  license="BSD-3-Clause",
  url="https://files.pythonhosted.org/packages/46/ef/34533186e76c526d9ec17a1ad9a10c7354cbfb20f51583cc36dfe4bdccd0/flit_core-4.0.2.tar.gz",
  deps=["python >= 3.14.7"], bdeps=["python >= 3.14.7"], provides=["flit-core"],
  build=["python3 -m flit_core.wheel"],
  install=["python3 bootstrap_install.py --install-root=$DESTDIR dist/flit_core-*.whl"])
P(name="packaging", version="26.3", description="Python packaging core utilities",
  license="Apache-2.0 OR BSD-2-Clause",
  url="https://files.pythonhosted.org/packages/7d/fa/3944b40b07da9ce895c0e6303a5ab7d53da063554f534556b134a54d6093/packaging-26.3.tar.gz",
  deps=["python >= 3.14.7", "flit-core >= 4.0.2"], bdeps=["python >= 3.14.7", "flit-core >= 3.9.0"],
  provides=["packaging"],
  build=["python3 -m flit_core.wheel"],
  install=["mkdir -p $DESTDIR/usr/lib/python3.14/site-packages",
           "python3 -c \"import sys, zipfile, glob; [zipfile.ZipFile(f).extractall(sys.argv[1]) for f in glob.glob('dist/*.whl')]\" $DESTDIR/usr/lib/python3.14/site-packages"])
P(name="wheel", version="0.48.0", description="Python wheel packaging tool",
  license="MIT",
  url="https://files.pythonhosted.org/packages/d0/20/50ed6bdf27dec98b568a8ae25dc599f5baa3d9709f9e83fd1edb56b9a90/wheel-0.48.0.tar.gz",
  deps=["python >= 3.14.7", "flit-core >= 4.0.2"], bdeps=["python >= 3.14.7", "flit-core >= 3.9.0"],
  provides=["wheel"],
  build=["python3 -m flit_core.wheel"],
  install=["mkdir -p $DESTDIR/usr/lib/python3.14/site-packages",
           "python3 -c \"import sys, zipfile, glob; [zipfile.ZipFile(f).extractall(sys.argv[1]) for f in glob.glob('dist/*.whl')]\" $DESTDIR/usr/lib/python3.14/site-packages"])
P(name="setuptools", version="84.0.0", description="Python setuptools",
  license="MIT",
  url="https://files.pythonhosted.org/packages/6d/44/f5da03a8ef95d369145c5bb53050e7877c9f3d312e128605fd9504829143/setuptools-84.0.0.tar.gz",
  deps=["python >= 3.14.7", "packaging >= 26.3"], bdeps=["python >= 3.14.7", "flit-core >= 3.9.0", "wheel >= 0.40.0"],
  provides=["setuptools"],
  build=["python3 -c \"import setuptools.build_meta as b; b.build_wheel('dist')\""],
  install=["mkdir -p $DESTDIR/usr/lib/python3.14/site-packages",
           "python3 -c \"import sys, zipfile, glob; [zipfile.ZipFile(f).extractall(sys.argv[1]) for f in glob.glob('dist/*.whl')]\" $DESTDIR/usr/lib/python3.14/site-packages"])
P(name="markupsafe", version="3.0.3", description="Python HTML/XML string escape library",
  license="BSD-3-Clause",
  url="https://files.pythonhosted.org/packages/7e/99/7690b6d4034fffd95959cbe0c02de8deb3098cc577c67bb6a24fe5d7caa7/markupsafe-3.0.3.tar.gz",
  deps=["python >= 3.14.7"], bdeps=["python >= 3.14.7", "setuptools >= 68.0"],
  provides=["markupsafe"],
  build=["python3 -c \"import setuptools.build_meta as b; b.build_wheel('dist')\""],
  install=["mkdir -p $DESTDIR/usr/lib/python3.14/site-packages",
           "python3 -c \"import sys, zipfile, glob; [zipfile.ZipFile(f).extractall(sys.argv[1]) for f in glob.glob('dist/*.whl')]\" $DESTDIR/usr/lib/python3.14/site-packages"])
P(name="jinja2", version="3.1.6", description="Python Jinja2 template engine",
  license="BSD-3-Clause",
  url="https://files.pythonhosted.org/packages/df/bf/f7da0350254c0ed7c72f3e33cef02e048281fec7ecec5f032d4aac52226b/jinja2-3.1.6.tar.gz",
  deps=["python >= 3.14.7", "markupsafe >= 3.0.3"], bdeps=["python >= 3.14.7", "flit-core >= 3.9.0", "wheel >= 0.40.0"],
  provides=["jinja2"],
  build=["python3 -m flit_core.wheel"],
  install=["mkdir -p $DESTDIR/usr/lib/python3.14/site-packages",
           "python3 -c \"import sys, zipfile, glob; [zipfile.ZipFile(f).extractall(sys.argv[1]) for f in glob.glob('dist/*.whl')]\" $DESTDIR/usr/lib/python3.14/site-packages"])
P(name="ninja", version="1.13.2", description="Small build system with a focus on speed",
  license="Apache-2.0", url="https://github.com/ninja-build/ninja/archive/refs/tags/v1.13.2.tar.gz",
  deps=["gcc-libs >= 15.3.0"], bdeps=["python >= 3.14.7"], provides=["ninja"],
  build=["python3 configure.py --bootstrap --verbose"],
  install=["install -vDm755 ninja $DESTDIR/usr/bin/ninja",
           "install -vDm644 misc/bash-completion $DESTDIR/usr/share/bash-completion/completions/ninja 2>/dev/null || true",
           "install -vDm644 misc/zsh-completion $DESTDIR/usr/share/zsh/site-functions/_ninja 2>/dev/null || true"])
P(name="meson", version="1.12.0", description="Meson build system",
  license="Apache-2.0",
  url="https://files.pythonhosted.org/packages/48/91/d58a3eb45ed54bf32b96806dd2f4efd407f7a9675953e15e8ef257840a0d/meson-1.12.0.tar.gz",
  deps=["python >= 3.14.7", "ninja >= 1.13.2"], bdeps=["python >= 3.14.7"], provides=["meson"],
  build=["python3 -c \"import setuptools.build_meta as b; b.build_wheel('dist')\""],
  install=["mkdir -p $DESTDIR/usr/lib/python3.14/site-packages $DESTDIR/usr/bin",
           "python3 -c \"import sys, zipfile, glob; [zipfile.ZipFile(f).extractall(sys.argv[1]) for f in glob.glob('dist/*.whl')]\" $DESTDIR/usr/lib/python3.14/site-packages",
           "sed '1s|^#!.*|#!/usr/bin/python3|' meson.py > $DESTDIR/usr/bin/meson",
           "chmod 755 $DESTDIR/usr/bin/meson",
           "install -vDm644 data/shell-completions/bash/meson $DESTDIR/usr/share/bash-completion/completions/meson 2>/dev/null || true",
           "install -vDm644 data/shell-completions/zsh/_meson $DESTDIR/usr/share/zsh/site-functions/_meson 2>/dev/null || true"])
P(name="cmake", version="4.4.2", description="Cross-platform build system",
  license="BSD-3-Clause", url="https://github.com/Kitware/CMake/releases/download/v4.4.2/cmake-4.4.2.tar.gz",
  sha256="1db9e61e60b6e0874c86386340b910382f3c5e75b9fbfb44d122063129a2789d",
  deps=["glibc >= 2.44", "gcc-libs >= 15.3.0", "openssl-libs >= 4.0.1", "zlib >= 1.3.2",
        "bzip2-libs >= 1.0.8", "xz-libs >= 5.8.3", "zstd-libs >= 1.5.7", "curl-libs >= 8.21.0",
        "expat-libs >= 2.8.3", "libarchive-libs >= 3.8.9", "ncurses-libs >= 6.6"],
  bdeps=["openssl-dev >= 4.0.1", "zlib-dev >= 1.3.2", "curl-dev >= 8.21.0", "expat-dev >= 2.8.3",
         "libarchive-dev >= 3.8.9", "ncurses-dev >= 6.6", "pkgconf >= 3.0.5"],
  provides=["cmake"],
  build=['CFLAGS="$CFLAGS -fpermissive" CXXFLAGS="$CXXFLAGS -fpermissive" ./bootstrap --prefix=/usr --parallel=$(nproc) --system-curl --system-zlib --system-bzip2 --system-zstd --system-liblzma --system-libarchive --system-expat --no-system-jsoncpp --no-system-librhash --no-system-cppdap',
         "make -j$(nproc)"],
  install=AT_INSTALL)

# ---------- remaining system utilities ----------
P(name="bc", version="7.0.3", description="Arbitrary precision numeric processing language",
  license="BSD-2-Clause", url="https://github.com/gavinhoward/bc/releases/download/7.0.3/bc-7.0.3.tar.xz",
  deps=["readline >= 8.3"], bdeps=["readline-dev >= 8.3"], provides=["bc"],
  build=['CC=gcc CFLAGS="-std=gnu11 -O3" ./configure --prefix=/usr -G -O3', "make -j$(nproc)"],
  install=AT_INSTALL)
P(name="less", version="704", description="Terminal pager",
  license="GPL-3.0-or-later OR BSD-2-Clause", url="https://ftpmirror.gnu.org/less/less-704.tar.gz",
  deps=["ncurses-libs >= 6.6", "pcre2-libs >= 10.47"],
  bdeps=["ncurses-dev >= 6.6", "pcre2-dev >= 10.47"], provides=["less"],
  build=["./configure --prefix=/usr --sysconfdir=/etc --with-regex=pcre2", "make -j$(nproc)"],
  install=AT_INSTALL)
P(name="psmisc", version="23.7", description="Process management utilities (fuser, killall, pstree)",
  license="GPL-2.0-or-later", url="https://downloads.sourceforge.net/project/psmisc/psmisc/psmisc-23.7.tar.xz",
  deps=["ncurses-libs >= 6.6"], bdeps=["ncurses-dev >= 6.6", "gettext >= 1.0"], provides=["psmisc"],
  build=AT_BUILD, install=AT_INSTALL)
P(name="procps-ng-libs", version="4.0.7", description="libproc2 shared runtime library",
  license="LGPL-2.1-or-later", url="https://downloads.sourceforge.net/project/procps-ng/Production/procps-ng-4.0.7.tar.xz",
  deps=["ncurses-libs >= 6.6", "systemd-libs >= 261.2"],
  bdeps=["pkgconf >= 3.0.5", "ncurses-dev >= 6.6"],
  provides=["procps-ng-libs", "so:libproc2.so.0"],
  build=["./configure --prefix=/usr --disable-static --disable-kill --enable-watch8bit", "make -j$(nproc)"],
  install=AT_INSTALL + STRIP_LIBS)
P(name="procps-ng", version="4.0.7", description="Process and system monitoring utilities (ps, top, free)",
  license="GPL-2.0-or-later", url="https://downloads.sourceforge.net/project/procps-ng/Production/procps-ng-4.0.7.tar.xz",
  deps=["procps-ng-libs >= 4.0.7", "ncurses-libs >= 6.6", "systemd-libs >= 261.2"],
  bdeps=["pkgconf >= 3.0.5", "ncurses-dev >= 6.6"],
  provides=["procps-ng"],
  build=["./configure --prefix=/usr --docdir=/usr/share/doc/procps-ng-4.0.7 --disable-static --disable-kill --enable-watch8bit",
         "make -j$(nproc)"],
  install=AT_INSTALL + STRIP_CLI)
P(name="procps-ng-dev", version="4.0.7", description="libproc2 headers, pkg-config and linker symlink",
  license="LGPL-2.1-or-later", url="https://downloads.sourceforge.net/project/procps-ng/Production/procps-ng-4.0.7.tar.xz",
  deps=["procps-ng-libs >= 4.0.7"],
  bdeps=["pkgconf >= 3.0.5", "ncurses-dev >= 6.6"],
  provides=["procps-ng-dev"],
  build=["./configure --prefix=/usr --disable-static --disable-kill --enable-watch8bit", "make -j$(nproc)"],
  install=AT_INSTALL + STRIP_DEV)
P(name="inetutils", version="2.8", description="Basic networking utilities (ping, hostname, ftp, ...)",
  license="GPL-3.0-or-later", url="https://ftpmirror.gnu.org/inetutils/inetutils-2.8.tar.gz",
  deps=["ncurses-libs >= 6.6", "readline >= 8.3", "libxcrypt >= 4.5.2"],
  bdeps=["ncurses-dev >= 6.6", "readline-dev >= 8.3", "libxcrypt-dev >= 4.5.2"],
  provides=["inetutils"],
  build=["./configure --prefix=/usr --bindir=/usr/bin --localstatedir=/var --disable-logger --disable-whois --disable-rcp --disable-rexec --disable-rlogin --disable-rsh --disable-servers --disable-telnet",
         "make -j$(nproc)"],
  install=AT_INSTALL + [
      "mkdir -p $DESTDIR/usr/sbin",
      "mv -v $DESTDIR/usr/bin/ifconfig $DESTDIR/usr/sbin/ 2>/dev/null || true",
      "mv -v $DESTDIR/usr/bin/hostname $DESTDIR/usr/sbin/ 2>/dev/null || true",
  ])
P(name="iproute2", version="7.1.0", description="IP routing and traffic control utilities",
  license="GPL-2.0-or-later", url="https://mirrors.edge.kernel.org/pub/linux/utils/net/iproute2/iproute2-7.1.0.tar.xz",
  deps=["libcap-libs >= 2.78", "libelf >= 0.196"],
  bdeps=["bison >= 3.8.2", "flex >= 2.6.4", "pkgconf >= 3.0.5", "libcap-dev >= 2.78", "libelf-dev >= 0.196", "linux-headers >= 7.1.9"],
  provides=["iproute2"],
  build=["make -j$(nproc)"],
  install=["make DESTDIR=$DESTDIR install"])
P(name="kbd", version="2.10.0", description="Keyboard and console utilities",
  license="GPL-2.0-or-later", url="https://mirrors.edge.kernel.org/pub/linux/utils/kbd/kbd-2.10.0.tar.xz",
  deps=["linux-headers >= 7.1.9"], bdeps=["linux-headers >= 7.1.9", "flex >= 2.6.4"],
  provides=["kbd"],
  build=["./configure --prefix=/usr --disable-vlock", "make -j$(nproc)"],
  install=AT_INSTALL + ["rm -rf $DESTDIR/usr/share/doc/kbd/examples 2>/dev/null || true"])
P(name="shadow", version="4.20.2", description="Password and account management utilities",
  license="BSD-3-Clause", url="https://github.com/shadow-maint/shadow/releases/download/4.20.2/shadow-4.20.2.tar.gz",
  deps=["libxcrypt >= 4.5.2", "util-linux-libs >= 2.42.2", "acl-libs >= 2.4.0", "attr-libs >= 2.6.0"],
  bdeps=["libxcrypt-dev >= 4.5.2", "util-linux-dev >= 2.42.2", "acl-dev >= 2.4.0", "attr-dev >= 2.6.0"],
  provides=["shadow"],
  prepare=[
      "find man -name Makefile.in -exec sed -i 's/getspnam\\.3 / /' {} \\;",
      "find man -name Makefile.in -exec sed -i 's/passwd\\.5 / /' {} \\;",
      "sed -e 's:#ENCRYPT_METHOD DES:ENCRYPT_METHOD YESCRYPT:' -e 's:/var/spool/mail:/var/mail:' -e '/PATH=/{s@/sbin:@@;s@/bin:@@}' -i etc/login.defs",
      "sed -i '/stdio.h/i #include <stdint.h>' lib/find_new_sub_*ids.c",
  ],
  build=["touch /usr/bin/passwd 2>/dev/null || true",
         "./configure --sysconfdir=/etc --disable-static --with-{b,yes}crypt --without-libbsd --disable-logind --with-group-name-max-length=32",
         "make -j$(nproc)"],
  install=["make exec_prefix=/usr install DESTDIR=$DESTDIR",
           "make -C man install-man DESTDIR=$DESTDIR"])
P(name="man-pages", version="6.18", description="Linux man pages",
  license="GPL-2.0-or-later AND BSD-3-Clause",
  url="https://mirrors.kernel.org/pub/linux/docs/man-pages/man-pages-6.18.tar.xz",
  deps=[], provides=["man-pages"],
  build=[],
  install=["make -R prefix=/usr install DESTDIR=$DESTDIR"])
P(name="iana-etc", version="20260811", description="IANA /etc/services and /etc/protocols",
  license="custom:none", url="https://github.com/Mic92/iana-etc/releases/download/20260811/iana-etc-20260811.tar.gz",
  deps=[], provides=["iana-etc"],
  build=[],
  install=["mkdir -p $DESTDIR/etc", "cp services protocols $DESTDIR/etc/"])
P(name="man-db", version="2.13.1", description="Manual page browsing utilities",
  license="GPL-2.0-or-later", url="https://download.savannah.gnu.org/releases/man-db/man-db-2.13.1.tar.xz",
  deps=["libpipeline >= 1.5.8", "gdbm-libs >= 1.26", "groff >= 1.24.1", "less >= 704", "xz-libs >= 5.8.3", "zlib >= 1.3.2"],
  bdeps=["pkgconf >= 3.0.5", "libpipeline-dev >= 1.5.8", "gdbm-dev >= 1.26", "zlib-dev >= 1.3.2"],
  provides=["man-db"],
  prepare=["sed -i '/SUBDIRS/s/manual//' Makefile.in"],
  build=["./configure --prefix=/usr --docdir=/usr/share/doc/man-db-2.13.1 --sysconfdir=/etc --disable-setuid --enable-cache-owner=bin --with-browser=/usr/bin/lynx --with-vgrind=/usr/bin/vgrind --with-grap=/usr/bin/grap --disable-manual",
         "make -j$(nproc)"],
  install=AT_INSTALL)
P(name="dejagnu", version="1.6.3", description="DejaGNU test framework",
  license="GPL-3.0-or-later", url="https://ftp.gnu.org/gnu/dejagnu/dejagnu-1.6.3.tar.gz",
  deps=["expect >= 5.45.4", "tcl >= 8.6.18"], provides=["dejagnu"],
  build=["./configure --prefix=/usr", "make -j$(nproc)"],
  install=AT_INSTALL)

# ---------- systemd (function split) / dbus ----------
SD_URL = "https://github.com/systemd/systemd/archive/refs/tags/v261.2.tar.gz"
SD_DEPS = ["kmod-libs >= 34.2", "util-linux-libs >= 2.42.2", "libcap-libs >= 2.78", "zstd-libs >= 1.5.7",
           "lz4-libs >= 1.10.0", "xz-libs >= 5.8.3", "openssl-libs >= 4.0.1", "pcre2-libs >= 10.47",
           "glibc >= 2.44", "libseccomp >= 2.6.0", "acl-libs >= 2.4.0", "libxcrypt >= 4.5.2"]
SD_BDEPS = ["meson >= 1.12.0", "ninja >= 1.13.2", "python >= 3.14.7", "jinja2 >= 3.1.6", "markupsafe >= 3.0.3",
            "gperf >= 3.3", "gettext >= 1.0", "kmod-dev >= 34.2", "util-linux-dev >= 2.42.2",
            "openssl-dev >= 4.0.1", "pkgconf >= 3.0.5", "libcap-dev >= 2.78", "pcre2-dev >= 10.47",
            "libseccomp-dev >= 2.6.0", "acl-dev >= 2.4.0", "libxcrypt-dev >= 4.5.2"]
SD_PREP = ["sed -e 's/GROUP=\"render\"/GROUP=\"video\"/' -e 's/GROUP=\"sgx\", //' -i rules.d/50-udev-default.rules.in"]
SD_BUILD = ["mkdir -p build",
            "cd build && meson setup .. --prefix=/usr --buildtype=release -D default-dnssec=no -D firstboot=false -D install-tests=false -D ldconfig=false -D sysusers=false -D rpmmacrosdir=no -D homed=disabled -D man=disabled -D mode=release -D pamconfdir=no -D dev-kvm-mode=0660 -D nobody-group=nogroup -D sysupdate=disabled -D ukify=disabled -D docdir=/usr/share/doc/systemd-261.2",
            "cd build && ninja -j$(nproc)"]
SD_KEEP_LIBS = [
    "usr/lib/libsystemd.so.*",
    "usr/lib/libudev.so.*",
    "usr/lib/systemd/libsystemd-shared*",
    "usr/lib/systemd/libsystemd-core*",
    "usr/lib/libnss_systemd.so*",
    "usr/lib/libnss_myhostname.so*",
    "usr/lib/libnss_mymachines.so*",
]
SD_KEEP_UDEV = [
    "usr/bin/udevadm",
    "usr/bin/systemd-hwdb",
    "usr/lib/systemd/systemd-udevd",
    "usr/lib/systemd/systemd-hwdb",
    "usr/lib/udev",
    "usr/lib/hwdb.d",
    "etc/udev",
    "usr/lib/modprobe.d",
    "usr/lib/systemd/network/*.link",
    "usr/lib/systemd/system/systemd-udevd*",
    "usr/lib/systemd/system/systemd-udev-*",
    "usr/lib/systemd/system/systemd-hwdb*",
    "usr/lib/systemd/system/initrd-udevadm*",
    "usr/lib/systemd/system/*/systemd-udevd*",
    "usr/lib/systemd/system/*/systemd-udev-*",
    "usr/lib/systemd/system/*/systemd-hwdb*",
]
SD_KEEP_NETWORKD = [
    "usr/bin/networkctl",
    "usr/lib/systemd/systemd-networkd",
    "usr/lib/systemd/systemd-networkd-wait-online",
    "usr/lib/systemd/systemd-network-generator",
    "usr/lib/systemd/network/*.network",
    "usr/lib/systemd/network/*.netdev",
    "etc/systemd/network",
    "usr/lib/systemd/system/systemd-networkd*",
    "usr/lib/systemd/system/systemd-network-generator*",
    "usr/lib/systemd/system/*/systemd-networkd*",
    "usr/lib/systemd/system/*/systemd-network-generator*",
    "usr/lib/systemd/system/dbus-org.freedesktop.network1.service",
]
SD_KEEP_RESOLVED = [
    "usr/bin/resolvectl",
    "usr/bin/systemd-resolve",
    "usr/lib/systemd/systemd-resolved",
    "usr/lib/libnss_resolve.so*",
    "etc/systemd/resolved.conf",
    "etc/systemd/resolved.conf.d",
    "usr/lib/systemd/resolv.conf",
    "usr/lib/systemd/system/systemd-resolved*",
    "usr/lib/systemd/system/*/systemd-resolved*",
    "usr/lib/systemd/system/dbus-org.freedesktop.resolve1.service",
]
SD_KEEP_TIMESYNCD = [
    "usr/lib/systemd/systemd-timesyncd",
    "etc/systemd/timesyncd.conf",
    "etc/systemd/timesyncd.conf.d",
    "usr/lib/systemd/ntp-units.d",
    "usr/lib/systemd/system/systemd-timesyncd*",
    "usr/lib/systemd/system/*/systemd-timesyncd*",
    "usr/lib/systemd/system/dbus-org.freedesktop.timesync1.service",
]
SD_CORE_REMOVE = SD_KEEP_LIBS + SD_KEEP_UDEV + SD_KEEP_NETWORKD + SD_KEEP_RESOLVED + SD_KEEP_TIMESYNCD
P(name="systemd-libs", version="261.2", description="libsystemd/libudev and nss_systemd shared runtime libraries",
  license="LGPL-2.1-or-later", url=SD_URL, deps=SD_DEPS, bdeps=SD_BDEPS,
  provides=["systemd-libs", "so:libsystemd.so.0", "so:libudev.so.1"],
  prepare=SD_PREP, build=SD_BUILD,
  install=sd_keep_install(SD_KEEP_LIBS))
P(name="systemd-udev", version="261.2", description="udev device manager, hwdb and udevadm (from systemd)",
  license="LGPL-2.1-or-later", url=SD_URL,
  deps=["systemd-libs >= 261.2", "kmod-libs >= 34.2", "util-linux-libs >= 2.42.2", "libcap-libs >= 2.78",
        "acl-libs >= 2.4.0"],
  bdeps=SD_BDEPS,
  provides=["systemd-udev", "virtual/udev"],
  prepare=SD_PREP, build=SD_BUILD,
  install=sd_keep_install(SD_KEEP_UDEV))
P(name="systemd", version="261.2", description="systemd core manager, control tools and default unit graph (PID 1, journald, logind)",
  license="LGPL-2.1-or-later", url=SD_URL,
  deps=["systemd-libs >= 261.2", "systemd-udev >= 261.2"] + SD_DEPS, bdeps=SD_BDEPS,
  provides=["systemd", "virtual/init"],
  prepare=SD_PREP, build=SD_BUILD,
  install=sd_core_install(SD_CORE_REMOVE))
P(name="systemd-networkd", version="261.2", description="systemd-networkd network configuration daemon",
  license="LGPL-2.1-or-later", url=SD_URL,
  deps=["systemd-libs >= 261.2", "systemd >= 261.2"], bdeps=SD_BDEPS,
  provides=["systemd-networkd"],
  prepare=SD_PREP, build=SD_BUILD,
  install=sd_keep_install(SD_KEEP_NETWORKD))
P(name="systemd-resolved", version="261.2", description="systemd-resolved DNS resolver and nss-resolve",
  license="LGPL-2.1-or-later", url=SD_URL,
  deps=["systemd-libs >= 261.2", "systemd >= 261.2", "openssl-libs >= 4.0.1"], bdeps=SD_BDEPS,
  provides=["systemd-resolved"],
  prepare=SD_PREP, build=SD_BUILD,
  install=sd_keep_install(SD_KEEP_RESOLVED))
P(name="systemd-timesyncd", version="261.2", description="systemd-timesyncd SNTP client",
  license="LGPL-2.1-or-later", url=SD_URL,
  deps=["systemd-libs >= 261.2", "systemd >= 261.2"], bdeps=SD_BDEPS,
  provides=["systemd-timesyncd"],
  prepare=SD_PREP, build=SD_BUILD,
  install=sd_keep_install(SD_KEEP_TIMESYNCD))
P(name="systemd-dev", version="261.2", description="systemd/udev headers, pkg-config and linker symlink",
  license="LGPL-2.1-or-later", url=SD_URL, deps=["systemd-libs >= 261.2"], bdeps=SD_BDEPS,
  provides=["systemd-dev"],
  prepare=SD_PREP, build=SD_BUILD,
  install=["cd build && DESTDIR=$DESTDIR ninja install",
           "rm -rf $DESTDIR/usr/lib/systemd $DESTDIR/usr/lib/udev $DESTDIR/usr/lib/security "
           "$DESTDIR/usr/lib/sysctl.d $DESTDIR/usr/lib/tmpfiles.d $DESTDIR/usr/lib/modules-load.d "
           "$DESTDIR/usr/lib/sysusers.d $DESTDIR/usr/lib/kernel $DESTDIR/usr/lib/environment.d "
           "$DESTDIR/usr/lib/binfmt.d $DESTDIR/usr/lib/credstore $DESTDIR/usr/lib/hwdb.d "
           "$DESTDIR/etc 2>/dev/null || true",
           "rm -f $DESTDIR/usr/lib/libnss_*.so*"] + STRIP_DEV)

DBUS_URL = "https://dbus.freedesktop.org/releases/dbus/dbus-1.16.2.tar.xz"
P(name="dbus-libs", version="1.16.2", description="libdbus-1 shared runtime library",
  license="GPL-2.0-or-later OR AFL-2.1", url=DBUS_URL,
  deps=["expat-libs >= 2.8.3", "systemd-libs >= 261.2"],
  bdeps=["meson >= 1.12.0", "ninja >= 1.13.2", "pkgconf >= 3.0.5", "expat-dev >= 2.8.3"],
  provides=["dbus-libs", "so:libdbus-1.so.3"],
  build=["mkdir build", "cd build && meson setup --prefix=/usr --buildtype=release --wrap-mode=nofallback ..",
         "cd build && ninja -j$(nproc)"],
  install=["cd build && DESTDIR=$DESTDIR ninja install"] + STRIP_LIBS)
P(name="dbus", version="1.16.2", description="D-Bus message bus system",
  license="GPL-2.0-or-later OR AFL-2.1", url=DBUS_URL,
  deps=["dbus-libs >= 1.16.2", "expat-libs >= 2.8.3", "systemd >= 261.2"],
  bdeps=["meson >= 1.12.0", "ninja >= 1.13.2", "pkgconf >= 3.0.5", "expat-dev >= 2.8.3"],
  provides=["dbus"],
  build=["mkdir build", "cd build && meson setup --prefix=/usr --buildtype=release --wrap-mode=nofallback ..",
         "cd build && ninja -j$(nproc)"],
  install=["cd build && DESTDIR=$DESTDIR ninja install",
           "ln -sfv /etc/machine-id $DESTDIR/var/lib/dbus"] + STRIP_CLI)
P(name="dbus-dev", version="1.16.2", description="libdbus headers, pkg-config and linker symlink",
  license="GPL-2.0-or-later OR AFL-2.1", url=DBUS_URL,
  deps=["dbus-libs >= 1.16.2"],
  bdeps=["meson >= 1.12.0", "ninja >= 1.13.2", "pkgconf >= 3.0.5", "expat-dev >= 2.8.3"],
  provides=["dbus-dev"],
  build=["mkdir build", "cd build && meson setup --prefix=/usr --buildtype=release --wrap-mode=nofallback ..",
         "cd build && ninja -j$(nproc)"],
  install=["cd build && DESTDIR=$DESTDIR ninja install"] + STRIP_DEV)

# ---------- new extra libraries: libinih, userspace-rcu ----------
INI_URL = "https://github.com/benhoyt/inih/archive/refs/tags/r61.tar.gz"
INI_SHA = "7caf26a2202a4ca689df3fe4175dfa74e0faa18fcca07331bba934fd0ecb8f12"
P(name="libinih", version="61", description="inih INI file parser runtime library",
  license="BSD-3-Clause", url=INI_URL, sha256=INI_SHA, deps=["glibc >= 2.44"],
  bdeps=["meson >= 1.12.0", "ninja >= 1.13.2"],
  provides=["libinih", "so:libinih.so.0"],
  build=["meson setup build --prefix=/usr --buildtype=release -Ddefault_library=shared -Ddistro_install=true",
         "ninja -C build -j$(nproc)"],
  install=["DESTDIR=$DESTDIR ninja -C build install"] + STRIP_LIBS)
P(name="libinih-dev", version="61", description="inih headers, pkg-config and linker symlink",
  license="BSD-3-Clause", url=INI_URL, sha256=INI_SHA, deps=["libinih >= 61"],
  bdeps=["meson >= 1.12.0", "ninja >= 1.13.2"],
  provides=["libinih-dev"],
  build=["meson setup build --prefix=/usr --buildtype=release -Ddefault_library=shared -Ddistro_install=true",
         "ninja -C build -j$(nproc)"],
  install=["DESTDIR=$DESTDIR ninja -C build install"] + STRIP_DEV)

URCU_URL = "https://lttng.org/files/urcu/userspace-rcu-0.15.6.tar.bz2"
URCU_SHA = "850b192096eb11ebf2c70e8f97bc7da7479ee41da1bebeb44e3986908bac414f"
P(name="userspace-rcu", version="0.15.6", description="Userspace RCU runtime libraries",
  license="LGPL-2.1-or-later", url=URCU_URL, sha256=URCU_SHA, deps=["glibc >= 2.44"],
  provides=["userspace-rcu", "so:liburcu.so.8"],
  build=AT_BUILD, install=AT_INSTALL + STRIP_LIBS)
P(name="userspace-rcu-dev", version="0.15.6", description="Userspace RCU headers, pkg-config and linker symlink",
  license="LGPL-2.1-or-later", url=URCU_URL, sha256=URCU_SHA, deps=["userspace-rcu >= 0.15.6"],
  provides=["userspace-rcu-dev"],
  build=AT_BUILD, install=AT_INSTALL + STRIP_DEV)

# ---------- filesystem tools / grub ----------
P(name="xfsprogs", version="7.1.1", description="XFS filesystem utilities",
  license="GPL-2.0-or-later",
  url="https://mirrors.edge.kernel.org/pub/linux/utils/fs/xfs/xfsprogs/xfsprogs-7.1.1.tar.xz",
  sha256="063edc31ba8e85c95c7faf9be465a04898bba7c6e622fdd9b146eed4ca5415e8",
  deps=["util-linux-libs >= 2.42.2", "libinih >= 61", "userspace-rcu >= 0.15.6", "lzo >= 2.10"],
  bdeps=["util-linux-dev >= 2.42.2", "libinih-dev >= 61", "userspace-rcu-dev >= 0.15.6", "lzo-dev >= 2.10",
         "gettext >= 1.0"],
  provides=["xfsprogs"],
  build=["./configure --prefix=/usr --sbindir=/usr/sbin --enable-gettext=no", "make -j$(nproc)"],
  install=["make install DESTDIR=$DESTDIR PKG_ROOT_SBIN_DIR=/usr/sbin PKG_ROOT_LIB_DIR=/usr/lib"])
P(name="dosfstools", version="4.2", description="DOS FAT filesystem utilities (mkfs.fat, fsck.fat)",
  license="GPL-3.0-or-later",
  url="https://github.com/dosfstools/dosfstools/releases/download/v4.2/dosfstools-4.2.tar.gz",
  sha256="64926eebf90092dca21b14259a5301b7b98e7b1943e8a201c7d726084809b527",
  deps=["glibc >= 2.44"], provides=["dosfstools"],
  build=["./configure --prefix=/usr --sbindir=/usr/sbin --enable-compat-symlinks", "make -j$(nproc)"],
  install=AT_INSTALL)
P(name="btrfs-progs", version="7.1", description="Btrfs filesystem utilities",
  license="GPL-2.0-or-later",
  url="https://cdn.kernel.org/pub/linux/kernel/people/kdave/btrfs-progs/btrfs-progs-v7.1.tar.xz",
  sha256="d1f55cc2971398c9142eaa79d203e63d586a3b4b867f956664a1d68322cd4e34",
  deps=["e2fsprogs-libs >= 1.47.4", "zstd-libs >= 1.5.7", "lzo >= 2.10", "util-linux-libs >= 2.42.2",
        "systemd-libs >= 261.2"],
  bdeps=["e2fsprogs-dev >= 1.47.4", "zstd-dev >= 1.5.7", "lzo-dev >= 2.10", "util-linux-dev >= 2.42.2",
         "pkgconf >= 3.0.5", "python >= 3.14.7"],
  provides=["btrfs-progs"],
  build=["CFLAGS=\"-I/usr/include/lzo\" CPPFLAGS=\"-I/usr/include/lzo\" LDFLAGS=\"-L/usr/lib\" ./configure --prefix=/usr --disable-documentation --disable-static", "make -j$(nproc)"],
  install=AT_INSTALL)
P(name="grub", version="2.14", description="GNU GRUB 2 bootloader",
  license="GPL-3.0-or-later", url="https://ftp.gnu.org/gnu/grub/grub-2.14.tar.xz",
  sha256="bc8d3c73535b8838d8c8e2654d73edc4e6ae8c8acdb45d5df5dc9a1547446d43",
  deps=["xz-libs >= 5.8.3"],
  bdeps=["bison >= 3.8.2", "flex >= 2.6.4", "python >= 3.14.7", "gettext >= 1.0", "xz-dev >= 5.8.3"],
  provides=["grub"],
  build=["./configure --prefix=/usr --sbindir=/usr/sbin --sysconfdir=/etc --disable-efiemu --disable-werror --with-platform=pc --target=i386",
         "make -j$(nproc)"],
  install=AT_INSTALL)

# ---------- meta / local / kernel ----------
P(name="os-release", version="1.0.0", description="Operating System identification and release information",
  license="MIT", deps=[], provides=["os-release", "system-info"],
  build=[],
  install=[
      "mkdir -p $DESTDIR/etc $DESTDIR/usr/lib $DESTDIR/etc/sage",
      "printf 'NAME=\"sclinux\"\\nPRETTY_NAME=\"sclinux 1.0 (Rolling)\"\\nID=sclinux\\nBUILD_ID=rolling\\nANSI_COLOR=\"32;1\"\\nHOME_URL=\"https://github.com/sclinuxdev/sclinux\"\\nDOCUMENTATION_URL=\"https://github.com/sclinuxdev/sclinux\"\\nSUPPORT_URL=\"https://github.com/sclinuxdev/sclinux/issues\"\\nBUG_REPORT_URL=\"https://github.com/sclinuxdev/sclinux/issues\"\\n' > $DESTDIR/usr/lib/os-release",
      "ln -sf ../usr/lib/os-release $DESTDIR/etc/os-release",
      "echo 'sclinux 1.0 (\\\\l)' > $DESTDIR/etc/issue",
      "printf 'schema_version = 1\\n\\n[system]\\nroot_dir = \"/\"\\ndb_path = \"/var/lib/sage/data.mdb\"\\ncache_dir = \"/var/cache/sage\"\\nconfig_dir = \"/etc/sage\"\\n\\n[providers]\\ninit = \"systemd\"\\nudev = \"systemd-udev\"\\nlibc = \"glibc\"\\n' > $DESTDIR/etc/sage/system.toml",
  ])
P(name="base-files", version="1.0.0", release="4",
  description="FHS standard filesystem hierarchy and essential system files",
  license="GPL-2.0-or-later", deps=["os-release >= 1.0.0"], provides=["base-files"],
  build=[],
  install=[
      "mkdir -p $DESTDIR/usr/bin $DESTDIR/usr/lib $DESTDIR/usr/include $DESTDIR/usr/share/man $DESTDIR/usr/share/doc",
      "mkdir -p $DESTDIR/etc/profile.d $DESTDIR/etc/sage/profiles/default/bin $DESTDIR/etc/sage/profiles/default/lib $DESTDIR/etc/sage/profiles/default/runtimes",
      "mkdir -p $DESTDIR/var/lib/sage $DESTDIR/var/cache/sage $DESTDIR/var/log $DESTDIR/var/tmp $DESTDIR/var/run",
      "mkdir -p $DESTDIR/opt/channels $DESTDIR/usr/lib/runtimes $DESTDIR/root $DESTDIR/home $DESTDIR/mnt $DESTDIR/proc $DESTDIR/sys $DESTDIR/dev $DESTDIR/run $DESTDIR/tmp",
      "chmod 0700 $DESTDIR/root",
      "chmod 1777 $DESTDIR/tmp $DESTDIR/var/tmp",
      "rm -rf $DESTDIR/bin $DESTDIR/sbin $DESTDIR/lib $DESTDIR/lib64 $DESTDIR/usr/sbin $DESTDIR/usr/lib64",
      "ln -sf usr/bin $DESTDIR/bin",
      "ln -sf usr/bin $DESTDIR/sbin",
      "ln -sf bin $DESTDIR/usr/sbin",
      "ln -sf usr/lib $DESTDIR/lib",
      "ln -sf usr/lib $DESTDIR/lib64",
      "ln -sf lib $DESTDIR/usr/lib64",
      "ln -sf ../run $DESTDIR/var/run",
      "printf 'root:x:0:0:root:/root:/bin/bash\\nbin:x:1:1:bin:/dev/null:/bin/false\\ndaemon:x:6:6:Daemon User:/dev/null:/bin/false\\nmessagebus:x:18:18:D-Bus Message Daemon User:/run/dbus:/bin/false\\nsystemd-journal-remote:x:996:996:systemd Journal Remote:/dev/null:/bin/false\\nsystemd-network:x:997:997:systemd Network Management:/dev/null:/bin/false\\nsystemd-resolve:x:998:998:systemd Resolver:/dev/null:/bin/false\\nsystemd-timesync:x:999:999:systemd Time Synchronization:/dev/null:/bin/false\\nnobody:x:65534:65534:Nobody:/:/bin/false\\n' > $DESTDIR/etc/passwd",
      "printf 'root:x:0:\\nbin:x:1:daemon\\nsys:x:2:\\nadm:x:4:\\ntty:x:5:\\ndaemon:x:6:\\ndisk:x:8:\\nwheel:x:10:root\\nmessagebus:x:18:\\nsystemd-journal:x:995:\\nsystemd-journal-remote:x:996:\\nsystemd-network:x:997:\\nsystemd-resolve:x:998:\\nsystemd-timesync:x:999:\\nusers:x:999:\\nnogroup:x:65534:\\n' > $DESTDIR/etc/group",
      "printf 'root::19700:0:99999:7:::\\nbin:*:19700:0:99999:7:::\\ndaemon:*:19700:0:99999:7:::\\nnobody:*:19700:0:99999:7:::\\n' > $DESTDIR/etc/shadow",
      "chmod 0600 $DESTDIR/etc/shadow",
      "printf '/bin/sh\\n/bin/bash\\n' > $DESTDIR/etc/shells",
      "printf '# /etc/fstab\\n# <file system> <mount point>   <type>  <options>       <dump>  <pass>\\n' > $DESTDIR/etc/fstab",
      "printf 'sage\\n' > $DESTDIR/etc/hostname",
      "printf '127.0.0.1   localhost sage\\n::1         localhost ip6-localhost ip6-loopback\\n' > $DESTDIR/etc/hosts",
      "printf 'passwd: files systemd\\ngroup: files [SUCCESS=merge] systemd\\nshadow: files systemd\\nhosts: mymachines resolve [!UNAVAIL=return] files myhostname dns\\nnetworks: files\\n' > $DESTDIR/etc/nsswitch.conf",
      "printf '#!/bin/sh\\nexport PATH=\"/etc/sage/profiles/default/bin:/usr/local/bin:/usr/bin:/bin:/usr/local/sbin:/usr/sbin:/sbin\"\\nexport LD_LIBRARY_PATH=\"/etc/sage/profiles/default/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}\"\\nexport PS1=\"[\\u@\\h \\W]\\$ \"\\nfor f in /etc/profile.d/*.sh; do [ -r \"$f\" ] && . \"$f\"; done\\n' > $DESTDIR/etc/profile",
      "printf 'include /etc/ld.so.conf.d/*.conf\\n' > $DESTDIR/etc/ld.so.conf",
  ])
P(name="sage", version="0.2.0", release="5", description="Universal Multi-Layer Linux Package Manager",
  license="MIT",
  deps=["lmdb >= 0.9.35", "zstd-libs >= 1.5.7", "tomlplusplus >= 3.4.0", "curl-libs >= 8.21.0",
        "gcc-libs >= 15.3.0", "openssl-libs >= 4.0.1"],
  bdeps=["xmake >= 3.1.0", "lmdb-dev >= 0.9.35", "zstd-dev >= 1.5.7", "tomlplusplus-dev >= 3.4.0",
         "curl-dev >= 8.21.0", "openssl-dev >= 4.0.1"],
  provides=["sage"],
  build=["SAGE_SOURCE_DIR=\"${SAGE_SOURCE_DIR:-/home/ir/distro/sage}\"; if [ ! -d \"$SAGE_SOURCE_DIR\" ] && [ -d /distro/sage ]; then SAGE_SOURCE_DIR=/distro/sage; fi; cd \"$SAGE_SOURCE_DIR\" && XMAKE_ROOT=y xmake f -c -m release -y && XMAKE_ROOT=y xmake -y"],
  install=["SAGE_SOURCE_DIR=\"${SAGE_SOURCE_DIR:-/home/ir/distro/sage}\"; if [ ! -d \"$SAGE_SOURCE_DIR\" ] && [ -d /distro/sage ]; then SAGE_SOURCE_DIR=/distro/sage; fi; install -Dm755 \"$SAGE_SOURCE_DIR/build/linux/x86_64/release/sage\" $DESTDIR/usr/bin/sage"])
P(name="mkinitcpio", version="41.1", description="Modular initramfs image creation utility",
  license="GPL-2.0-only", url="https://sources.archlinux.org/other/mkinitcpio/mkinitcpio-41.1.tar.gz",
  deps=["bash >= 5.3.15", "kmod >= 34.2", "coreutils >= 9.11", "util-linux >= 2.42.2", "findutils >= 4.11.0",
        "grep >= 3.12", "gawk >= 5.4.1", "sed >= 4.10", "zstd >= 1.5.7", "libarchive >= 3.8.9",
        "systemd >= 261.2", "systemd-libs >= 261.2", "systemd-udev >= 261.2"],
  bdeps=["meson >= 1.12.0", "ninja >= 1.13.2", "pkgconf >= 3.0.5"],
  provides=["mkinitcpio", "virtual/initramfs-generator"],
  build=["meson setup build --prefix=/usr --sysconfdir=/etc -Ddocs=false", "ninja -C build"],
  install=["DESTDIR=$DESTDIR ninja -C build install"])
P(name="linux-zen", version="7.1.9", description="Linux ZEN kernel with performance optimizations",
  license="GPL-2.0-only", url=LINUX_ZEN_URL,
  deps=["kmod >= 34.2", "linux-zen-headers = 7.1.9"],
  bdeps=["bc >= 7.0.3", "bison >= 3.8.2", "flex >= 2.6.4", "libelf-dev >= 0.196", "openssl-dev >= 4.0.1",
         "perl >= 5.44.0", "python >= 3.14.7", "tar >= 1.35", "xz >= 5.8.3", "zlib-dev >= 1.3.2",
         "zstd >= 1.5.7"],
  provides=["linux-zen", "linux", "virtual/linux", "virtual/kernel"],
  prepare=[
      "[ -f .prepared ] || zstd -dc ../distfiles/linux-v7.1.9-zen1.patch.zst | patch -p1 --forward --batch || true",
      "[ -f .prepared ] || cp -v ../config.x86_64 .config",
      "[ -f .prepared ] || echo '-1' > localversion.10-pkgrel",
      "[ -f .prepared ] || echo '-zen' > localversion.20-pkgname",
      "[ -f .prepared ] || ./scripts/config --disable DEBUG_INFO --enable DEBUG_INFO_NONE --disable DEBUG_INFO_DWARF5 --disable DEBUG_INFO_DWARF4 --disable DEBUG_INFO_BTF --disable DEBUG_INFO_BTF_MODULES --disable RUST",
      "[ -f .prepared ] || make olddefconfig",
      "[ -f .prepared ] || touch .prepared",
  ],
  build=['make -j$(nproc) -k KCFLAGS="-Wno-error" all || make -j$(nproc) KCFLAGS="-Wno-error" all'],
  install=[
      'set -e; KVER=$(make -s kernelrelease); echo "Installing kernel release: ${KVER}"; mkdir -p $DESTDIR/boot $DESTDIR/usr/lib/modules; make INSTALL_MOD_PATH=$DESTDIR modules_install; if [ -d $DESTDIR/lib/modules ] && [ ! -L $DESTDIR/lib ]; then cp -a $DESTDIR/lib/modules $DESTDIR/usr/lib/ && rm -rf $DESTDIR/lib; fi; rm -f $DESTDIR/usr/lib/modules/$KVER/build $DESTDIR/usr/lib/modules/$KVER/source; install -Dm644 arch/x86/boot/bzImage $DESTDIR/boot/vmlinuz-${KVER}; install -Dm644 System.map $DESTDIR/boot/System.map-${KVER}; install -Dm644 .config $DESTDIR/boot/config-${KVER}; install -Dm644 arch/x86/boot/bzImage $DESTDIR/usr/lib/modules/$KVER/vmlinuz',
  ])
P(name="fastfetch", version="2.67.1", description="Fast, highly customizable system information tool",
  license="MIT", url="https://github.com/fastfetch-cli/fastfetch/archive/refs/tags/2.67.1.tar.gz",
  deps=["glibc >= 2.44", "zlib >= 1.3.2", "sqlite-libs >= 3.53.4"],
  bdeps=["cmake >= 4.4.2", "pkgconf >= 3.0.5", "zlib-dev >= 1.3.2", "sqlite-dev >= 3.53.4"],
  provides=["fastfetch"],
  build=["cmake -B build -S . -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/usr -DBUILD_TESTS=OFF",
         "cmake --build build -j$(nproc)"],
  install=["DESTDIR=$DESTDIR cmake --install build"])

BASE_DEPS = [
    "os-release", "base-files", "linux-headers >= 7.1.9", "glibc", "glibc-dev",
    "zlib", "zlib-dev", "bzip2", "bzip2-libs", "bzip2-dev",
    "xz", "xz-libs", "xz-dev", "zstd", "zstd-libs", "zstd-dev",
    "lz4", "lz4-libs", "lz4-dev", "lzo", "lzo-dev",
    "file", "file-libs", "file-dev", "m4",
    "ncurses", "ncurses-libs", "ncurses-dev", "ncurses-terminfo",
    "readline", "readline-dev", "sed", "pcre2", "pcre2-libs", "pcre2-dev",
    "grep", "gawk", "diffutils", "findutils", "tar", "gzip", "patch", "make",
    "bash", "attr", "attr-libs", "attr-dev", "acl", "acl-libs", "acl-dev",
    "libcap", "libcap-libs", "libcap-dev", "libseccomp", "libseccomp-dev",
    "coreutils", "gmp", "gmp-dev", "mpfr", "mpfr-dev", "mpc", "mpc-dev", "isl", "isl-dev",
    "binutils", "gcc", "gcc-libs", "xmake",
    "lmdb", "lmdb-dev",
    "openssl", "openssl-libs", "openssl-dev",
    "curl", "curl-libs", "curl-dev",
    "tomlplusplus", "tomlplusplus-dev", "sage",
    "util-linux", "util-linux-libs", "util-linux-dev",
    "e2fsprogs", "e2fsprogs-libs", "e2fsprogs-dev",
    "xfsprogs", "dosfstools", "btrfs-progs",
    "man-pages", "iana-etc", "pkgconf", "bc", "flex", "bison", "texinfo",
    "libtool",
    "autoconf", "automake", "gdbm", "gdbm-libs", "gdbm-dev", "gperf",
    "gettext", "gettext-libs", "gettext-dev",
    "tcl", "tcl-libs", "tcl-dev", "expect", "dejagnu",
    "perl", "perl-libs",
    "libffi", "libffi-dev",
    "sqlite", "sqlite-libs", "sqlite-dev",
    "mpdecimal", "mpdecimal-dev",
    "python", "python-libs", "python-dev",
    "flit-core", "packaging", "wheel", "setuptools", "meson", "ninja",
    "expat", "expat-libs", "expat-dev",
    "libpipeline", "libpipeline-dev", "libelf", "libelf-dev",
    "libxcrypt", "libxcrypt-dev",
    "libarchive", "libarchive-libs", "libarchive-dev",
    "libinih", "libinih-dev", "userspace-rcu", "userspace-rcu-dev",
    "groff", "markupsafe", "jinja2", "psmisc", "inetutils", "less",
    "iproute2", "kbd", "procps-ng", "procps-ng-libs", "procps-ng-dev", "shadow",
    "kmod", "kmod-libs", "kmod-dev",
    "systemd-libs", "systemd-udev", "systemd", "systemd-networkd", "systemd-resolved",
    "systemd-timesyncd", "systemd-dev",
    "dbus", "dbus-libs", "dbus-dev",
    "man-db", "which", "grub",
]
P(name="base", version="2.0.0", description="sclinux Base System Meta-Package (Stage 2)",
  license="GPL-3.0-or-later", deps=BASE_DEPS, provides=["base"], build=[], install=[])

# ===========================================================================
# Families whose distfiles should be shared across split packages
# ===========================================================================
DIST_FAMILIES = {
    "zlib": ["zlib", "zlib-dev"],
    "bzip2": ["bzip2", "bzip2-libs", "bzip2-dev"],
    "xz": ["xz", "xz-libs", "xz-dev"],
    "zstd": ["zstd", "zstd-libs", "zstd-dev"],
    "lz4": ["lz4", "lz4-libs", "lz4-dev"],
    "lzo": ["lzo", "lzo-dev"],
    "file": ["file", "file-libs", "file-dev"],
    "gmp": ["gmp", "gmp-dev"],
    "mpfr": ["mpfr", "mpfr-dev"],
    "mpc": ["mpc", "mpc-dev"],
    "isl": ["isl", "isl-dev"],
    "ncurses": ["ncurses", "ncurses-libs", "ncurses-dev", "ncurses-terminfo"],
    "readline": ["readline", "readline-dev"],
    "attr": ["attr", "attr-libs", "attr-dev"],
    "acl": ["acl", "acl-libs", "acl-dev"],
    "libcap": ["libcap", "libcap-libs", "libcap-dev"],
    "libxcrypt": ["libxcrypt", "libxcrypt-dev"],
    "openssl": ["openssl", "openssl-libs", "openssl-dev"],
    "pcre2": ["pcre2", "pcre2-libs", "pcre2-dev"],
    "curl": ["curl", "curl-libs", "curl-dev"],
    "libelf": ["libelf", "libelf-dev"],
    "kmod": ["kmod", "kmod-libs", "kmod-dev"],
    "lmdb": ["lmdb", "lmdb-dev"],
    "tomlplusplus": ["tomlplusplus", "tomlplusplus-dev"],
    "libarchive": ["libarchive", "libarchive-libs", "libarchive-dev"],
    "libseccomp": ["libseccomp", "libseccomp-dev"],
    "libpipeline": ["libpipeline", "libpipeline-dev"],
    "libtool": ["libtool"],
    "util-linux": ["util-linux", "util-linux-libs", "util-linux-dev"],
    "e2fsprogs": ["e2fsprogs", "e2fsprogs-libs", "e2fsprogs-dev"],
    "gdbm": ["gdbm", "gdbm-libs", "gdbm-dev"],
    "gettext": ["gettext", "gettext-libs", "gettext-dev"],
    "expat": ["expat", "expat-libs", "expat-dev"],
    "libffi": ["libffi", "libffi-dev"],
    "sqlite": ["sqlite", "sqlite-libs", "sqlite-dev"],
    "mpdecimal": ["mpdecimal", "mpdecimal-dev"],
    "tcl": ["tcl", "tcl-libs", "tcl-dev"],
    "python": ["python", "python-libs", "python-dev"],
    "perl": ["perl", "perl-libs"],
    "glibc": ["glibc", "glibc-dev"],
    "libinih": ["libinih", "libinih-dev"],
    "userspace-rcu": ["userspace-rcu", "userspace-rcu-dev"],
    "procps-ng": ["procps-ng", "procps-ng-libs", "procps-ng-dev"],
    "systemd": ["systemd", "systemd-libs", "systemd-udev", "systemd-networkd",
                "systemd-resolved", "systemd-timesyncd", "systemd-dev"],
    "dbus": ["dbus", "dbus-libs", "dbus-dev"],
}

STALE = ["libffi-libs", "python-toolchain", "perl-toolchain", "lmdb-libs", "libtool-libs", "libtool-dev", "linux-headers"]

BUILD_ORDER = [
    "linux-zen-headers",
    "glibc", "glibc-dev",
    "zlib", "zlib-dev",
    "bzip2-libs", "bzip2", "bzip2-dev",
    "xz-libs", "xz", "xz-dev",
    "zstd-libs", "zstd", "zstd-dev",
    "lz4-libs", "lz4", "lz4-dev",
    "lzo", "lzo-dev",
    "m4",
    "file-libs", "file", "file-dev",
    "gmp", "gmp-dev",
    "mpfr", "mpfr-dev",
    "mpc", "mpc-dev",
    "isl", "isl-dev",
    "binutils",
    "gcc",
    "gcc-libs",
    "ncurses-libs", "ncurses-dev", "ncurses-terminfo", "ncurses",
    "readline", "readline-dev",
    "bash",
    "attr-libs", "attr", "attr-dev",
    "acl-libs", "acl", "acl-dev",
    "libcap-libs", "libcap", "libcap-dev",
    "libxcrypt", "libxcrypt-dev",
    "pkgconf",
    "gperf",
    "libseccomp", "libseccomp-dev",
    "pcre2-libs", "pcre2", "pcre2-dev",
    "openssl-libs", "openssl", "openssl-dev",
    "diffutils", "gawk", "grep", "sed", "patch", "make", "tar", "gzip", "findutils", "coreutils",
    "curl-libs", "curl", "curl-dev",
    "expat-libs", "expat", "expat-dev",
    "libffi", "libffi-dev",
    "libelf", "libelf-dev",
    "util-linux-libs", "util-linux", "util-linux-dev",
    "e2fsprogs-libs", "e2fsprogs", "e2fsprogs-dev",
    "lmdb", "lmdb-dev",
    "tomlplusplus", "tomlplusplus-dev",
    "libarchive-libs", "libarchive", "libarchive-dev",
    "libpipeline", "libpipeline-dev",
    "libtool",
    "gdbm-libs", "gdbm", "gdbm-dev",
    "sqlite-libs", "sqlite", "sqlite-dev",
    "mpdecimal", "mpdecimal-dev",
    "tcl-libs", "tcl", "tcl-dev",
    "expect",
    "less", "nano", "bc",
    "iana-etc", "man-pages",
    "perl-libs", "perl",
    "gettext-libs", "gettext", "gettext-dev",
    "bison", "flex", "texinfo", "autoconf", "automake", "which",
    "python-libs", "python", "python-dev",
    "flit-core", "packaging", "wheel", "setuptools", "markupsafe", "jinja2",
    "ninja", "meson", "cmake", "xmake",
    "kmod-libs", "kmod", "kmod-dev",
    "groff", "man-db",
    "psmisc", "inetutils", "iproute2", "kbd",
    "shadow",
    "systemd-libs", "systemd-udev", "systemd", "systemd-networkd", "systemd-resolved",
    "systemd-timesyncd", "systemd-dev",
    "procps-ng-libs", "procps-ng", "procps-ng-dev",
    "dbus-libs", "dbus", "dbus-dev",
    "libinih", "libinih-dev",
    "userspace-rcu", "userspace-rcu-dev",
    "xfsprogs", "dosfstools", "btrfs-progs",
    "dejagnu",
    "os-release", "base-files",
    "sage",
    "mkinitcpio",
    "grub",
    "linux-zen",
    "fastfetch",
    "base",
]


def main() -> None:
    names = [p["name"] for p in PKGS]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        raise SystemExit(f"duplicate package specs: {dupes}")

    for pkg in PKGS:
        write_pkg(pkg)

    for src, members in DIST_FAMILIES.items():
        others = [m for m in members if m != src]
        copy_dist(src, *others)
        # also copy from any member that already has distfiles
        for m in members:
            rest = [x for x in members if x != m]
            copy_dist(m, *rest)

    # linux-zen-headers shares the kernel tarball only (not the zen patch).
    hdr_df = os.path.join(ROOT, "linux-zen-headers", "distfiles")
    zen_tarball = os.path.join(ROOT, "linux-zen", "distfiles", "linux-7.1.9.tar.xz")
    os.makedirs(hdr_df, exist_ok=True)
    dst_tb = os.path.join(hdr_df, "linux-7.1.9.tar.xz")
    if os.path.isfile(zen_tarball) and not os.path.isfile(dst_tb):
        shutil.copy2(zen_tarball, dst_tb)
    patch = os.path.join(hdr_df, "linux-v7.1.9-zen1.patch.zst")
    if os.path.isfile(patch):
        os.remove(patch)

    for stale in STALE:
        d = os.path.join(ROOT, stale)
        if os.path.isdir(d):
            shutil.rmtree(d)
            print("removed stale", d)

    missing = [n for n in BUILD_ORDER if n not in set(names)]
    extra = sorted(set(names) - set(BUILD_ORDER))
    if missing or extra:
        print("BUILD_ORDER missing:", missing)
        print("specs not in BUILD_ORDER:", extra)
        raise SystemExit(1)

    sync_build_sh()
    print(f"generated {len(PKGS)} recipes")
    print("BUILD_ORDER count:", len(BUILD_ORDER))


def sync_build_sh() -> None:
    path = "/mnt/stage2/build-stage2.sh"
    with open(path) as f:
        text = f.read()
    start = text.find("BUILD_ORDER=(\n")
    end = text.find("\n)\n", start) if start >= 0 else -1
    if start < 0 or end < 0:
        raise SystemExit("failed to locate BUILD_ORDER in build-stage2.sh")
    new = "BUILD_ORDER=(\n" + "".join(f'    "{n}"\n' for n in BUILD_ORDER) + ")"
    text = text[:start] + new + text[end + 2:]
    marker = "# 2. Stage 2 Topological Build Queue ("
    i = text.find(marker)
    if i >= 0:
        j = text.find(")", i)
        text = (
            text[:i]
            + f"# 2. Stage 2 Topological Build Queue ({len(BUILD_ORDER)} Fine-Grained, Split & Toolchain Packages"
            + text[j:]
        )
    with open(path, "w") as f:
        f.write(text)
    print("synced", path)


if __name__ == "__main__":
    main()

