#!/usr/bin/env bash
# Build the OutLayer worker for wasm32-wasip2 and print its sha256 (the hash the contract pins).
# Paths are remapped so the hash does not depend on where the repo or the cargo registry live.
set -euo pipefail
cd "$(dirname "$0")"
export RUSTFLAGS="--remap-path-prefix=$PWD=/nearbooks/worker --remap-path-prefix=${CARGO_HOME:-$HOME/.cargo}=/cargo"
cargo build --release --locked --target wasm32-wasip2
mkdir -p res
cp target/wasm32-wasip2/release/nearbooks-worker.wasm res/nearbooks-worker.wasm
rustc -V
ls -l res/nearbooks-worker.wasm
sha256sum res/nearbooks-worker.wasm
