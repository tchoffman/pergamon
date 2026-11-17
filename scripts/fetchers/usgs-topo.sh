#!/usr/bin/env bash
# Fetch USGS topographic maps (GeoPDF) around a place. United States only.
#
# Env vars:
#   TOPO_AREA      — a place ("Asheville, NC") or "lat,lon"
#   TOPO_RADIUS    — miles around it (default 10)
#   TOPO_EDITIONS  — current and/or historical (default: current,historical)
#
# See rag/usgs_topo.py for what each edition shows. Files land in
# build/corpus/maps/usgs-topo/{current,historical}/.

source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

: "${TOPO_AREA:?required (a place or lat,lon)}"
RADIUS="${TOPO_RADIUS:-10}"
EDITIONS="${TOPO_EDITIONS:-current,historical}"

if ! plan="$(cd "$REPO_ROOT" && "$PY" -m rag.usgs_topo "$TOPO_AREA" "$RADIUS" "$EDITIONS")"; then
  err "couldn't plan USGS topo maps for '$TOPO_AREA' (see above)"
  record FAIL download "usgs-topo" "no maps found for '$TOPO_AREA'"
  exit 1
fi
log "$(wc -l <<< "$plan" | tr -d ' ') maps within $RADIUS miles of $TOPO_AREA"

failed=0
n_current=0; n_historical=0
while IFS=$'\t' read -r url file label; do
  edition="${label%% *}"
  if [[ "$TEST_MODE" == 1 ]]; then  # install test: two maps per edition prove the links
    if [[ "$edition" == current ]]; then n_current=$((n_current + 1)); n=$n_current
    else n_historical=$((n_historical + 1)); n=$n_historical; fi
    [[ $n -gt 2 ]] && continue
  fi
  mkdir -p "$MAP_DIR/usgs-topo/$edition"
  log "$label"
  PERGAMON_ITEM="usgs-topo $label" fetch_url "$url" "$MAP_DIR/usgs-topo/$edition/$file" 1800 || failed=1
done <<< "$plan"
exit "$failed"
