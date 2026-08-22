#!/bin/sh
set -eu

dynamic_linker=$1
shift

loader=$SC_BUILD_SYSROOT/usr/lib/$dynamic_linker
library_path=$SC_BUILD_SYSROOT/usr/lib:$SC_BUILD_SYSROOT/usr/lib64:$SC_BUILD_SYSROOT/lib:$SC_BUILD_SYSROOT/lib64:$SC_BUILD_SYSROOT/opt/channels/gcc/15/lib64:$SC_BUILD_SYSROOT/opt/channels/gcc/15/lib
host_ldflags="-Wl,--dynamic-linker,$loader -Wl,--disable-new-dtags,-rpath,$library_path"
target_runner="$loader --library-path $library_path"

unset CPATH
exec make HOSTLDFLAGS="$host_ldflags" SC_TARGET_RUNNER="$target_runner" "$@"
