#!/usr/bin/env bash
# Build the contract to wasm. Prefers cargo-near; falls back to plain cargo (what the testnet deploy in README used if cargo-near was absent).
set -euo pipefail
cd "$(dirname "$0")"
mkdir -p res
if command -v cargo-near >/dev/null 2>&1; then
  cargo near build non-reproducible-wasm --no-abi --out-dir res
else
  echo "cargo-near not found; using cargo build --target wasm32-unknown-unknown" >&2
  cargo build --target wasm32-unknown-unknown --release
  cp target/wasm32-unknown-unknown/release/nearbooks.wasm res/nearbooks.wasm
fi
ls -l res/nearbooks.wasm
sha256sum res/nearbooks.wasm
