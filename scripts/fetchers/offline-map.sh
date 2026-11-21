#!/usr/bin/env bash
# Install offline street maps viewable in Pergamon's Maps app.
#
# Env vars:
#   MAP_REGIONS — '|'-separated regions, each described any way rag/maps.py
#                 accepts: a town or county ("Keller, TX"), a named region
#                 ("North Texas", "Atlanta Metro"), "lat,lon miles", a box, or
#                 several places joined with ';'.
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

: "${MAP_REGIONS:?required}"
failed=0
while IFS= read -r region; do
  region="$(echo "$region" | xargs)"
  [[ -z "$region" ]] && continue
  # "lat,lon 25" or "Place 25": a trailing number is a radius in miles
  radius=""
  if [[ "$region" =~ ^(.*[^0-9.])[[:space:]]+([0-9]+(\.[0-9]+)?)$ ]]; then
    region="${BASH_REMATCH[1]}"; radius="${BASH_REMATCH[2]}"
  fi
  if [[ "$TEST_MODE" == 1 ]]; then
    if out="$(cd "$REPO_ROOT" && "$PY" -m rag.maps install "$region" $radius --dry-run 2>&1)"; then
      size="$(grep -oE 'archive size of [0-9.]+ [KMG]B' <<< "$out" | tail -1)"
      ok "map '$region': $size"
      record PASS download "map $region" "${size:-sized}; would download from Protomaps"
    else
      err "map '$region': $(tail -1 <<< "$out")"
      record FAIL download "map $region" "$(tail -1 <<< "$out")"
      failed=1
    fi
    continue
  fi
  log "map: $region"
  (cd "$REPO_ROOT" && "$PY" -m rag.maps install "$region" $radius) || failed=1
done < <(printf '%s\n' "${MAP_REGIONS//|/$'\n'}")
exit "$failed"
