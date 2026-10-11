#!/usr/bin/env bash
# Run the pipeline in Docker.
#   ./watchalong.sh <stream> <episode>... [options]
#   ./watchalong.sh --job my-job.txt [options]
#
# Local files can be anywhere and given with their normal paths, relative or absolute. This
# script finds every local path in the arguments and job files (streams, episodes, job
# files, cookies) and shares just the folders containing them with the container, read-only
# and at the same paths. Downloads, cache and output go to ./data next to this script.
set -e
repo="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$repo/data"

mounts=()

# Share the folder holding a local path (the folder itself if it is one). Folders rather than
# single files, so a subtitle file next to an episode is visible too. URLs and magnets are
# skipped; missing paths are left for the program to report.
share() {
  local p="$1" base="$2" dir m
  case "$p" in
    [a-zA-Z]*://* | magnet:*) return 0 ;;
    "~") p="$HOME" ;;
    "~/"*) p="$HOME/${p#\~/}" ;;
  esac
  case "$p" in /*) ;; *) p="$base/$p" ;; esac
  if [ -d "$p" ]; then
    dir="$(cd "$p" && pwd)"
  elif [ -e "$p" ]; then
    dir="$(cd "$(dirname "$p")" && pwd)"
  else
    return 0
  fi
  for m in "${mounts[@]}"; do
    if [ "$m" = "$dir" ]; then return 0; fi
  done
  mounts+=("$dir")
}

# The local paths in a job file: stream/episode lines and the cookies setting, relative to
# the job file's folder.
share_job_file() {
  local file="$1" base line key value
  base="$(cd "$(dirname "$file")" && pwd)"
  share "$file" "$PWD"
  while IFS= read -r line || [ -n "$line" ]; do
    line="$(printf '%s' "$line" | tr -d '\r' | sed -E 's/[[:space:]]+#.*$//; s/^[[:space:]]+//; s/[[:space:]]+$//')"
    case "$line" in "" | "#"*) continue ;; esac
    key="$(printf '%s' "${line%% *}" | tr '[:upper:]' '[:lower:]')"
    value="$(printf '%s' "${line#* }" | sed -E 's/^[[:space:]]+//; s/[[:space:]]+skip=[0-9.]+$//')"
    if [ "$value" = "$line" ]; then continue; fi  # a key with no value
    case "$key" in stream | episode | anime | cookies) share "$value" "$base" ;; esac
  done < "$file"
}

share_job() {
  local f
  if [ -d "$1" ]; then
    share "$1" "$PWD"
    for f in "$1"/*.txt; do
      if [ -f "$f" ]; then share_job_file "$f"; fi
    done
  elif [ -f "$1" ]; then
    share_job_file "$1"
  fi
}

expect=""
for arg in "$@"; do
  case "$expect" in
    job) share_job "$arg"; expect=""; continue ;;
    path) share "$arg" "$PWD"; expect=""; continue ;;
    value) expect=""; continue ;;  # e.g. the "none" of --subs none
  esac
  case "$arg" in
    --job) expect=job ;;
    --job=*) share_job "${arg#--job=}" ;;
    --cookies) expect=path ;;
    --cookies=*) share "${arg#--cookies=}" "$PWD" ;;
    --*=* | -h | --help | -v | --verbose | --dry-run | --force | --no-render | --no-auto-vocab | --no-duck) ;;
    -*) expect=value ;;  # every other option takes a value
    *) share "$arg" "$PWD" ;;  # positional: the stream and episodes
  esac
done

args=()
for m in "${mounts[@]}"; do
  args+=(-v "$m:$m:ro")
  echo "sharing with Docker (read-only): $m" >&2
done

exec docker compose -f "$repo/compose.yaml" run --rm --build "${args[@]}" \
  -e WATCHALONG_HOST_HOME="$HOME" -w "$PWD" watchalong "$@"
