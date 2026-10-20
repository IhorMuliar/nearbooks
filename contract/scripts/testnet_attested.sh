#!/usr/bin/env bash
# End-to-end on NEAR TESTNET only: deploy nearbooks, pin the OutLayer worker, register the predictions, request the
# attested snapshot of a UTC day (observed at that day's first mainnet block and the first block 28 days earlier),
# exercise rejection paths and the re-pin timelock, then verify the attestation like a third party would.
# Needs: near-cli-rs (`near` or $NEAR), python3, contract/res/nearbooks.wasm (make wasm),
#        worker/res/nearbooks-worker.wasm uploaded to FastFS (WASM_URL), and a funded testnet account FUNDER whose
#        key is in $NEAR_HOME/.near-credentials (it pays: ~2.7 NEAR for the contract account, then ~0.0025 NEAR per run
#        plus ~0.011 NEAR of storage per stored snapshot).
# Prints every transaction id. Usage:
#   FUNDER=you.testnet WASM_URL=https://test.fastfs.io/... [SUB=nearbooks-v5] [DATE=YYYY-MM-DD] [PIN_DELAY=3600] \
#   [DEMO_DATE=YYYY-MM-DD] [DCAP=path/to/dcap-qvl] ./testnet_attested.sh
set -uo pipefail
cd "$(dirname "$0")/../.."
NEAR=${NEAR:-near}
export NEAR_HOME=${NEAR_HOME:-$PWD/.tools/home}; mkdir -p "$NEAR_HOME"
n() { HOME="$NEAR_HOME" "$NEAR" "$@" 2>&1; }
tx() { awk '/Transaction ID/ && !f {print $NF; f=1}'; }
panic() { grep -o 'Smart contract panicked: [^"│]*' | head -1; }
FUNDER=${FUNDER:?set FUNDER to a funded testnet account}
WASM_URL=${WASM_URL:?set WASM_URL to the FastFS URL printed by outlayer upload}
WASM_HASH=$(sha256sum worker/res/nearbooks-worker.wasm | cut -d' ' -f1)
OUTLAYER=${OUTLAYER:-outlayer.testnet}
API=${API:-https://testnet-api.outlayer.ai}
REPORTER=${REPORTER:-nbproto-rep-1696.testnet}
PIN_DELAY=${PIN_DELAY:-3600}
C=${CONTRACT:-${SUB:-nearbooks-v5}.$FUNDER}
DATE=${DATE:-$(date -u +%F)}
DEMO_DATE=${DEMO_DATE:-$(date -u -d "$DATE +1 day" +%F)}
read -r B B28 <<<"$(python3 -I worker/scripts/first_block.py $DATE --pair)"
echo "first mainnet block of $DATE: $B   of $DATE minus 28 days: $B28"
echo "contract: $C   requester: $FUNDER   worker: $WASM_HASH   pin delay: ${PIN_DELAY}s"
[ "$(case $WASM_URL in *$WASM_HASH.wasm) echo ok;; esac)" = ok ] || { echo "WASM_URL does not end in the local build's hash"; exit 1; }
echo "contract wasm sha256: $(sha256sum contract/res/nearbooks.wasm | cut -d' ' -f1)"

call() { n contract call-function as-transaction $C "$1" json-args "$2" prepaid-gas "$3" attached-deposit "$4" sign-as "$5" network-config testnet sign-with-legacy-keychain send; }
view() { n contract call-function as-read-only $C "$1" json-args "$2" network-config testnet now | awk '/return value/ {f=1; next} f && /^Here is/ {exit} f && NF {print}'; }
ret() { awk '/return value/ {f=1; next} f && NF {print; exit}'; }
ev() { grep -o "EVENT_JSON:[^│]*\"event\":\"$1\"[^│]*" | head -1 | sed 's/[[:space:]]*$//'; }
bal() { curl -s https://rpc.testnet.near.org -H 'content-type: application/json' -d "{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"query\",\"params\":{\"request_type\":\"view_account\",\"finality\":\"final\",\"account_id\":\"$C\"}}" | python3 -c "import json,sys; r=json.load(sys.stdin)['result']; a=int(r['amount']); u=r['storage_usage']; print(f'contract balance {a} yocto, storage {u} bytes, free {a-u*10**19} yocto, code_hash {r[\"code_hash\"]}')"; }

if [ "${SKIP_CREATE:-0}" != 1 ]; then
  echo "-- create $C (funded by $FUNDER: the code needs ~2.6 NEAR of storage)"
  n account create-account fund-myself $C '2.75 NEAR' autogenerate-new-keypair save-to-legacy-keychain sign-as $FUNDER network-config testnet sign-with-legacy-keychain send | tx
  echo "-- deploy + new(owner=$C, reporter=$REPORTER, pin_delay_secs=$PIN_DELAY)"
  n contract deploy $C use-file contract/res/nearbooks.wasm with-init-call new json-args "{\"owner_id\":\"$C\",\"reporter_id\":\"$REPORTER\",\"pin_delay_secs\":$PIN_DELAY}" prepaid-gas '30 Tgas' attached-deposit '0 NEAR' network-config testnet sign-with-legacy-keychain send | tx
  sleep 2; bal   # code_hash is base58 of the sha256 above
fi
PIN="{\"outlayer_id\":\"$OUTLAYER\",\"wasm_url\":\"$WASM_URL\",\"wasm_hash\":\"$WASM_HASH\",\"attestation_api\":\"$API\"}"
echo "-- owner pins the worker: the first pin applies at once (event worker_pinned)"
OUT=$(call propose_pin "$PIN" '30 Tgas' '0 NEAR' $C); echo "$OUT" | tx; echo "$OUT" | ev worker_pinned
echo "-- NEGATIVE: a stranger cannot pin"
call propose_pin "$PIN" '30 Tgas' '0 NEAR' $FUNDER | panic

echo "-- owner registers P1..P4 (pipeline/predictions.json via make_args.py) and the demo predictions resolving on $DEMO_DATE"
M=data/snapshot-2026-10-20/metrics.json
for i in $(seq 0 $(( $(python3 -I contract/scripts/make_args.py count $M) - 1 ))); do
  OUT=$(call add_prediction "$(python3 -I contract/scripts/make_args.py prediction $M $i)" '30 Tgas' '0 NEAR' $C); echo "P$((i+1)) $(echo "$OUT" | tx) id $(echo "$OUT" | ret)"
done
for i in $(seq 0 $(( $(python3 -I contract/scripts/make_args.py demo-count) - 1 ))); do
  OUT=$(call add_prediction "$(python3 -I contract/scripts/make_args.py demo $i $DEMO_DATE)" '30 Tgas' '0 NEAR' $C); echo "demo $i $(echo "$OUT" | tx) id $(echo "$OUT" | ret)"
done
echo "-- NEGATIVE: a prediction whose answer is already knowable (resolve_after = today)"
call add_prediction "{\"claim\":\"x\",\"metric\":\"supply_total_near\",\"comparator\":\"gt\",\"threshold\":\"0\",\"resolve_after\":\"$(date -u +%F)\"}" '30 Tgas' '0 NEAR' $C | panic

DEP=0.03   # min_request_deposit: 0.01 NEAR for OutLayer + 0.02 NEAR storage reserve
echo "-- NEGATIVE: 0.01 NEAR covers OutLayer but not the storage reserve"
call request_snapshot "{\"date\":\"$DATE\",\"block_height\":$B,\"block_height_28d\":$B28}" '300 Tgas' '0.01 NEAR' $FUNDER | panic
echo "-- NEGATIVE: a 28-day block that is not the first of its day ($((B28+1))): the worker refuses, nothing stored, full reserve refunded"
OUT=$(call request_snapshot "{\"date\":\"$DATE\",\"block_height\":$B,\"block_height_28d\":$((B28+1))}" '300 Tgas' "$DEP NEAR" $FUNDER)
echo "$OUT" | tx; echo "$OUT" | grep -o 'Execution failed: [^(]*' | head -1; echo "$OUT" | ev storage_settled; echo "$OUT" | ret

echo "-- $FUNDER calls request_snapshot($DATE, $B, $B28) with $DEP NEAR, 300 Tgas"
bal
OUT=$(call request_snapshot "{\"date\":\"$DATE\",\"block_height\":$B,\"block_height_28d\":$B28}" '300 Tgas' "$DEP NEAR" $FUNDER)
echo "$OUT" | tx; echo "$OUT" | grep -o 'Execution completed successfully[^│]*' | head -1 | sed 's/[[:space:]]*$//'; echo "$OUT" | ev storage_settled; echo "$OUT" | ret
sleep 3; bal   # free balance must not drop: the requester paid the snapshot's storage
echo "-- NEGATIVE: a second request for $DATE is refused up front (one attested snapshot per date)"
call request_snapshot "{\"date\":\"$DATE\",\"block_height\":$B,\"block_height_28d\":$B28}" '300 Tgas' "$DEP NEAR" $FUNDER | panic

echo "-- re-pin timelock: propose (event pin_proposed), an immediate apply is refused, then cancel (event pin_proposal_cancelled)"
REPIN="{\"outlayer_id\":\"$OUTLAYER\",\"wasm_url\":\"$WASM_URL\",\"wasm_hash\":\"$WASM_HASH\",\"attestation_api\":\"$API\",\"max_execution_seconds\":90}"
OUT=$(call propose_pin "$REPIN" '30 Tgas' '0 NEAR' $C); echo "$OUT" | tx; echo "$OUT" | ev pin_proposed
call apply_pin '{}' '30 Tgas' '0 NEAR' $C | panic
OUT=$(call cancel_pin_proposal '{}' '30 Tgas' '0 NEAR' $C); echo "$OUT" | tx; echo "$OUT" | ev pin_proposal_cancelled

echo "-- views"
echo "attested_count: $(view attested_count '{}')   pin_delay_secs: $(view get_pin_delay_secs '{}')   predictions: $(view list_predictions '{}' | grep -c '"id"')"
view get_attested_snapshot "{\"date\":\"$DATE\"}" | head -80

echo "-- third-party verification"
python3 -I contract/scripts/verify_attestation.py $C $DATE --wasm worker/res/nearbooks-worker.wasm ${DCAP:+--dcap $DCAP}
