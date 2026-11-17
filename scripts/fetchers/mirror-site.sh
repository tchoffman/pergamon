#!/usr/bin/env bash
# Mirror a website for offline reference using wget.
#
# Env vars:
#   MIRROR_URL           — seed URL
#   MIRROR_ALLOWED_HOSTS — comma-separated hosts to crawl
#   MIRROR_DEPTH         — recursion depth (default: 3)
#   OUTPUT_NAME          — subdirectory under build/corpus/mirrors/
#
# Uses wget --mirror with sensible converters so HTML works offline.

source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

: "${MIRROR_URL:?required}"
: "${OUTPUT_NAME:?required}"
DEPTH="${MIRROR_DEPTH:-3}"
HOSTS="${MIRROR_ALLOWED_HOSTS:-}"

if ! command -v wget >/dev/null 2>&1; then
  if command -v brew >/dev/null 2>&1; then
    log "wget not installed; installing via brew"
    brew install wget
  else
    err "wget required and not installable. Install manually."
    exit 1
  fi
fi

dest="$MIRROR_DIR/$OUTPUT_NAME"
mkdir -p "$dest"

args=(
  --mirror
  --convert-links
  --adjust-extension
  --page-requisites
  --no-parent
  --level="$DEPTH"
  --execute robots=off
  --wait=0.5
  --random-wait
  --timeout=30
  --tries=3
  --quiet
  --show-progress
  --no-verbose
  --directory-prefix="$dest"
)
if [[ "$TEST_MODE" == 1 ]]; then
  args=("${args[@]/--level=$DEPTH/--level=1}" --quota=1m)  # install test: the front page and a few links
fi
if [[ -n "$HOSTS" ]]; then
  args+=( --domains="$HOSTS" )
fi

log "mirroring $MIRROR_URL into $dest (depth $DEPTH)"
wget "${args[@]}" "$MIRROR_URL" || warn "wget returned non-zero; partial mirror kept"
ok "mirror $(basename "$dest") — $(du -sh "$dest" 2>/dev/null | cut -f1)"
if [[ "$TEST_MODE" == 1 ]]; then
  pages="$(find "$dest" -type f -name '*.htm*' | wc -l | tr -d ' ')"
  if [[ "$pages" -gt 0 ]]; then record PASS download "$OUTPUT_NAME" "$pages pages mirrored (depth 1) · $MIRROR_URL"
  else record FAIL download "$OUTPUT_NAME" "no pages mirrored · $MIRROR_URL"; fi
fi
