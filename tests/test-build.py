#!/usr/bin/env python3
"""Tests for the architecture-aware build entry point."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock


REPO = Path(__file__).resolve().parent.parent
BUILD = REPO / "tools" / "build.py"
spec = importlib.util.spec_from_file_location("shenchen_build", BUILD)
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


# Every check that runs is counted here. The summary used to print a
# hand-maintained constant, which drifted to 90 against 147 call sites -- so the
# "N passed" line was decoration and only "0 failed" carried information. Checks
# inside loops each count once, which is what "ran" means.
checks_run = 0


def check(description: str, actual: object, expected: object) -> bool:
    global checks_run
    checks_run += 1
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
    image_assembler = (REPO / "tools" / "assemble-image.sh").read_text()
    failed += not check(
        "image assembler resolves outputs below missing parent directories",
        'output=$(readlink -m "$2")' in image_assembler,
        True,
    )
    failed += not check(
        "image assembler keeps its VG aligned with the packaged kernel command line",
        "vg_name=vg0" in image_assembler and "SCLINUX_VG_NAME" not in image_assembler,
        True,
    )
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
        "architectures select distinct systemd-boot EFI binaries",
        {values["systemd_boot_efi"] for values in architectures.values()},
        {"systemd-bootx64.efi", "systemd-bootaa64.efi"},
    )
    failed += not check(
        "architectures select their QEMU serial consoles",
        {values["kernel_console"] for values in architectures.values()},
        {"ttyS0,115200", "ttyAMA0,115200"},
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
        "kernel_arch, kernel_console, kernel_image, oci_platform, qemu_machine, "
        "qemu_system, systemd_boot_efi",
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
    copied = [line for line in containerfile.splitlines() if line.startswith(("COPY ", "ADD "))]
    failed += not check(
        "Stage0 does not copy local Sage source patches",
        copied,
        [],
    )
    failed += not check(
        "Stage0 allows xmake inside the root-owned build container",
        "XMAKE_ROOT=y" in containerfile,
        True,
    )
    failed += not check(
        "Stage0 tag binds the bootstrap Sage source",
        build.stage0_tag("aarch64", seed).endswith(
            build.source_for_package("sage")["sha256"][:12]
        ),
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
    failed += not check(
        "Stage0 builds the pinned Sage source without local patches",
        "patch -p1 < /tmp/sage-bootstrap-install-integrity.patch" in containerfile,
        False,
    )
    sage_recipe = build.tomllib.loads(
        (REPO / "Stage1" / "recipes" / "sage" / "recipe.toml").read_text()
    )
    failed += not check(
        "Stage0 and Stage1 pin the tested Sage bootstrap release",
        (sage_recipe["source"]["url"], sage_recipe["source"]["sha256"]),
        (
            "https://codeload.github.com/sclinuxdev/sage/tar.gz/"
            "77b0e29a22cd6e3883aaf7e1823980e140432f0d",
            "7b2bfca4d74401c11216d6c743756f0306ef33b8d07e997adc7dfc0fc6458e5b",
        ),
    )
    sage_helper = (REPO / "Stage1" / "recipes" / "sage" / "shc").read_text()
    failed += not check(
        "Sage package installs the tested SCLinux shorthand wrapper",
        sage_recipe["package"]["release"] == "14"
        and 'install -Dm755 ../shc "$DESTDIR/usr/bin/shc"'
        in sage_recipe["package"]["install"]
        and sage_helper == (REPO / "scripts" / "shc").read_text(),
        True,
    )
    xmake_recipe = build.tomllib.loads(
        (REPO / "Stage1" / "recipes" / "xmake" / "recipe.toml").read_text()
    )
    xmake_wrapper = (
        REPO / "Stage1" / "recipes" / "xmake" / "xmake-channel-wrapper.sh"
    ).read_text()
    failed += not check(
        "xmake remains runnable through the Sage toolchain profile",
        xmake_recipe["package"]["release"] == "5"
        and "XMAKE_PROGRAM_FILE" in xmake_wrapper
        and "/opt/channels/xmake/3/bin/xmake.real" in xmake_wrapper,
        True,
    )
    proot_chroot = (REPO / "tools" / "proot-bin" / "chroot").read_text()
    failed += not check(
        "restricted builders can run target-root triggers through PRoot",
        'target_root=$(readlink -f "$1")' in proot_chroot
        and "PRoot 5.4.0 or newer" in proot_chroot
        and 'exec proot -R "$target_root" "$@"' in proot_chroot,
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
    stage1_sysroot = Path("/fixture-stage1-sysroot")
    stage1_environment = build.stage1_build_environment(seed, stage1_sysroot)
    failed += not check(
        "Stage1 exports the locked source epoch",
        stage1_environment["SOURCE_DATE_EPOCH"],
        str(seed["source_date_epoch"]),
    )
    failed += not check(
        "Stage1 fixes the build timezone",
        stage1_environment["TZ"],
        "UTC",
    )
    failed += not check(
        "Stage1 searches its isolated tools before Stage0",
        stage1_environment["PATH"].split(":")[0],
        "/fixture-stage1-sysroot/.stage1-tool-wrappers/usr/bin",
    )
    failed += not check(
        "Stage1 keeps the Stage0 compiler until target libc is complete",
        "/fixture-stage1-sysroot/opt/channels/gcc/15/bin"
        not in stage1_environment["PATH"].split(":"),
        True,
    )
    failed += not check(
        "Stage1 links against its isolated package libraries",
        stage1_environment["LDFLAGS"].split()[0],
        "-L/fixture-stage1-sysroot/usr/lib",
    )
    failed += not check(
        "Stage1 exposes its isolated sysroot to dependency-aware recipes",
        stage1_environment["SC_BUILD_SYSROOT"],
        "/fixture-stage1-sysroot",
    )
    failed += not check(
        "Stage1 loads character converters from its isolated glibc",
        stage1_environment["GCONV_PATH"],
        "/fixture-stage1-sysroot/usr/lib/gconv",
    )
    failed += not check(
        "Stage1 rewrites pkg-config prefixes into its isolated sysroot",
        stage1_environment["PKG_CONFIG_SYSROOT_DIR"],
        "/fixture-stage1-sysroot",
    )
    with tempfile.TemporaryDirectory() as directory:
        fixture = Path(directory)
        executable = fixture / "python3"
        stale = fixture / "xmake"
        executable.touch()
        stale.touch()
        proc_exe = fixture / "proc-self-exe"
        proc_exe.symlink_to(stale)
        try:
            build.validate_stage1_procfs(proc_exe, executable)
        except build.ConfigError as exc:
            procfs_error = str(exc)
        else:
            procfs_error = "no error"
    failed += not check(
        "Stage1 rejects a stale procfs executable snapshot",
        "mount procfs or empty /proc" in procfs_error,
        True,
    )
    failed += not check(
        "Stage1 resolves a relative workspace before invoking Sage",
        "workspace = workspace.resolve()" in BUILD.read_text(),
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
    failed += not check(
        "Stage0 image labels the bootstrap Sage source identity",
        f"org.shenchen.stage0.sage-sha256={sage_source['sha256']}" in command,
        True,
    )

    stage1_packages = build.load_stage1_manifest()
    failed += not check(
        "Stage1 manifest covers every canonical recipe",
        len(stage1_packages),
        120,
    )
    failed += not check(
        "Stage1 builds libunistring before the gettext tools that load it",
        stage1_packages.index("libunistring") < stage1_packages.index("gettext"),
        True,
    )
    failed += not check(
        "Stage1 builds libxcrypt before util-linux supplies sulogin",
        stage1_packages.index("libxcrypt") < stage1_packages.index("util-linux"),
        True,
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
        102,
    )
    failed += not check(
        "Stage1 source lock retains every package reference",
        sum(len(source["packages"]) for source in stage1_sources),
        115,
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
            "Stage1 Sage no longer applies the upstreamed integrity patch",
            rendered["package"]["prepare"],
            [],
        )
        failed += not check(
            "Stage1 Sage passes the isolated sysroot to xmake links",
            '--ldflags="$LDFLAGS"' in rendered["package"]["build"][0],
            True,
        )
        failed += not check(
            "canonical Stage1 recipes remain architecture-neutral",
            "arch" in canonical["package"],
            False,
        )
        flit_core = build.tomllib.loads(
            (REPO / "Stage1" / "recipes" / "flit-core" / "recipe.toml").read_text()
        )
        failed += not check(
            "Stage1 flit-core installs directly below the package root",
            "$DESTDIR/usr/lib/python3.14/site-packages"
            in " ".join(flit_core["source"]["install"])
            and "bootstrap_install.py"
            not in " ".join(flit_core["source"]["install"]),
            True,
        )
        libelf = build.tomllib.loads(
            (REPO / "Stage1" / "recipes" / "libelf" / "recipe.toml").read_text()
        )
        failed += not check(
            "Stage1 libelf keeps format diagnostics non-fatal with new glibc headers",
            "-Wno-error=format-nonliteral" in libelf["source"]["build"][0],
            True,
        )
        groff = build.tomllib.loads(
            (REPO / "Stage1" / "recipes" / "groff" / "recipe.toml").read_text()
        )
        failed += not check(
            "Stage1 groff normalizes generated Perl shebangs",
            "#!/usr/bin/perl" in groff["source"]["install"][1]
            and "mmroff" in groff["source"]["install"][1],
            True,
        )
        setuptools_recipe = build.tomllib.loads(
            (REPO / "Stage1" / "recipes" / "setuptools" / "recipe.toml").read_text()
        )
        failed += not check(
            "Stage1 setuptools retains its generated pyproject validators",
            "fastjsonschema_*.py" in setuptools_recipe["source"]["install"][2],
            True,
        )
        inetutils = build.tomllib.loads(
            (REPO / "Stage1" / "recipes" / "inetutils" / "recipe.toml").read_text()
        )
        failed += not check(
            "Stage1 inetutils does not probe the build host procfs path",
            "inetutils_cv_path_procnet_dev=/proc/net/dev"
            in inetutils["source"]["build"][0],
            True,
        )
        iproute2 = build.tomllib.loads(
            (REPO / "Stage1" / "recipes" / "iproute2" / "recipe.toml").read_text()
        )
        failed += not check(
            "Stage1 iproute2 probes and builds against its isolated sysroot",
            'CC="gcc $LDFLAGS" ./configure' in iproute2["source"]["build"]
            and 'HOSTCC="gcc $LDFLAGS"' in iproute2["source"]["build"][1],
            True,
        )
        failed += not check(
            "Stage1 usr-merge recipes install administrative tools canonically",
            "for directory in $DESTDIR/sbin $DESTDIR/usr/sbin"
            in build.tomllib.loads(
                (REPO / "Stage1" / "recipes" / "glibc" / "recipe.toml").read_text()
            )["source"]["install"][1]
            and "s@/usr/lib/@@g"
            in build.tomllib.loads(
                (REPO / "Stage1" / "recipes" / "glibc" / "recipe.toml").read_text()
            )["source"]["install"][2]
            and "SBINDIR=/usr/bin" in iproute2["source"]["install"][0]
            and any(
                "rm -rf $DESTDIR/bin $DESTDIR/sbin" in command
                for command in build.tomllib.loads(
                    (
                        REPO
                        / "Stage1"
                        / "recipes"
                        / "util-linux-libs"
                        / "recipe.toml"
                    ).read_text()
                )["source"]["install"]
            ),
            True,
        )
        util_linux_recipe = build.tomllib.loads(
            (REPO / "Stage1" / "recipes" / "util-linux" / "recipe.toml").read_text()
        )
        failed += not check(
            "Stage1 util-linux closes the sulogin crypt dependency",
            "libxcrypt >= 4.5.2" in util_linux_recipe["source"]["dependencies"],
            True,
        )
        runtime_generated_recipes = (
            "autoconf",
            "automake",
            "bash",
            "bison",
            "coreutils",
            "gettext",
            "glibc",
            "groff",
            "inetutils",
            "libffi",
            "libunistring",
            "texinfo",
        )
        failed += not check(
            "Stage1 recipes exclude generated info indexes from package payloads",
            all(
                "usr/share/info/dir"
                in " ".join(
                    build.tomllib.loads(
                        (
                            REPO / "Stage1" / "recipes" / package / "recipe.toml"
                        ).read_text()
                    )["source"]["install"]
                )
                for package in runtime_generated_recipes
            ),
            True,
        )
        base_files_recipe = build.tomllib.loads(
            (REPO / "Stage1" / "recipes" / "base-files" / "recipe.toml").read_text()
        )
        failed += not check(
            "Stage1 base files support enabled systemd services and image assembly",
            base_files_recipe["package"]["release"] == "6"
            and "systemd-oom:x:994:994" in " ".join(base_files_recipe["package"]["install"])
            and "systemd-oom:x:994:" in " ".join(base_files_recipe["package"]["install"])
            and "etc/fstab" not in " ".join(base_files_recipe["package"]["install"]),
            True,
        )
        gettext_recipe = build.tomllib.loads(
            (REPO / "Stage1" / "recipes" / "gettext" / "recipe.toml").read_text()
        )
        failed += not check(
            "Stage1 gettext forces libasprintf through its working libtool fallback",
            any(
                "libasprintf.la CXXLINK=false" in command
                for command in gettext_recipe["source"]["build"]
            ),
            True,
        )
        libffi_recipe = build.tomllib.loads(
            (REPO / "Stage1" / "recipes" / "libffi" / "recipe.toml").read_text()
        )
        failed += not check(
            "Stage1 libffi installs shared libraries in the canonical usr libdir",
            "--libdir=/usr/lib" in libffi_recipe["source"]["build"][0]
            and "--disable-multi-os-directory"
            in libffi_recipe["source"]["build"][0],
            True,
        )
        binutils_recipe = build.tomllib.loads(
            (REPO / "Stage1" / "recipes" / "binutils" / "recipe.toml").read_text()
        )
        binutils_install = " ".join(binutils_recipe["source"]["install"])
        failed += not check(
            "Stage1 binutils installs the normalized multiarch linker alias",
            "@SC_GNU_TRIPLET@" in binutils_install
            and "s/-pc-/-/" in binutils_install
            and "s/-unknown-/-/" in binutils_install
            and "ln -sf ld" in binutils_install
            and "opt/channels/gcc/15/share/info/dir" in binutils_install,
            True,
        )
        gcc_recipe = build.tomllib.loads(
            (REPO / "Stage1" / "recipes" / "gcc" / "recipe.toml").read_text()
        )
        failed += not check(
            "Stage1 GCC disables the unused libcc1 interface",
            "--disable-libcc1" in gcc_recipe["source"]["build"][0]
            and "opt/channels/gcc/15/share/info/dir"
            in " ".join(gcc_recipe["source"]["install"]),
            True,
        )
        bash_recipe = build.tomllib.loads(
            (REPO / "Stage1" / "recipes" / "bash" / "recipe.toml").read_text()
        )
        failed += not check(
            "Stage1 Bash uses procfs-backed descriptors for process substitution",
            "bash_cv_dev_fd=standard" in bash_recipe["source"]["build"][0],
            True,
        )
        glibc_recipe = build.tomllib.loads(
            (REPO / "Stage1" / "recipes" / "glibc" / "recipe.toml").read_text()
        )
        failed += not check(
            "Stage1 glibc excludes the runtime linker cache from its payload",
            "etc/ld.so.cache" in " ".join(glibc_recipe["source"]["install"]),
            True,
        )
        mkinitcpio_recipe = build.tomllib.loads(
            (
                REPO / "Stage1" / "recipes" / "mkinitcpio" / "recipe.toml"
            ).read_text()
        )
        linux_recipe = build.tomllib.loads(
            (REPO / "Stage1" / "recipes" / "linux-zen" / "recipe.toml").read_text()
        )
        boot_recipe = build.tomllib.loads(
            (
                REPO / "Stage1" / "recipes" / "sclinux-boot" / "recipe.toml"
            ).read_text()
        )
        failed += not check(
            "Stage1 boot recipes declare executable capability hooks",
            mkinitcpio_recipe["capability_hooks"]
            == [
                {
                    "capability": "virtual/initramfs-generator",
                    "exec": "/usr/bin/mkinitcpio",
                    "args": ["-P"],
                }
            ]
            and boot_recipe["capability_hooks"]
            == [
                {
                    "capability": "virtual/bootloader",
                    "exec": "/usr/bin/sclinux-update-boot",
                    "args": [],
                }
            ]
            and "virtual/initramfs-generator"
            in linux_recipe["source"]["dependencies"]
            and "mkinitcpio.d/linux-zen.preset"
            in " ".join(linux_recipe["source"]["install"]),
            True,
        )
        failed += not check(
            "Stage1 initramfs configuration exists before the kernel trigger fires",
            "etc/mkinitcpio.conf"
            in " ".join(mkinitcpio_recipe["source"]["install"])
            and "etc/mkinitcpio.conf"
            not in " ".join(boot_recipe["package"]["install"]),
            True,
        )
        failed += not check(
            "Stage1 kernel objtool tolerates build containers without procfs",
            "[ -f .prepared ] || patch -p1"
            in " ".join(linux_recipe["source"]["prepare"])
            and "objtool-no-procfs.patch"
            in " ".join(linux_recipe["source"]["prepare"])
            and "return stack_limit &&"
            in (
                REPO
                / "Stage1"
                / "recipes"
                / "linux-zen"
                / "objtool-no-procfs.patch"
            ).read_text(),
            True,
        )
        kbd = build.tomllib.loads(
            (REPO / "Stage1" / "recipes" / "kbd" / "recipe.toml").read_text()
        )
        failed += not check(
            "Stage1 kbd excludes unshipped Autotest build artifacts",
            "--disable-tests" in kbd["source"]["build"][0],
            True,
        )
        shadow = build.tomllib.loads(
            (REPO / "Stage1" / "recipes" / "shadow" / "recipe.toml").read_text()
        )
        failed += not check(
            "Stage1 shadow uses POSIX-shell-compatible crypt options",
            "--with-bcrypt --with-yescrypt" in shadow["source"]["build"][1]
            and "--with-{b,yes}crypt" not in shadow["source"]["build"][1],
            True,
        )
        split_cleanup_expectations = {
            "xz-libs": "usr/share/locale",
            "ncurses-libs": "usr/share/terminfo",
            "pcre2-libs": "rm -rf $DESTDIR/usr/bin $DESTDIR/usr/share",
            "util-linux-libs": "libsmartcols.la",
            "e2fsprogs-libs": "rm -rf $DESTDIR/usr/bin $DESTDIR/usr/share",
            "e2fsprogs": "libe2p.*",
            "file-libs": "rm -rf $DESTDIR/usr/share",
            "openssl-libs": "usr/lib/cmake",
            "openssl": "usr/lib/ossl-modules",
            "shadow": "usr/bin/nologin",
            "man-pages": "crypt_r.3",
            "mkinitcpio": "HOOKS=(systemd modconf block lvm2 filesystems fsck)",
            "inetutils": "usr/bin/hostname",
            "tcl": "Tcl_Thread.3",
        }
        failed += not check(
            "Stage1 split-package recipes remove every audited ownership overlap",
            all(
                expected
                in " ".join(
                    build.tomllib.loads(
                        (REPO / "Stage1" / "recipes" / name / "recipe.toml").read_text()
                    )["source"]["install"]
                )
                for name, expected in split_cleanup_expectations.items()
            ),
            True,
        )
        ncurses_libs_install = build.tomllib.loads(
            (REPO / "Stage1" / "recipes" / "ncurses-libs" / "recipe.toml").read_text()
        )["source"]["install"]
        failed += not check(
            "Stage1 ncurses libraries skip the discarded terminfo install",
            ncurses_libs_install[0],
            "make install.libs install.includes DESTDIR=$DESTDIR",
        )
        kmod = build.tomllib.loads(
            (REPO / "Stage1" / "recipes" / "kmod" / "recipe.toml").read_text()
        )
        failed += not check(
            "Stage1 kmod installs libraries in the shared package search root",
            "--libdir=lib" in kmod["source"]["build"][1],
            True,
        )
        systemd = build.tomllib.loads(
            (REPO / "Stage1" / "recipes" / "systemd" / "recipe.toml").read_text()
        )
        failed += not check(
            "Stage1 systemd preserves its compatibility headers and shared libdir",
            "--libdir=lib" in systemd["source"]["build"][1]
            and all(
                "env -u CPATH CPPFLAGS=" in command
                and 'C_INCLUDE_PATH="$SC_BUILD_SYSROOT/usr/include"' in command
                for command in systemd["source"]["build"][1:]
                + systemd["source"]["install"][:1]
            ),
            True,
        )
        # The mime-database trigger shells out to /usr/bin/update-mime-database,
        # which only shared-mime-info provides and this tree does not package.
        # Sage fails a transaction whose trigger executable is absent, so the
        # single io.systemd.xml systemd drops there would fail every install.
        failed += not check(
            "Stage1 systemd ships no mime directory while shared-mime-info is unpackaged",
            any(
                "rm -rf $DESTDIR/usr/share/mime" in command
                for command in systemd["source"]["install"]
            ),
            True,
        )
        failed += not check(
            "Stage1 systemd keeps D-Bus install paths outside the build sysroot",
            all(
                option in systemd["source"]["build"][1]
                for option in (
                    "-D dbuspolicydir=/usr/share/dbus-1/system.d",
                    "-D dbussessionservicedir=/usr/share/dbus-1/services",
                    "-D dbussystemservicedir=/usr/share/dbus-1/system-services",
                    "-D dbus-interfaces-dir=/usr/share/dbus-1/interfaces",
                )
            ),
            True,
        )
        dbus = build.tomllib.loads(
            (REPO / "Stage1" / "recipes" / "dbus" / "recipe.toml").read_text()
        )
        failed += not check(
            "Stage1 dbus installs libraries and units outside the build sysroot",
            "--libdir=lib" in dbus["source"]["build"][1]
            and "-Dsystemd_system_unitdir=/usr/lib/systemd/system"
            in dbus["source"]["build"][1]
            and "-Dsystemd_user_unitdir=/usr/lib/systemd/user"
            in dbus["source"]["build"][1],
            True,
        )
        man_db = build.tomllib.loads(
            (REPO / "Stage1" / "recipes" / "man-db" / "recipe.toml").read_text()
        )
        failed += not check(
            "Stage1 man-db installs systemd data outside the build sysroot",
            "--with-systemdtmpfilesdir=/usr/lib/tmpfiles.d"
            in man_db["source"]["build"][0]
            and "--with-systemdsystemunitdir=/usr/lib/systemd/system"
            in man_db["source"]["build"][0],
            True,
        )
        bzip2_libs = build.tomllib.loads(
            (REPO / "Stage1" / "recipes" / "bzip2-libs" / "recipe.toml").read_text()
        )
        failed += not check(
            "Stage1 bzip2-libs installs every declared shared-library ABI name",
            "ln -sf libbz2.so.1.0.8 $DESTDIR/usr/lib/libbz2.so.1.0"
            in bzip2_libs["source"]["install"],
            True,
        )
        failed += not check(
            "Stage1 bzip2-libs links through the isolated sysroot",
            "$(CC) $(LDFLAGS) -shared" in bzip2_libs["source"]["prepare"][0]
            and "$(CC) $(CFLAGS) $(LDFLAGS) -o bzip2-shared"
            in bzip2_libs["source"]["prepare"][1]
            and bzip2_libs["source"]["build"][-1]
            == "make -j$(nproc) libbz2.a",
            True,
        )
        bison = build.tomllib.loads(
            (REPO / "Stage1" / "recipes" / "bison" / "recipe.toml").read_text()
        )
        failed += not check(
            "Stage1 Bison resolves the available architecture-matched runtime",
            bison["source"]["build"][0].startswith("M4=m4 ./configure ")
            and "TARGET_LOADER=$SC_BUILD_SYSROOT/usr/lib/@SC_DYNAMIC_LINKER@"
            in bison["source"]["build"][1]
            and "for directory in /lib /lib64 /usr/lib /usr/lib64"
            in bison["source"]["build"][1]
            and "PREBISON=\"$TARGET_LOADER --library-path"
            in bison["source"]["build"][1]
            and "TARGET_LOADER=$SC_BUILD_SYSROOT/usr/lib/@SC_DYNAMIC_LINKER@"
            in bison["source"]["install"][0],
            True,
        )
        expect_recipe = build.tomllib.loads(
            (REPO / "Stage1" / "recipes" / "expect" / "recipe.toml").read_text()
        )
        failed += not check(
            "Stage1 Expect bypasses host architecture and Tcl runtime assumptions",
            expect_recipe["package"]["release"] == "4"
            and "--build=@SC_GNU_TRIPLET@" in expect_recipe["source"]["build"][0]
            and ".stage1-tool-wrappers/usr/bin/tclsh8.6"
            in expect_recipe["source"]["install"][0]
            and "env -u LD_LIBRARY_PATH" in expect_recipe["source"]["install"][0]
            and "usr/lib/tcl8.6" in expect_recipe["source"]["install"][0],
            True,
        )
        coreutils = build.tomllib.loads(
            (REPO / "Stage1" / "recipes" / "coreutils" / "recipe.toml").read_text()
        )
        failed += not check(
            "Stage1 coreutils hashes do not retain the seed OpenSSL ABI",
            "--without-openssl" in coreutils["source"]["build"][0]
            and "$ENV{SC_TARGET_RUNNER}" in coreutils["source"]["prepare"][0]
            and "TARGET_LOADER=$SC_BUILD_SYSROOT/usr/lib/@SC_DYNAMIC_LINKER@"
            in coreutils["source"]["build"][1]
            and "for directory in /lib /lib64 /usr/lib /usr/lib64"
            in coreutils["source"]["build"][1]
            and "SC_TARGET_RUNNER=\"$TARGET_LOADER --library-path"
            in coreutils["source"]["build"][1]
            and "TARGET_LOADER=$SC_BUILD_SYSROOT/usr/lib/@SC_DYNAMIC_LINKER@"
            in coreutils["source"]["install"][0]
            and "cu_install_program=install"
            in coreutils["source"]["install"][0],
            True,
        )
        mkinitcpio = build.tomllib.loads(
            (REPO / "Stage1" / "recipes" / "mkinitcpio" / "recipe.toml").read_text()
        )
        mkinitcpio_paths = (
            REPO
            / "Stage1"
            / "recipes"
            / "mkinitcpio"
            / "stage1-runtime-paths.patch"
        ).read_text()
        failed += not check(
            "Stage1 mkinitcpio installs and records only target runtime paths",
            "--libdir=lib" in mkinitcpio["source"]["build"][0]
            and "stage1-runtime-paths.patch" in mkinitcpio["source"]["prepare"][0]
            and "lvm2-runtime-tools.patch" in mkinitcpio["source"]["prepare"][1]
            and "systemd_system_unit_dir = '/usr/lib/systemd/system'"
            in mkinitcpio_paths
            and "tmpfiles_dir = '/usr/lib/tmpfiles.d'" in mkinitcpio_paths
            and "conf_data.set('UDEVD_PATH', '/usr/lib/systemd/systemd-udevd')"
            in mkinitcpio_paths
            and "conf_data.set('TMPFILES_PATH', '/usr/bin/systemd-tmpfiles')"
            in mkinitcpio_paths
            and '[[ -e "$nvpcr" ]] || continue' in mkinitcpio_paths
            and "add_binary sh" in mkinitcpio_paths
            and "LC_ALL=C.UTF-8/LC_ALL=C" in mkinitcpio["source"]["install"][1],
            True,
        )
        mkinitcpio_lvm = (
            REPO
            / "Stage1"
            / "recipes"
            / "mkinitcpio"
            / "lvm2-runtime-tools.patch"
        ).read_text()
        failed += not check(
            "Stage1 mkinitcpio includes LVM userspace without requiring pdata_tools",
            "if command -v pdata_tools" in mkinitcpio_lvm
            and "map add_binary lvm dmsetup" in mkinitcpio_lvm,
            True,
        )
        lvm2 = build.tomllib.loads(
            (REPO / "Stage1" / "recipes" / "lvm2" / "recipe.toml").read_text()
        )
        failed += not check(
            "Stage1 LVM does not advertise absent metadata tools",
            all(
                f"--with-{kind}-{tool}=" in lvm2["source"]["build"][0]
                for kind in ("thin", "cache")
                for tool in ("check", "dump", "repair", "restore")
            ),
            True,
        )
        failed += not check(
            "Stage1 LVM udev rules use the target systemd-run path",
            any(
                ".stage1-tool-wrappers/usr/bin/systemd-run#/usr/bin/systemd-run"
                in command
                for command in lvm2["source"]["install"]
            ),
            True,
        )
        systemd = build.tomllib.loads(
            (REPO / "Stage1" / "recipes" / "systemd" / "recipe.toml").read_text()
        )
        failed += not check(
            "Stage1 systemd builds architecture-matched EFI boot artifacts",
            "python-pyelftools >= 0.33" in systemd["source"]["dependencies"]
            and "-D efi=true" in " ".join(systemd["source"]["build"])
            and "-D bootloader=enabled" in " ".join(systemd["source"]["build"])
            and "-D sbat-distro=sclinux" in " ".join(systemd["source"]["build"]),
            True,
        )
        xfsprogs = build.tomllib.loads(
            (REPO / "Stage1" / "recipes" / "xfsprogs" / "recipe.toml").read_text()
        )
        failed += not check(
            "Stage1 xfsprogs keeps udev rules outside the build sysroot",
            "--with-udev-rule-dir=/usr/lib/udev/rules.d"
            in xfsprogs["source"]["build"][1]
            and "--disable-lib64" in xfsprogs["source"]["build"][1]
            and "so:libhandle.so.1" in xfsprogs["source"]["provides"]
            and "so:libxfs.so.0" not in xfsprogs["source"]["provides"],
            True,
        )
        efivar = build.tomllib.loads(
            (REPO / "Stage1" / "recipes" / "efivar" / "recipe.toml").read_text()
        )
        efivar_runner = (
            REPO / "Stage1" / "recipes" / "efivar" / "target-runner.patch"
        ).read_text()
        failed += not check(
            "Stage1 efivar runs generated tools with the target runtime",
            "target-runner.patch" in efivar["source"]["prepare"][0]
            and "SC_TARGET_RUNNER=\"$SC_BUILD_SYSROOT/usr/lib/@SC_DYNAMIC_LINKER@"
            in efivar["source"]["build"][0]
            and "$(SC_TARGET_RUNNER) ./makeguids" in efivar_runner,
            True,
        )
        efibootmgr = build.tomllib.loads(
            (REPO / "Stage1" / "recipes" / "efibootmgr" / "recipe.toml").read_text()
        )
        failed += not check(
            "Stage1 efibootmgr avoids an unwrapped LTO compiler subprocess",
            "CFLAGS='-O2 -g'" in efibootmgr["source"]["build"][0]
            and "-flto" not in efibootmgr["source"]["build"][0],
            True,
        )
        cmake = build.tomllib.loads(
            (REPO / "Stage1" / "recipes" / "cmake" / "recipe.toml").read_text()
        )
        failed += not check(
            "Stage1 CMake runs bootstrap probes and its temporary binary with the target loader",
            "run-bootstrap-cmake.sh @SC_DYNAMIC_LINKER@"
            in cmake["source"]["prepare"][2]
            and "./${TMPFILE}"
            in cmake["source"]["prepare"][1]
            and "./test"
            in cmake["source"]["prepare"][1]
            and "--direct @SC_DYNAMIC_LINKER@"
            in cmake["source"]["prepare"][1]
            and "../../run-bootstrap-cmake.sh"
            in cmake["source"]["prepare"][1]
            and "../../../run-bootstrap-cmake.sh"
            in cmake["source"]["prepare"][1]
            and "--library-path"
            in (
                REPO / "Stage1" / "recipes" / "cmake" / "run-bootstrap-cmake.sh"
            ).read_text(),
            True,
        )
        failed += not check(
            "Stage1 CMake installs its final ELF through the target loader",
            "run-bootstrap-cmake.sh --direct @SC_DYNAMIC_LINKER@ bin/cmake -P"
            in cmake["source"]["install"][0],
            True,
        )
        failed += not check(
            "Stage1 CMake leaves libc headers after the C++ include_next chain",
            all(
                "env -u CPATH" in command
                for command in cmake["source"]["build"] + cmake["source"]["install"]
            ),
            True,
        )
        linux_zen = build.tomllib.loads(
            (REPO / "Stage1" / "recipes" / "linux-zen" / "recipe.toml").read_text()
        )
        kernel_runner = (
            REPO / "Stage1" / "recipes" / "linux-zen" / "make-stage1-kernel.sh"
        ).read_text()
        failed += not check(
            "Stage1 kernel host tools run with their target runtime closure",
            "make-stage1-kernel.sh @SC_DYNAMIC_LINKER@"
            in linux_zen["source"]["build"][0]
            and any(
                "$(SC_TARGET_RUNNER) $(objtool)" in command
                for command in linux_zen["source"]["prepare"]
            )
            and "--dynamic-linker,$loader" in kernel_runner
            and "--disable-new-dtags,-rpath,$library_path" in kernel_runner
            and "unset CPATH" in kernel_runner
            and 'SC_TARGET_RUNNER="$target_runner"' in kernel_runner,
            True,
        )

        all_output = Path(directory) / "all-recipes"
        all_rendered = build.render_stage1_recipes(
            build.resolve_architecture("aarch64"), all_output
        )
        failed += not check(
            "Stage1 renderer emits every manifest package",
            len(all_rendered),
            120,
        )
        failed += not check(
            "Stage1 renderer assigns one target architecture to every package",
            {
                build.tomllib.loads(path.read_text())["package"]["arch"]
                for path in all_rendered
            },
            {"aarch64"},
        )
        build.validate_rendered_stage1_recipes(
            build.resolve_architecture("aarch64"), all_output
        )
        stale_recipe = all_output / "lvm2" / "recipe.toml"
        stale_recipe.write_text(stale_recipe.read_text() + "# stale\n")
        try:
            build.validate_rendered_stage1_recipes(
                build.resolve_architecture("aarch64"), all_output
            )
            stale_error = "no error"
        except build.ConfigError as exc:
            stale_error = str(exc)
        failed += not check(
            "Stage1 run rejects stale rendered inputs",
            "lvm2/recipe.toml" in stale_error and "stage1-recipes" in stale_error,
            True,
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

        fixture_artifact = fixture_recipe.parent / "fixture-1.0-1-aarch64.pkg.tar.zst"
        fixture_artifact.write_bytes(payload)
        fixture_entry = build.stage1_package_entry(
            "fixture", fixture_recipe, build.resolve_architecture("aarch64")
        )
        failed += not check(
            "Stage1 resumes from an existing architecture-matched artifact",
            fixture_entry["sha256"],
            digest,
        )

        meta_recipe = Path(directory) / "meta" / "recipe.toml"
        meta_recipe.parent.mkdir()
        meta_recipe.write_text(
            "[package]\n"
            'name = "meta"\n'
            'version = "1.0"\n'
            'arch = "aarch64"\n\n'
            'dependencies = ["fixture"]\n\n'
            "prepare = []\n"
            "build = []\n"
            "install = []\n"
        )
        meta_artifact = meta_recipe.parent / "meta-1.0-1-aarch64.pkg.tar.zst"
        meta_artifact.write_bytes(payload)
        meta_sysroot = Path(directory) / "meta-sysroot"
        build.stage_stage1_package(
            {
                "name": "meta",
                "sha256": digest,
                "artifact": meta_artifact.name,
            },
            meta_recipe,
            meta_sysroot,
            {},
        )
        failed += not check(
            "Stage1 stages an empty meta-package without a data archive member",
            (meta_sysroot / ".stage1-build-packages/meta").read_text(),
            digest + "\n",
        )

        replace_recipe = Path(directory) / "replace" / "recipe.toml"
        replace_recipe.parent.mkdir()
        replace_recipe.write_text(
            "[package]\n"
            'name = "replace"\n'
            'version = "1.0"\n'
            'arch = "aarch64"\n\n'
            'install = ["true"]\n'
        )
        replace_artifact = replace_recipe.parent / "replace-1.0-1-aarch64.pkg.tar.zst"
        replace_artifact.write_bytes(payload)
        replace_sysroot = Path(directory) / "replace-sysroot"
        replace_sysroot.mkdir()
        (replace_sysroot / "bin").symlink_to("usr/bin")
        with mock.patch.object(build.subprocess, "run") as mocked_run:
            build.stage_stage1_package(
                {
                    "name": "base-files",
                    "sha256": digest,
                    "artifact": replace_artifact.name,
                },
                replace_recipe,
                replace_sysroot,
                {},
            )
        failed += not check(
            "Stage1 base-files refreshes usr-merge aliases already present in its build sysroot",
            not (replace_sysroot / "bin").exists()
            and not (replace_sysroot / "bin").is_symlink()
            and "--overwrite" in mocked_run.call_args.args[0],
            True,
        )

        sysroot_library = Path(directory) / "build-sysroot" / "usr/lib"
        sysroot_library.mkdir(parents=True)
        (sysroot_library / "legacy.la").write_text("libdir='/usr/lib'\n")
        (sysroot_library / "runtime.so").write_bytes(payload)
        build.clean_stage1_build_sysroot(sysroot_library.parents[1])
        failed += not check(
            "Stage1 drops path-bound libtool metadata only from its build sysroot",
            sorted(path.name for path in sysroot_library.iterdir()),
            ["runtime.so"],
        )

        native_recipe = Path(directory) / "native-recipe"
        native_binary = native_recipe / "pkg/usr/bin/example"
        native_binary.parent.mkdir(parents=True)
        native_binary.write_text("#!/usr/bin/sh\n")
        native_validation_error = ""
        try:
            build.validate_stage1_package_paths(native_recipe, [Path("/")])
            build.validate_stage1_package_shebangs(native_recipe, [Path("/")])
        except build.ConfigError as exc:
            native_validation_error = str(exc)
        failed += not check(
            "Stage1 accepts the native root without disabling workspace leak checks",
            native_validation_error,
            "",
        )

        legacy_layout_recipe = Path(directory) / "legacy-layout-recipe"
        (legacy_layout_recipe / "pkg/usr/sbin").mkdir(parents=True)
        try:
            build.validate_stage1_usr_merge_layout(legacy_layout_recipe, "fixture")
        except build.ConfigError as exc:
            legacy_layout_error = str(exc)
        else:
            legacy_layout_error = "no error"
        failed += not check(
            "Stage1 rejects package payloads below usr-merge aliases",
            "non-canonical usr-merge path: usr/sbin" in legacy_layout_error,
            True,
        )

        generated_file_recipe = Path(directory) / "generated-file-recipe"
        generated_cache = generated_file_recipe / "pkg/etc/ld.so.cache"
        generated_cache.parent.mkdir(parents=True)
        generated_cache.write_bytes(b"cache")
        try:
            build.validate_stage1_runtime_generated_files(
                generated_file_recipe, "fixture"
            )
        except build.ConfigError as exc:
            generated_file_error = str(exc)
        else:
            generated_file_error = "no error"
        failed += not check(
            "Stage1 rejects runtime-generated files in package payloads",
            "runtime-generated file: etc/ld.so.cache" in generated_file_error,
            True,
        )

        generated_info_recipe = Path(directory) / "generated-info-recipe"
        generated_info = (
            generated_info_recipe
            / "pkg/opt/channels/gcc/15/share/info/dir"
        )
        generated_info.parent.mkdir(parents=True)
        generated_info.write_text("generated index\n")
        try:
            build.validate_stage1_runtime_generated_files(
                generated_info_recipe, "fixture"
            )
        except build.ConfigError as exc:
            generated_info_error = str(exc)
        else:
            generated_info_error = "no error"
        failed += not check(
            "Stage1 rejects generated info indexes below toolchain channels",
            "runtime-generated file: opt/channels/gcc/15/share/info/dir"
            in generated_info_error,
            True,
        )

        sysroot_binary = sysroot_library.parent / "bin/pkgconf"
        sysroot_binary.parent.mkdir(parents=True)
        sysroot_binary.write_bytes(b"\x7fELFfixture")
        sysroot_binary.chmod(0o755)
        (sysroot_binary.parent / "pkg-config").symlink_to("pkgconf")
        (sysroot_binary.parent / "perl").symlink_to("pkgconf")
        (sysroot_binary.parent / "cmake").symlink_to("pkgconf")
        (sysroot_binary.parent / "make").symlink_to("pkgconf")
        cmake_modules = sysroot_library.parent / "share/cmake-3.31"
        cmake_modules.mkdir(parents=True)
        gcc_binary = (
            sysroot_library.parents[1] / "opt/channels/gcc/15/bin/gcc"
        )
        gcc_binary.parent.mkdir(parents=True)
        gcc_binary.write_bytes(b"\x7fELFfixture")
        gcc_binary.chmod(0o755)
        gcc_ld = gcc_binary.parent / "ld"
        gcc_ld.write_bytes(b"\x7fELFfixture")
        gcc_ld.chmod(0o755)
        gcc_cc1 = (
            sysroot_library.parents[1]
            / "opt/channels/gcc/15/libexec/gcc/aarch64-linux-gnu/15.3.0/cc1"
        )
        gcc_cc1.parent.mkdir(parents=True)
        gcc_cc1.write_bytes(b"\x7fELFfixture")
        gcc_cc1.chmod(0o755)
        gcc_plugin = gcc_cc1.parent / "liblto_plugin.so"
        gcc_plugin.write_bytes(b"\x7fELFfixture")
        gcc_plugin.chmod(0o755)
        xmake_launcher = (
            sysroot_library.parents[1] / "opt/channels/xmake/3/bin/xmake"
        )
        xmake_launcher.parent.mkdir(parents=True)
        xmake_launcher.write_text("#!/bin/sh\nexec /opt/channels/xmake/3/bin/xmake.real \"$@\"\n")
        xmake_launcher.chmod(0o755)
        xmake_binary = xmake_launcher.with_name("xmake.real")
        xmake_binary.write_bytes(b"\x7fELFfixture")
        xmake_binary.chmod(0o755)
        perl_root = sysroot_library / "perl5/5.44/core_perl"
        (perl_root / "CORE").mkdir(parents=True)
        autoconf_modules = sysroot_library.parent / "share/autoconf"
        autoconf_modules.mkdir(parents=True)
        (sysroot_binary.parent / "autom4te").write_text("#!/bin/sh\n")
        build.refresh_stage1_tool_wrappers(
            sysroot_library.parents[1], build.resolve_architecture("aarch64")
        )
        tool_wrapper_root = sysroot_library.parents[1] / ".stage1-tool-wrappers"
        wrapper_root = tool_wrapper_root / "usr/bin"
        failed += not check(
            "Stage1 wraps only target ELF tools with their isolated runtime libraries",
            sorted(path.name for path in wrapper_root.iterdir()),
            ["cmake", "gmake", "make", "perl", "pkg-config", "pkgconf"],
        )
        failed += not check(
            "Stage1 defers its GCC wrappers until target libc is complete",
            not (tool_wrapper_root / "gcc-bin").exists()
            and not (tool_wrapper_root / "gcc-libexec").exists(),
            True,
        )
        failed += not check(
            "Stage1 keeps gmake on the same target runtime as make",
            (wrapper_root / "gmake").resolve(),
            (wrapper_root / "make").resolve(),
        )
        failed += not check(
            "Stage1 exposes the locked xmake channel through a target wrapper",
            (tool_wrapper_root / "xmake-bin/xmake").is_symlink()
            and (tool_wrapper_root / "xmake-bin/xmake").resolve()
            == (tool_wrapper_root / "xmake-bin/xmake.real").resolve(),
            True,
        )
        xmake_wrapper = (tool_wrapper_root / "xmake-bin/xmake").read_text()
        failed += not check(
            "Stage1 xmake wrapper preserves its installed module root",
            f"--argv0 {xmake_binary} " in xmake_wrapper
            and f"export XMAKE_PROGRAM_DIR={sysroot_library.parents[1].resolve() / 'opt/channels/xmake/3/share/xmake'}"
            in xmake_wrapper,
            True,
        )
        failed += not check(
            "Stage1 CMake wrapper preserves recursive target-loader execution",
            '--argv0 "$0"' in (wrapper_root / "cmake").read_text()
            and (tool_wrapper_root / "usr/share/cmake-3.31").resolve()
            == cmake_modules.resolve(),
            True,
        )
        wrapper = (wrapper_root / "pkgconf").read_text()
        failed += not check(
            "Stage1 tool wrappers do not leak target libraries to child processes",
            "exec /lib/ld-linux-aarch64.so.1 --library-path" in wrapper
            and '--argv0 "$0"' in wrapper
            and "LD_LIBRARY_PATH" not in wrapper,
            True,
        )
        failed += not check(
            "Stage1 tool wrappers locate private interpreter libraries",
            str(perl_root / "CORE") in wrapper,
            True,
        )
        perl_wrapper = (wrapper_root / "perl").read_text()
        failed += not check(
            "Stage1 interpreters locate modules inside the isolated sysroot",
            f" -I{perl_root} " in perl_wrapper and "PERL5LIB" not in perl_wrapper,
            True,
        )
        target_loader = sysroot_library / "ld-linux-aarch64.so.1"
        target_loader.write_bytes(b"\x7fELFfixture")
        (sysroot_library / "libc.so.6").write_bytes(b"\x7fELFfixture")
        target_stdio = sysroot_library.parent / "include/stdio.h"
        target_stdio.parent.mkdir(parents=True)
        target_stdio.write_text("/* fixture */\n")
        build.refresh_stage1_tool_wrappers(
            sysroot_library.parents[1], build.resolve_architecture("aarch64")
        )
        failed += not check(
            "Stage1 GCC wrapper runs compiler subprograms through the target loader",
            f"-B{tool_wrapper_root / 'gcc-libexec'}/"
            in (tool_wrapper_root / "gcc-bin/gcc").read_text()
            and f"--sysroot={sysroot_library.parents[1].resolve()}"
            in (tool_wrapper_root / "gcc-bin/gcc").read_text()
            and f"-Wl,-rpath-link,{sysroot_library.resolve()}"
            in (tool_wrapper_root / "gcc-bin/gcc").read_text()
            and "-fuse-ld=lld"
            in (tool_wrapper_root / "gcc-bin/gcc").read_text()
            and (tool_wrapper_root / "gcc-libexec/cc1").is_file()
            and (tool_wrapper_root / "gcc-libexec/liblto_plugin.so").resolve()
            == gcc_plugin.resolve()
            and (tool_wrapper_root / "gcc-bin/ld.lld").resolve()
            == (tool_wrapper_root / "gcc-bin/ld").resolve(),
            True,
        )
        failed += not check(
            "Stage1 switches to its own dynamic loader after glibc is staged",
            f"exec {target_loader.resolve()} --library-path"
            in (wrapper_root / "pkgconf").read_text(),
            True,
        )
        interpreter_environment = build.stage1_build_environment(
            seed, sysroot_library.parents[1]
        )
        failed += not check(
            "Stage1 links through its own sysroot after glibc is staged",
            interpreter_environment["LDFLAGS"].split()[0],
            f"--sysroot={sysroot_library.parents[1].resolve()}",
        )
        failed += not check(
            "Stage1 scripts locate Autoconf modules inside the isolated sysroot",
            {
                interpreter_environment["autom4te_perllibdir"],
                interpreter_environment["AC_MACRODIR"],
            },
            {str(autoconf_modules)},
        )
        failed += not check(
            "Stage1 Autom4te loads its isolated configuration",
            interpreter_environment["AUTOM4TE_CFG"],
            str(autoconf_modules / "autom4te.cfg"),
        )
        failed += not check(
            "Stage1 Autoconf loads its isolated trailer",
            interpreter_environment["trailer_m4"],
            str(autoconf_modules / "autoconf/trailer.m4"),
        )
        failed += not check(
            "Stage1 Autotools scripts call sibling tools inside the isolated sysroot",
            interpreter_environment["AUTOM4TE"],
            f"{sysroot_binary.parent / 'autom4te'} --prepend-include={autoconf_modules}",
        )
        failed += not check(
            "Stage1 CMake discovers the target-loader pkg-config wrapper",
            interpreter_environment["PKG_CONFIG"],
            str(wrapper_root / "pkg-config"),
        )

        leaking_script = fixture_recipe.parent / "pkg/usr/bin/leak"
        leaking_script.parent.mkdir(parents=True)
        leaking_script.write_text(f"#! {sysroot_binary}\n")
        try:
            build.validate_stage1_package_shebangs(
                fixture_recipe.parent, [sysroot_library.parents[1]]
            )
        except build.ConfigError as exc:
            shebang_error = str(exc)
        else:
            shebang_error = "no error"
        failed += not check(
            "Stage1 rejects package shebangs that retain temporary build paths",
            "shebang contains a build path" in shebang_error,
            True,
        )

        leaking_payload = (
            fixture_recipe.parent
            / "pkg"
            / Path(*sysroot_library.parents[1].resolve().parts[1:])
            / "usr/lib/leak"
        )
        leaking_payload.parent.mkdir(parents=True)
        leaking_payload.write_text("fixture")
        try:
            build.validate_stage1_package_paths(
                fixture_recipe.parent, [sysroot_library.parents[1]]
            )
        except build.ConfigError as exc:
            payload_error = str(exc)
        else:
            payload_error = "no error"
        failed += not check(
            "Stage1 rejects payloads nested below a temporary build path",
            "payload contains a build path" in payload_error,
            True,
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

        boot_output = Path(directory) / "sclinux-boot" / "recipe.toml"
        build.render_stage1_recipe(
            "sclinux-boot", build.resolve_architecture("aarch64"), boot_output
        )
        boot_text = boot_output.read_text()
        boot_helper = (boot_output.parent / "sclinux-update-boot.in").read_text()
        kernel_install = (boot_output.parent / "90-sclinux-boot.install").read_text()
        failed += not check(
            "Stage1 boot integration selects AArch64 EFI and serial paths",
            "systemd-bootaa64.efi" in boot_text
            and "BOOTAA64.EFI" in boot_text
            and "ttyAMA0,115200" in boot_text
            and "root=/dev/mapper/vg0-root rd.lvm.lv=vg0/root" in boot_text
            and "SCLINUX_ESP:-/boot/efi" in boot_helper
            and "SCLINUX_ESP:-/boot/efi" in kernel_install,
            True,
        )
        failed += not check(
            "Stage1 renderer copies boot integration helpers",
            (boot_output.parent / "sclinux-update-boot.in").is_file()
            and (boot_output.parent / "90-sclinux-boot.install").is_file(),
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

    print(f"\n{checks_run - failed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
