#!/usr/bin/env bash
# Reproducible contract build (NEP-330, cargo-near `build reproducible-wasm`) of one git commit.
#   contract/scripts/repro_build.sh [COMMIT] [OUT]     defaults: deployment.json source_commit, contract/res/nearbooks.wasm
# cargo-near builds the committed tree inside the image pinned by digest in contract/Cargo.toml and embeds the
# repository URL and the commit in the wasm (contract_source_metadata), so the hash is a function of the commit.
# The build therefore runs in a fresh clone checked out at COMMIT: uncommitted changes and later commits in this
# working tree cannot leak in. Needs git, docker and cargo-near (`CARGO_NEAR`, default: cargo-near on PATH).
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
COMMIT=${1:-$(python3 -I -c "import json; print(json.load(open('$ROOT/contract/deployment.json'))['source_commit'])")}
OUT=${2:-$ROOT/contract/res/nearbooks.wasm}
CARGO_NEAR=${CARGO_NEAR:-$(command -v cargo-near || true)}
[ -x "$CARGO_NEAR" ] || { echo "cargo-near not found (set CARGO_NEAR); cargo install cargo-near --version 0.22.0" >&2; exit 2; }
docker info >/dev/null 2>&1 || { echo "docker is not available" >&2; exit 2; }
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
git clone -q "$ROOT" "$TMP/src"
git -C "$TMP/src" checkout -q --detach "$COMMIT"
(cd "$TMP/src/contract" && "$CARGO_NEAR" near build reproducible-wasm --out-dir "$TMP/out") >"$TMP/build.log" 2>&1 || { tail -30 "$TMP/build.log" >&2; exit 1; }
mkdir -p "$(dirname "$OUT")"
cp "$TMP/out/nearbooks.wasm" "$OUT"
grep -o 'CARGO_NEAR_BUILD_ENVIRONMENT=[^)]*' "$TMP/build.log" | head -1
echo "commit $(git -C "$TMP/src" rev-parse HEAD)"
sha256sum "$OUT"
