#!/usr/bin/env bash
# M1 end-to-end on NEAR TESTNET only: deploy nearbooks, pin the OutLayer worker, request the attested snapshot of a
# UTC day (observed at that day's first mainnet block), exercise the rejection paths, backfill the previous day, then
# verify the attestation like a third party would.
# Needs: near-cli-rs (`near` or $NEAR), python3, contract/res/nearbooks.wasm (./build.sh),
#        worker/res/nearbooks-worker.wasm uploaded to FastFS (WASM_URL), and a funded testnet account FUNDER whose
#        key is in $NEAR_HOME/.near-credentials (it pays for the runs: ~0.0025 NEAR each plus ~0.008 NEAR of storage per stored snapshot).
# Prints every transaction id. Usage: FUNDER=you.testnet WASM_URL=https://test.fastfs.io/... [DATE=YYYY-MM-DD] [DCAP=path/to/dcap-qvl] ./testnet_attested.sh
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
DATE=${DATE:-$(date -u +%F)}; PREV=$(date -u -d "$DATE -1 day" +%F)
B=$(python3 -I worker/scripts/first_block.py $DATE); BP=$(python3 -I worker/scripts/first_block.py $PREV)
echo "first mainnet block of $DATE: $B   of $PREV: $BP"
echo "contract: $C   requester: $FUNDER   worker: $WASM_HASH"
[ "$(case $WASM_URL in *$WASM_HASH.wasm) echo ok;; esac)" = ok ] || { echo "WASM_URL does not end in the local build's hash"; exit 1; }

if [ "${SKIP_CREATE:-0}" != 1 ]; then
  echo "-- create the contract account (faucet-sponsored; falls back to funding it from $FUNDER)"
  OUT=$(n account create-account sponsor-by-faucet-service $C autogenerate-new-keypair save-to-legacy-keychain network-config testnet create)
  echo "$OUT" | grep -q "successfully created" || {
    echo "$OUT" | grep -iE "error|429" | head -3
    C=${SUB:-nearbooks-m1}.$FUNDER; echo "faucet unavailable; using sub-account $C"
    n account create-account fund-myself $C '3 NEAR' autogenerate-new-keypair save-to-legacy-keychain sign-as $FUNDER network-config testnet sign-with-legacy-keychain send | tx
  }
  echo "-- deploy + new(owner=$C, reporter=$REPORTER)"
  n contract deploy $C use-file contract/res/nearbooks.wasm with-init-call new json-args "{\"owner_id\":\"$C\",\"reporter_id\":\"$REPORTER\"}" prepaid-gas '30 Tgas' attached-deposit '0 NEAR' network-config testnet sign-with-legacy-keychain send | tx
fi

call() { n contract call-function as-transaction $C "$1" json-args "$2" prepaid-gas "$3" attached-deposit "$4" sign-as "$5" network-config testnet sign-with-legacy-keychain send; }
ret() { awk '/return value/ {f=1; next} f && NF {print; exit}'; }
# contract balance, storage and free balance (balance minus storage staking at 1e19 yocto/byte)
bal() { curl -s https://rpc.testnet.near.org -H 'content-type: application/json' -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"query\",\"params\":{\"request_type\":\"view_account\",\"finality\":\"final\",\"account_id\":\"$C\"}}" | python3 -c "import json,sys; r=json.load(sys.stdin)['result']; a=int(r['amount']); u=r['storage_usage']; print(f'contract balance {a} yocto, storage {u} bytes, free {a-u*10**19} yocto')"; }

echo "-- owner pins the worker (emits worker_pinned)"
OUT=$(call pin_worker "{\"outlayer_id\":\"$OUTLAYER\",\"wasm_url\":\"$WASM_URL\",\"wasm_hash\":\"$WASM_HASH\",\"attestation_api\":\"$API\"}" '30 Tgas' '0 NEAR' $C)
echo "$OUT" | tx; echo "$OUT" | grep -o 'EVENT_JSON:.*worker_pinned[^│]*' | head -1

echo "-- NEGATIVE: a stranger cannot re-pin"
call pin_worker "{\"outlayer_id\":\"$OUTLAYER\",\"wasm_url\":\"$WASM_URL\",\"wasm_hash\":\"$WASM_HASH\",\"attestation_api\":\"$API\"}" '30 Tgas' '0 NEAR' $FUNDER | grep -o 'Smart contract panicked: [^"│]*' | head -1
DEP=0.03   # min_request_deposit: 0.01 NEAR for OutLayer + 0.02 NEAR storage reserve
echo "-- min_request_deposit (yoctoNEAR)"; n contract call-function as-read-only $C min_request_deposit json-args '{}' network-config testnet now | ret
echo "-- NEGATIVE: 0.01 NEAR covers OutLayer but not the storage reserve"
call request_snapshot "{\"date\":\"$DATE\",\"block_height\":$B}" '300 Tgas' '0.01 NEAR' $FUNDER | grep -o 'Smart contract panicked: [^"│]*' | head -1
echo "-- NEGATIVE: a date that has not started"
call request_snapshot "{\"date\":\"$(date -u -d "$DATE +2 day" +%F)\",\"block_height\":$B}" '300 Tgas' "$DEP NEAR" $FUNDER | grep -o 'Smart contract panicked: [^"│]*' | head -1

echo "-- NEGATIVE: a block that is not the first of $DATE ($((B+1))): OutLayer runs the worker, the worker refuses, the contract stores nothing"
OUT=$(call request_snapshot "{\"date\":\"$DATE\",\"block_height\":$((B+1))}" '300 Tgas' "$DEP NEAR" $FUNDER)
echo "$OUT" | tx; echo "$OUT" | grep -o 'Execution failed: [^(]*' | head -1; echo "$OUT" | grep -o 'EVENT_JSON:.*storage_settled[^│]*' | head -1; echo "$OUT" | ret

echo "-- $FUNDER calls request_snapshot($DATE, $B) with $DEP NEAR, 300 Tgas: OutLayer runs the pinned worker in TDX, yield/resume delivers the output"
bal
OUT=$(call request_snapshot "{\"date\":\"$DATE\",\"block_height\":$B}" '300 Tgas' "$DEP NEAR" $FUNDER)
echo "$OUT" | tx; echo "$OUT" | grep -o 'Execution completed successfully[^│]*' | head -1; echo "$OUT" | grep -o 'EVENT_JSON:.*storage_settled[^│]*' | head -1; echo "$OUT" | ret
sleep 3; bal   # free balance must not drop: the requester paid the snapshot's storage

echo "-- NEGATIVE: a second request for $DATE is refused up front (one attested snapshot per date)"
call request_snapshot "{\"date\":\"$DATE\",\"block_height\":$B}" '300 Tgas' "$DEP NEAR" $FUNDER | grep -o 'Smart contract panicked: [^"│]*' | head -1
echo "-- NEGATIVE: nobody can call the callback directly"
call on_snapshot_result '{"nonce":1}' '30 Tgas' '0 NEAR' $FUNDER | grep -o 'Smart contract panicked: [^"│]*' | head -1

echo "-- NEGATIVE: $PREV is before earliest_date (default: deployment date), so backfill is bounded"
call request_snapshot "{\"date\":\"$PREV\",\"block_height\":$BP}" '300 Tgas' "$DEP NEAR" $FUNDER | grep -o 'Smart contract panicked: [^"│]*' | head -1
echo "-- owner moves earliest_date to $PREV (event earliest_date_set)"
OUT=$(call set_earliest_date "{\"date\":\"$PREV\"}" '30 Tgas' '0 NEAR' $C); echo "$OUT" | tx
echo "-- NEGATIVE: earliest_date can never move forward again (it could otherwise shut out a prediction's date)"
call set_earliest_date "{\"date\":\"$DATE\"}" '30 Tgas' '0 NEAR' $C | grep -o 'Smart contract panicked: [^"│]*' | head -1
echo "-- backfill the previous day $PREV (archival read at block $BP; dates may arrive in any order)"
OUT=$(call request_snapshot "{\"date\":\"$PREV\",\"block_height\":$BP}" '300 Tgas' "$DEP NEAR" $FUNDER)
echo "$OUT" | tx; echo "$OUT" | grep -o 'Execution completed successfully[^│]*' | head -1; echo "$OUT" | ret

echo "-- views"
n contract call-function as-read-only $C attested_count json-args '{}' network-config testnet now | ret
n contract call-function as-read-only $C get_attested_snapshot json-args "{\"date\":\"$DATE\"}" network-config testnet now | sed -n '/return value/,/^Here is/p' | grep -vE '^\s*$|^Here is' | head -60

echo "-- third-party verification of both snapshots"
python3 -I contract/scripts/verify_attestation.py $C $DATE --wasm worker/res/nearbooks-worker.wasm ${DCAP:+--dcap $DCAP}
python3 -I contract/scripts/verify_attestation.py $C $PREV ${DCAP:+--dcap $DCAP}
