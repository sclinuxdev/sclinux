#!/bin/sh
set -eu

direct=false
if [ "$1" = "--direct" ]; then
    direct=true
    shift
fi

dynamic_linker=$1
binary=$2
shift 2

real_binary=$binary.stage1
loader=$SC_BUILD_SYSROOT/usr/lib/$dynamic_linker
library_path=$SC_BUILD_SYSROOT/usr/lib:$SC_BUILD_SYSROOT/usr/lib64:$SC_BUILD_SYSROOT/lib:$SC_BUILD_SYSROOT/lib64:$SC_BUILD_SYSROOT/opt/channels/gcc/15/lib64:$SC_BUILD_SYSROOT/opt/channels/gcc/15/lib

if [ "$direct" = false ]; then
    mv -f "$binary" "$real_binary"
    {
        printf '%s\n' '#!/bin/sh'
        printf 'exec "%s" --library-path "%s" "%s" "$@"\n' \
            "$loader" "$library_path" "$real_binary"
    } > "$binary"
    chmod 755 "$binary"
    binary=$real_binary
fi

exec "$loader" --library-path "$library_path" "$binary" "$@"
