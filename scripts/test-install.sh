#!/usr/bin/env bash
# Test-drive the installer quickly, without touching your real install.
#
# Runs the real install steps (setup choices → models → downloads → index →
# services) inside build/test-install/, with three shortcuts:
#   - downloads stop after the first 2 MB of each file (TEST_BYTES), and each
#     sample is checked to be the right kind of file (ZIM, PDF, 7z, zip, text);
#   - models are looked up in the Ollama registry instead of pulled;
#   - service images are looked up in their registries instead of pulled.
# A tiny real library (about 1 MB) is downloaded whole, indexed and searched,
# to prove the pipeline end to end. Ends with a pass/fail report.
#
# Usage:
#   scripts/test-install.sh               # test what your install config would install
#   scripts/test-install.sh --choose      # pick in the setup menu first (choices stay in the sandbox)
#   scripts/test-install.sh --everything  # every item, every variant, every model and service
#
# Env: TEST_BYTES=N to sample more or less of each file (default 2097152).
set -euo pipefail
cd "$(dirname "$0")/.."
REPO="$PWD"
PY="$REPO/build/venv/bin/python"
[[ -x "$PY" ]] || { echo "Run 'make venv' first." >&2; exit 1; }

MODE="${1:-current}"
case "$MODE" in
  current|--choose|--everything) ;;
  -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
  *) echo "Unknown option: $MODE (try --choose or --everything)" >&2; exit 2 ;;
esac

SB="$REPO/build/test-install"
rm -rf "$SB"
mkdir -p "$SB"
export PERGAMON_SANDBOX="$SB" PERGAMON_TEST_INSTALL=1 TEST_BYTES="${TEST_BYTES:-2097152}"
START=$(date +%s)
echo "==> Install test run in $SB (your real config and data are not touched)"

configs=()
case "$MODE" in
  current)
    if [[ -f config/install.local.yaml ]]; then
      cp config/install.local.yaml "$SB/install.local.yaml"
    fi
    configs=("$SB/install.local.yaml")
    ;;
  --choose)
    "$PY" -m rag.setup
    configs=("$SB/install.local.yaml")
    ;;
  --everything)
    while IFS= read -r f; do configs+=("$f"); done < <("$PY" -m rag.install_test rounds "$SB/rounds")
    ;;
esac

n=0
for cfg in "${configs[@]}"; do
  n=$((n + 1))
  if [[ ${#configs[@]} -gt 1 ]]; then
    echo ""
    echo "################ round $n of ${#configs[@]} ################"
    cp "$cfg" "$SB/install.local.yaml"
  fi
  echo ""
  scripts/pull-models.sh || true
  echo ""
  if [[ $n -eq 1 ]] || [[ ${#configs[@]} -gt 1 ]]; then
    scripts/fetch-corpus.sh || true
  fi
  services="$("$PY" -c 'from rag.install_config import load_effective_config as e; print(" ".join(e().get("services") or {}))')"
  if [[ -n "$services" ]]; then
    echo ""
    echo "==> Checking service images (test run: not pulling)"
    "$PY" -m rag.service_check $services || true
  fi
done

echo ""
echo "==> Index and search a small sample"
KIWIX_PROJECT=devdocs KIWIX_PREFIX=devdocs_en_angularjs_ OUTPUT_NAME=_probe \
  PERGAMON_ITEM="sample ZIM (for indexing)" scripts/fetchers/kiwix-zim.sh || true
"$PY" -m rag.install_test pipeline || true

set +e
"$PY" -m rag.install_test report
rc=$?
echo "  took $(( $(date +%s) - START ))s · details in $SB/results.tsv"
exit $rc
