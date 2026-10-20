#!/usr/bin/env bash
# M1 end-to-end on NEAR TESTNET only: deploy nearbooks, pin the OutLayer worker, request an attested snapshot,
# exercise the rejection paths, then verify the attestation like a third party would.
# Needs: near-cli-rs (`near` or $NEAR), python3, contract/res/nearbooks.wasm (./build.sh),
#        worker/res/nearbooks-worker.wasm uploaded to FastFS (WASM_URL), and a funded testnet account FUNDER whose
#        key is in $NEAR_HOME/.near-credentials (it pays for the runs: ~0.0015 NEAR each).
# Prints every transaction id. Usage: FUNDER=you.testnet WASM_URL=https://test.fastfs.io/... ./testnet_attested.sh
set -uo pipefail
cd "$(dirname "$0")/../.."
NEAR=${NEAR:-near}
export NEAR_HOME=${NEAR_HOME:-$PWD/.tools/home}; mkdir -p "$NEAR_HOME"
n() { HOME="$NEAR_HOME" "$NEAR" "$@" 2>&1; }
tx() { awk '/Transaction ID/ && !f {print $NF; f=1}'; }
FUNDER=${FUNDER:?set FUNDER to a funded testnet account}
WASM_URL=${WASM_URL:?set WASM_URL to the FastFS URL printed by outlayer upload}
WASM_HASH=$(sha256sum worker/res/nearbooks-worker.wasm | cut -d' ' -f1)
OUTLAYER=${OUTLAYER:-outlayer.testnet}
API=${API:-https://testnet-api.outlayer.ai}
REPORTER=${REPORTER:-nbproto-rep-1696.testnet}
C=${CONTRACT:-nearbooks-m1-$RANDOM.testnet}
echo "contract: $C   requester: $FUNDER   worker: $WASM_HASH"
[ "$(case $WASM_URL in *$WASM_HASH.wasm) echo ok;; esac)" = ok ] || { echo "WASM_URL does not end in the local build's hash"; exit 1; }

if [ "${SKIP_CREATE:-0}" != 1 ]; then
  echo "-- create the contract account (faucet-sponsored; falls back to funding it from $FUNDER)"
  OUT=$(n account create-account sponsor-by-faucet-service $C autogenerate-new-keypair save-to-legacy-keychain network-config testnet create)
  echo "$OUT" | grep -q "successfully created" || {
    echo "$OUT" | grep -iE "error|429" | head -3
    C=nearbooks-m1.$FUNDER; echo "faucet unavailable; using sub-account $C"
    n account create-account fund-myself $C '3 NEAR' autogenerate-new-keypair save-to-legacy-keychain sign-as $FUNDER network-config testnet sign-with-legacy-keychain send | tx
  }
  echo "-- deploy + new(owner=$C, reporter=$REPORTER)"
  n contract deploy $C use-file contract/res/nearbooks.wasm with-init-call new json-args "{\"owner_id\":\"$C\",\"reporter_id\":\"$REPORTER\"}" prepaid-gas '30 Tgas' attached-deposit '0 NEAR' network-config testnet sign-with-legacy-keychain send | tx
fi

call() { n contract call-function as-transaction $C "$1" json-args "$2" prepaid-gas "$3" attached-deposit "$4" sign-as "$5" network-config testnet sign-with-legacy-keychain send; }
ret() { awk '/return value/ {f=1; next} f && NF {print; exit}'; }

echo "-- owner pins the worker (emits worker_pinned)"
OUT=$(call pin_worker "{\"outlayer_id\":\"$OUTLAYER\",\"wasm_url\":\"$WASM_URL\",\"wasm_hash\":\"$WASM_HASH\",\"attestation_api\":\"$API\"}" '30 Tgas' '0 NEAR' $C)
echo "$OUT" | tx; echo "$OUT" | grep -o 'EVENT_JSON:.*worker_pinned[^│]*' | head -1

echo "-- NEGATIVE: a stranger cannot re-pin"
call pin_worker "{\"outlayer_id\":\"$OUTLAYER\",\"wasm_url\":\"$WASM_URL\",\"wasm_hash\":\"$WASM_HASH\",\"attestation_api\":\"$API\"}" '30 Tgas' '0 NEAR' $FUNDER | grep -o 'Smart contract panicked: [^"│]*' | head -1
echo "-- NEGATIVE: request_snapshot without a deposit"
call request_snapshot '{}' '300 Tgas' '0 NEAR' $FUNDER | grep -o 'Smart contract panicked: [^"│]*' | head -1

echo "-- $FUNDER calls request_snapshot (0.01 NEAR, 300 Tgas): OutLayer runs the pinned worker in TDX, yield/resume delivers the output"
OUT=$(call request_snapshot '{}' '300 Tgas' '0.01 NEAR' $FUNDER)
echo "$OUT" | tx; echo "$OUT" | grep -o 'Execution completed successfully[^│]*' | head -1; echo "$OUT" | ret

echo "-- NEGATIVE: a second run the same day is executed by OutLayer but rejected by the contract (date order), nothing stored"
OUT=$(call request_snapshot '{}' '300 Tgas' '0.01 NEAR' $FUNDER)
echo "$OUT" | tx; echo "$OUT" | ret
echo "-- NEGATIVE: nobody can call the callback directly"
call on_snapshot_result '{"nonce":1}' '30 Tgas' '0 NEAR' $FUNDER | grep -o 'Smart contract panicked: [^"│]*' | head -1

echo "-- views"
n contract call-function as-read-only $C snapshot_count json-args '{}' network-config testnet now | ret
n contract call-function as-read-only $C latest_snapshot json-args '{}' network-config testnet now | sed -n '/return value/,$p' | grep -vE '^\s*$' | head -60

echo "-- third-party verification"
python3 -I contract/scripts/verify_attestation.py $C --wasm worker/res/nearbooks-worker.wasm
