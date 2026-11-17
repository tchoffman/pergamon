#!/usr/bin/env bash
# Fetch OsmAnd regional map packs.
#
# Env vars:
#   OSM_REGIONS — comma-separated regions, as people would write them:
#                "us-georgia", "us-north-carolina", "germany-berlin".
#                Each is looked up in OsmAnd's index (rag/osm_regions.py) to
#                find the real file name, e.g. Us_georgia_northamerica_2.obf.zip.
#                Full list: https://download.osmand.net/list.php
#
# Puts each region at build/corpus/maps/<region>.obf.zip. OsmAnd on Android/iOS
# can consume these directly; headless use via the obf tool is possible but out
# of scope for v0.

source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

: "${OSM_REGIONS:?required (comma-separated region codes)}"

IFS=',' read -r -a regions <<< "$OSM_REGIONS"
failed=0

for region in "${regions[@]}"; do
  region="$(echo "$region" | xargs)"
  [[ -z "$region" ]] && continue
  if ! file="$(cd "$REPO_ROOT" && "$PY" -m rag.osm_regions "$region")"; then
    err "can't find an OsmAnd map for '$region' (see above)"
    record FAIL download "map $region" "no OsmAnd map by that name"
    failed=1
    continue
  fi
  log "$region → $file"
  url="https://download.osmand.net/download?standard=yes&file=${file}"
  out="$MAP_DIR/${region}.obf.zip"
  PERGAMON_ITEM="map $region" fetch_url "$url" "$out" 1800 || failed=1
done
exit "$failed"
