#!/usr/bin/env sh
set -eu

runtime_uid=${LUMINA_RUNTIME_UID:-1000}
runtime_gid=${LUMINA_RUNTIME_GID:-1000}
render_gid=${LUMINA_RENDER_GID:-}
data_dir=${LUMINA_DATA_DIR:-/app/backend/.data}

case "$runtime_uid:$runtime_gid" in
  *[!0-9:]*|0:*|*:0|:*|*:)
    echo "LUMINA_RUNTIME_UID and LUMINA_RUNTIME_GID must be non-zero numeric IDs." >&2
    exit 1
    ;;
esac

# Supplementary groups are always cleared. The only group ever granted is the host's
# render group (docker-compose.qsv.yml), so /dev/dri works and nothing else leaks in.
case "$render_gid" in
  '') groups=--clear-groups ;;
  *[!0-9]*|0*)
    echo "LUMINA_RENDER_GID must be the host render group's non-zero numeric ID." >&2
    exit 1
    ;;
  *) groups="--groups=$render_gid" ;;
esac

mkdir -p "$data_dir" "$HOME"
chown "$runtime_uid:$runtime_gid" "$HOME"
# Only walk the (possibly large) data tree when its owner changed, e.g. first start or a new UID/GID.
if [ "$(stat -c %u:%g "$data_dir")" != "$runtime_uid:$runtime_gid" ]; then
  chown -R "$runtime_uid:$runtime_gid" "$data_dir"
fi

exec setpriv --reuid="$runtime_uid" --regid="$runtime_gid" "$groups" python -m uvicorn app.main:app --host 0.0.0.0 --port 8765 --no-proxy-headers --no-access-log --timeout-keep-alive 30
