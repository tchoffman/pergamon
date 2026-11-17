#!/usr/bin/env bash
# Fetch OsmAnd map packs for regions, with optional terrain layers.
#
# Env vars:
#   OSM_REGIONS — comma-separated regions, as people would write them:
#                "us-georgia", "us-north-carolina", "germany-berlin".
#                Each is looked up in OsmAnd's index (rag/osm_regions.py) to
#                find the real file names.
#   OSM_LAYERS  — comma-separated layers (default: streets):
#                streets          roads, addresses, places, water, woods, trails
#                contours-feet    contour lines in feet (or contours-meters)
#                hillshade        shaded relief
#                slope            slope steepness shading
#                wikipedia        Wikipedia articles as map points
#                Full list: https://download.osmand.net/list.php
#
# Files land in build/corpus/maps/: <region>.obf.zip for the street map and
# <region>.<layer>.<ext> for the rest. OsmAnd on Android/iOS opens them.

source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

: "${OSM_REGIONS:?required (comma-separated region codes)}"
LAYERS="${OSM_LAYERS:-streets}"

IFS=',' read -r -a regions <<< "$OSM_REGIONS"
failed=0

for region in "${regions[@]}"; do
  region="$(echo "$region" | xargs)"
  [[ -z "$region" ]] && continue
  if ! plan="$(cd "$REPO_ROOT" && "$PY" -m rag.osm_regions "$region" "$LAYERS")" || [[ -z "$plan" ]]; then
    err "can't find OsmAnd maps for '$region' (see above)"
    record FAIL download "map $region" "no OsmAnd map by that name"
    failed=1
    continue
  fi
  while IFS=$'\t' read -r url file layer; do
    if [[ "$layer" == streets ]]; then
      out="$MAP_DIR/${region}.obf.zip"
    else
      out="$MAP_DIR/${region}.${layer}.${file#*.}"
    fi
    log "$region $layer → $file"
    PERGAMON_ITEM="map $region $layer" fetch_url "$url" "$out" 3600 || failed=1
  done <<< "$plan"
done
exit "$failed"
