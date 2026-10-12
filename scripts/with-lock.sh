#!/usr/bin/env bash
# Runs a command while holding a machine-wide kernel lock on <lock file>, so only one runs at a time across every
# checkout on this host. The OS drops the lock when the holder exits or dies. Waiting is bounded: exit 75 on timeout.
#   usage: scripts/with-lock.sh <lock file> <max wait seconds> <command...>
set -u
lock=$1 max_wait=$2
shift 2
echo "with-lock: waiting for perf lock $lock (up to ${max_wait}s)"
if command -v lockf >/dev/null; then exec lockf -k -t "$max_wait" "$lock" "$@"; fi # macOS/BSD: 75 (EX_TEMPFAIL) on timeout
exec flock -w "$max_wait" -E 75 "$lock" "$@" # Linux (util-linux)
