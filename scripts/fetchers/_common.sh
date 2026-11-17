#!/usr/bin/env bash
# Shared helpers for fetcher scripts. Source this at the top of each fetcher.
# Not intended to be executed directly.

set -euo pipefail

# Resolve paths relative to repo root.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# An install test run (scripts/test-install.sh) points this at a throwaway folder.
BUILD_DIR="${PERGAMON_SANDBOX:-$REPO_ROOT/build}"
CORPUS_DIR="$BUILD_DIR/corpus"
ZIM_DIR="$CORPUS_DIR/zims"
PDF_DIR="$CORPUS_DIR/pdfs"
BOOK_DIR="$CORPUS_DIR/books"
MIRROR_DIR="$CORPUS_DIR/mirrors"
MAP_DIR="$CORPUS_DIR/maps"

mkdir -p "$ZIM_DIR" "$PDF_DIR" "$BOOK_DIR" "$MIRROR_DIR" "$MAP_DIR"

log()  { printf "  %s\n" "$*"; }
ok()   { printf "  [\033[32m ok \033[0m] %s\n" "$*"; }
err()  { printf "  [\033[31merr \033[0m] %s\n" "$*" >&2; }
warn() { printf "  [\033[33mwarn\033[0m] %s\n" "$*"; }

# ── install test runs ──────────────────────────────────────────────────────
# With PERGAMON_TEST_INSTALL=1, fetch_url downloads only the first TEST_BYTES
# of each file, checks it is the kind of file it should be, and records the
# outcome in $PERGAMON_SANDBOX/results.tsv for the end-of-run report.
TEST_MODE="${PERGAMON_TEST_INSTALL:-0}"
TEST_BYTES="${TEST_BYTES:-2097152}"

# record <PASS|FAIL|SKIP> <step> <item> <detail>
record() {
  [[ -n "${PERGAMON_SANDBOX:-}" ]] || return 0
  printf '%s\t%s\t%s\t%s\n' "$1" "$2" "$3" "${4//$'\t'/ }" >> "$PERGAMON_SANDBOX/results.tsv"
}

# Does the start of <file> look like the file type its name says?
looks_right() {
  local f="$1" head
  head="$(head -c 8 "$f" | xxd -p)"
  case "$f" in
    *.zim)  [[ "$head" == 5a494d04* ]] ;;
    *.pdf)  [[ "$head" == 25504446* ]] ;;
    *.7z)   [[ "$head" == 377abcaf271c* ]] ;;
    *.zip)  [[ "$head" == 504b0304* ]] ;;
    *.sqlitedb) [[ "$head" == 53514c6974652066* ]] ;;  # "SQLite f"
    *)      ! head -c 2048 "$f" | grep -qi '<html' ;;
  esac
}

human_bytes() {
  awk -v b="${1:-0}" 'BEGIN { split("B KB MB GB TB", u); i = 1;
    while (b >= 1024 && i < 5) { b /= 1024; i++ } printf (i == 1 ? "%d %s" : "%.1f %s"), b, u[i] }'
}

# fetch_sample <url> <out>: the first TEST_BYTES, checked. Records unless FETCH_RECORD=0.
fetch_sample() {
  local url="$1" out="$2" item="${PERGAMON_ITEM:-$(basename "$2")}" rc total status hdrs
  hdrs="$(curl -sIL --max-time 20 "$url" | tr -d '\r')"
  total="$(awk 'tolower($1) == "content-length:" {print $2}' <<< "$hdrs" | tail -1)"
  status="$(awk '/^HTTP\// {print $2}' <<< "$hdrs" | tail -1)"
  # Ask for just the first TEST_BYTES; head -c covers servers that ignore ranges.
  set +e
  curl -sL --fail --max-time 120 -r "0-$((TEST_BYTES - 1))" "$url" | head -c "$TEST_BYTES" > "$out"
  rc=${PIPESTATUS[0]}
  set -e
  local got
  got="$(wc -c < "$out" | tr -d ' ')"
  # Cutting the download short makes curl exit non-zero (23, 56, 141); that's
  # fine when we got the bytes we asked for, or the whole (small) file.
  if [[ "$got" -ge "$TEST_BYTES" || ( -n "$total" && "$got" -ge "$total" ) ]]; then rc=0; fi
  if [[ $rc -ne 0 ]] || [[ ! -s "$out" ]]; then
    err "sample download failed (curl exit $rc): $url"
    [[ "${FETCH_RECORD:-1}" == 1 ]] && record FAIL download "$item" "HTTP ${status:-?} (curl exit $rc) · $url"
    rm -f "$out"
    return 1
  fi
  local size_note
  size_note="sampled $(human_bytes "$got")"
  [[ -n "$total" ]] && size_note+=" of $(human_bytes "$total")"
  if ! looks_right "$out"; then
    err "$(basename "$out") is not the expected file type (got $(head -c 16 "$out" | xxd -p))"
    [[ "${FETCH_RECORD:-1}" == 1 ]] && record FAIL download "$item" "wrong file type · $url"
    return 1
  fi
  ok "$size_note — $(basename "$out") looks right"
  [[ "${FETCH_RECORD:-1}" == 1 ]] && record PASS download "$item" "$size_note · $url"
  return 0
}

# Idempotent curl. Resumes partial files. --max-time caps total transfer.
# Usage: fetch_url <url> <output-path> [max-time-seconds]
fetch_url() {
  local url="$1" out="$2" max="${3:-1800}"
  if [[ "$TEST_MODE" == 1 ]]; then
    fetch_sample "$url" "$out"
    return
  fi
  if [[ -s "$out" ]]; then
    # If we already have the file at expected size, don't refetch.
    local have have_remote
    have="$(stat -f%z "$out" 2>/dev/null || stat -c%s "$out" 2>/dev/null || echo 0)"
    have_remote="$(curl -sIL --max-time 15 "$url" | awk '/^[Cc]ontent-[Ll]ength:/ {print $2}' | tr -d '\r' | tail -1)"
    if [[ -n "$have_remote" && "$have" == "$have_remote" ]]; then
      ok "already have $(basename "$out") ($(du -h "$out" | cut -f1))"
      return 0
    fi
  fi
  log "fetching $(basename "$out")"
  if curl -L --fail --retry 5 --retry-delay 10 --retry-all-errors \
          --max-time "$max" --continue-at - -o "$out" "$url"; then
    ok "done $(basename "$out")"
  else
    err "download failed; partial file kept — re-run to resume"
    return 1
  fi
}

# Python used by fetcher scripts (venv preferred for YAML parsing + 3.11 syntax).
if [[ -x "$REPO_ROOT/build/venv/bin/python" ]]; then
  PY="$REPO_ROOT/build/venv/bin/python"
else
  PY="python3"
fi
export PY
