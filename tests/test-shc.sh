#!/bin/sh
# Behavioural tests for scripts/shc.
#
# Runs the wrapper against a stub `sage` that echoes its arguments, so the
# exact argument vector -- including spacing and quoting -- can be asserted.
#
#   sh tests/test-shc.sh

set -eu

CDPATH=''
repo_root=$(cd -- "$(dirname -- "$0")/.." && pwd)
shc="$repo_root/scripts/shc"

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

# Stub sage: prints each argument bracketed, so "a b" and "a" "b" differ.
cat > "$tmp/sage" <<'STUB'
#!/bin/sh
out=""
for a in "$@"; do out="$out[$a]"; done
printf '%s\n' "$out"
STUB
chmod +x "$tmp/sage"
SAGE_BIN="$tmp/sage"
export SAGE_BIN

fail=0
pass=0

check() {
    expected=$1
    shift
    actual=$(sh "$shc" "$@" 2>&1) || true
    if [ "$actual" = "$expected" ]; then
        pass=$((pass + 1))
    else
        fail=$((fail + 1))
        printf 'FAIL  shc %s\n  expected: %s\n  actual:   %s\n' "$*" "$expected" "$actual"
    fi
}

# --- aliases expand in the subcommand position ---
check '[install][hyprland][neofetch-mew]' in hyprland neofetch-mew
check '[remove][nginx]'                   rm nginx
check '[rebuild]'                         rb
check '[query][owner][/usr/bin/rg]'       q owner /usr/bin/rg
check '[build][./packages/shc]'           b ./packages/shc
check '[channel]'                         ch
check '[service][list]'                   sv list
check '[toolchain][use][rust:nightly]'    tc use rust:nightly
check '[status][--full]'                  st --full

# --- global options may precede the subcommand ---
check '[--verbose][install][foo]'         --verbose in foo
check '[--dry-run][rebuild]'              --dry-run rb
check '[--root][/mnt][install][foo]'      --root /mnt in foo
check '[--sysroot][/mnt][remove][pkg]'    --sysroot /mnt rm pkg

# --- canonical names pass through untouched ---
check '[install][foo]'                    install foo
check '[toolchain][list]'                 toolchain list

# --- unknown commands are not mangled ---
check '[bogus][arg]'                      bogus arg

# --- an alias-looking word AFTER the subcommand must NOT expand ---
# Regression guard: a package literally named `rm` or `in` must survive.
check '[query][in]'                       query in
check '[install][rm]'                     install rm
check '[install][b]'                      in b

# --- quoting is preserved ---
check '[install][my package]'             in "my package"

# --- no arguments ---
check ''

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
