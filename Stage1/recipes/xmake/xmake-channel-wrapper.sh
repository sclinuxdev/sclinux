#!/bin/sh

XMAKE_PROGRAM_FILE=${XMAKE_PROGRAM_FILE:-/opt/channels/xmake/3/bin/xmake.real}
export XMAKE_PROGRAM_FILE

exec /opt/channels/xmake/3/bin/xmake.real "$@"
