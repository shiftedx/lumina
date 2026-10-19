#!/bin/sh
# Retain an image against pruning without running a service or its inherited health check.
set -eu
if [ "$#" -ne 2 ]; then
    echo "Usage: $0 IMAGE CONTAINER_NAME" >&2
    exit 2
fi
exec docker create --name "$2" --no-healthcheck --restart=no --network=none \
    --label traefik.enable=false --label com.centurylinklabs.watchtower.enable=false \
    --entrypoint /bin/true "$1"
