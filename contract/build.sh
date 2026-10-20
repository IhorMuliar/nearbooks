#!/usr/bin/env bash
# Quick NON-reproducible build of the working tree (`make wasm-local`) into res/nearbooks-local.wasm. Its hash depends on
# the local toolchain; the deployed code is the reproducible build (`make wasm`, scripts/repro_build.sh).
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p res
if command -v cargo-near >/dev/null 2>&1; then
  cargo near build non-reproducible-wasm --no-abi --out-dir res/local && mv res/local/nearbooks.wasm res/nearbooks-local.wasm && rmdir res/local
else
  echo "cargo-near not found; using cargo build --target wasm32-unknown-unknown" >&2
  cargo build --target wasm32-unknown-unknown --release
  cp target/wasm32-unknown-unknown/release/nearbooks.wasm res/nearbooks-local.wasm
fi
ls -l res/nearbooks-local.wasm
sha256sum res/nearbooks-local.wasm
