#!/usr/bin/env bash
# Install the Entertainment pack (films, audiobooks, music) into the library.
#   MEDIA_PACK — sampler (~1.1 GB) or full (~3.4 GB)
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"
PACK="${MEDIA_PACK:-sampler}"
if [[ "$TEST_MODE" == 1 ]]; then
  # Install test: every item reachable with the expected size; nothing downloaded.
  failed=0
  while IFS=$'\t' read -r status title detail; do
    [[ -z "$status" ]] && continue
    record "$status" download "media: $title" "$detail"
    [[ "$status" == FAIL ]] && failed=1
  done < <(cd "$REPO_ROOT" && "$PY" -m rag.entertainment check "$PACK")
  exit "$failed"
fi
cd "$REPO_ROOT" && exec "$PY" -m rag.entertainment install "$PACK"
