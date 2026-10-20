#!/usr/bin/env bash
# The keeper-budget contract end to end on NEAR TESTNET only: deploy the reproducible build to a new account, pin the
# worker, register the predictions, create the keeper account with nothing but a function-call key (allowance 0.5 NEAR,
# receiver = the contract, methods keeper_request_snapshot + resolve_prediction), name it keeper with a daily cap,
# prepay the budget, show what the key cannot do, then let keeper/keeper.py request the attested snapshot of DATE.
# Needs: near-cli-rs (`near` or $NEAR), python3, the reproducible build (WASM, default contract/res/nearbooks.wasm),
#        worker/res/nearbooks-worker.wasm (for the pin hash) and a funded FUNDER key in $NEAR_HOME/.near-credentials.
# Usage: FUNDER=you.testnet WASM_URL=https://test.fastfs.io/... [SUB=nearbooks-v6] [KEEPER_SUB=nearbooks-keeper]
#        [DATE=YYYY-MM-DD] [DEMO_DATE=YYYY-MM-DD] [CAP=0.06] [BUDGET=0.15] ./testnet_keeper.sh
set -uo pipefail
cd "$(dirname "$0")/../.."
NEAR=${NEAR:-near}
export NEAR_HOME=${NEAR_HOME:-$PWD/.tools/home}; mkdir -p "$NEAR_HOME"
n() { HOME="$NEAR_HOME" "$NEAR" "$@" 2>&1; }
tx() { awk '/Transaction ID/ && !f {print $NF; f=1}'; }
panic() { grep -o 'Smart contract panicked: [^"│]*' | head -1; }
ev() { grep -o "EVENT_JSON:[^│]*\"event\":\"$1\"[^│]*" | head -1 | sed 's/[[:space:]]*$//'; }
FUNDER=${FUNDER:?set FUNDER to a funded testnet account}
WASM_URL=${WASM_URL:?set WASM_URL to the worker FastFS URL}
WASM=${WASM:-contract/res/nearbooks.wasm}
WORKER_HASH=$(sha256sum worker/res/nearbooks-worker.wasm | cut -d' ' -f1)
C=${CONTRACT:-${SUB:-nearbooks-v6}.$FUNDER}
K=${KEEPER:-${KEEPER_SUB:-nearbooks-keeper}.$FUNDER}
DATE=${DATE:-$(date -u +%F)}
DEMO_DATE=${DEMO_DATE:-$(date -u -d "$DATE +1 day" +%F)}
CAP=${CAP:-0.06}; BUDGET=${BUDGET:-0.15}
KEYFILE=$NEAR_HOME/keeper-fc-key.json
call() { n contract call-function as-transaction $C "$1" json-args "$2" prepaid-gas "$3" attached-deposit "$4" sign-as "$5" network-config testnet sign-with-legacy-keychain send; }
view() { n contract call-function as-read-only $C "$1" json-args "$2" network-config testnet now | awk '/return value/ {f=1; next} f && /^Here is/ {exit} f && NF {print}'; }
echo "contract $C (code sha256 $(sha256sum $WASM | cut -d' ' -f1))   keeper $K   worker $WORKER_HASH   date $DATE   demo date $DEMO_DATE"

echo "-- create $C and deploy + new(owner=$C, reporter=nbproto-rep-1696.testnet, pin_delay_secs=3600)"
n account create-account fund-myself $C '2.9 NEAR' autogenerate-new-keypair save-to-legacy-keychain sign-as $FUNDER network-config testnet sign-with-legacy-keychain send | tx
n contract deploy $C use-file $WASM with-init-call new json-args "{\"owner_id\":\"$C\",\"reporter_id\":\"nbproto-rep-1696.testnet\",\"pin_delay_secs\":3600}" prepaid-gas '30 Tgas' attached-deposit '0 NEAR' network-config testnet sign-with-legacy-keychain send | tx
PIN="{\"outlayer_id\":\"outlayer.testnet\",\"wasm_url\":\"$WASM_URL\",\"wasm_hash\":\"$WORKER_HASH\",\"attestation_api\":\"https://testnet-api.outlayer.ai\"}"
echo "-- first pin (applies at once)"
OUT=$(call propose_pin "$PIN" '30 Tgas' '0 NEAR' $C); echo "$OUT" | tx; echo "$OUT" | ev worker_pinned
echo "-- predictions P1..P4 and the demos resolving on $DEMO_DATE"
M=data/snapshot-2026-10-20/metrics.json
for i in $(seq 0 $(( $(python3 -I contract/scripts/make_args.py count $M) - 1 ))); do
  echo "P$((i+1)) $(call add_prediction "$(python3 -I contract/scripts/make_args.py prediction $M $i)" '30 Tgas' '0 NEAR' $C | tx)"
done
for i in $(seq 0 $(( $(python3 -I contract/scripts/make_args.py demo-count) - 1 ))); do
  echo "demo $i $(call add_prediction "$(python3 -I contract/scripts/make_args.py demo $i $DEMO_DATE)" '30 Tgas' '0 NEAR' $C | tx)"
done

echo "-- create $K (1 NEAR); its full-access key stays in the local keychain, CI only ever gets the function-call key"
n account create-account fund-myself $K '1 NEAR' autogenerate-new-keypair save-to-legacy-keychain sign-as $FUNDER network-config testnet sign-with-legacy-keychain send | tx
PUB=$(python3 -I keeper/keygen.py "$K" "$KEYFILE")
echo "-- add the function-call key $PUB: allowance 0.5 NEAR, receiver $C, methods keeper_request_snapshot, resolve_prediction"
n account add-key $K grant-function-call-access --allowance '0.5 NEAR' --contract-account-id $C --function-names 'keeper_request_snapshot, resolve_prediction' use-manually-provided-public-key "$PUB" network-config testnet sign-with-legacy-keychain send | tx

echo "-- owner names the keeper, daily cap $CAP NEAR (event keeper_set); $FUNDER prepays $BUDGET NEAR (event keeper_funded)"
OUT=$(call set_keeper "{\"keeper_id\":\"$K\",\"daily_cap\":\"$(python3 -c "print(int(round($CAP*10**24)))")\"}" '30 Tgas' '0 NEAR' $C); echo "$OUT" | tx; echo "$OUT" | ev keeper_set
OUT=$(call fund_keeper '{}' '30 Tgas' "$BUDGET NEAR" $FUNDER); echo "$OUT" | tx; echo "$OUT" | ev keeper_funded
echo "get_keeper: $(view get_keeper '{}' | tr -d ' \n')"

echo "-- NEGATIVE: what the keeper key cannot do (rejected before execution, no fee)"
python3 -I keeper/probe.py "$C" "$K" "$KEYFILE"
echo "-- NEGATIVE: the funder (not the keeper) cannot spend the budget"
call keeper_request_snapshot "{\"date\":\"$DATE\",\"block_height\":1,\"block_height_28d\":0}" '300 Tgas' '0 NEAR' $FUNDER | panic

echo "-- the keeper requests the attested snapshot of $DATE through the budget"
python3 -I keeper/keeper.py --contract $C --account $K --key-file "$KEYFILE" --date $DATE
echo "get_keeper: $(view get_keeper '{}' | tr -d ' \n')"
echo "-- second run: idempotent (the snapshot exists, nothing is signed)"
python3 -I keeper/keeper.py --contract $C --account $K --key-file "$KEYFILE" --date $DATE
echo "-- third-party verification"
python3 -I contract/scripts/verify_attestation.py $C $DATE --wasm worker/res/nearbooks-worker.wasm ${DCAP:+--dcap $DCAP}
