#!/usr/bin/env sh
# Run the pipeline in Docker.
#   ./watchalong.sh <stream> <episode>... [options]
#   ./watchalong.sh --job my-job.txt [options]
#
# Your home folder (and /Volumes on macOS) is mounted read-only at the same path inside the
# container, so local files and job files can be given with their normal paths, relative or
# absolute. Downloads, cache and output go to ./data next to this script.
set -e
repo="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$repo/data"

mounts="-v $HOME:$HOME:ro"
[ -d /Volumes ] && mounts="$mounts -v /Volumes:/Volumes:ro"

# shellcheck disable=SC2086
exec docker compose -f "$repo/compose.yaml" run --rm --build $mounts -w "$PWD" watchalong "$@"
