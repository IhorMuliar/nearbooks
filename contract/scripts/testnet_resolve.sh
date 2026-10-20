#!/usr/bin/env bash
# Prediction resolution and the re-pin timelock end to end on NEAR TESTNET (after testnet_attested.sh).
#   PHASE=propose  (before DEMO_DATE starts) owner proposes a re-pin that only changes the time limit (60 s -> 90 s).
#   PHASE=resolve  (after DEMO_DATE 00:00 UTC and after the timelock) owner applies it; DEMO_DATE started before the
#                  apply, so its snapshot must still use the old pin entry; anyone requests the attested snapshot of
#                  DEMO_DATE and resolves every prediction that waits for it; a prediction for a later date stays Pending.
# Usage: PHASE=propose|resolve FUNDER=you.testnet CONTRACT=nearbooks-v5.you.testnet DEMO_DATE=YYYY-MM-DD [DCAP=...] ./testnet_resolve.sh
set -uo pipefail
cd "$(dirname "$0")/../.."
NEAR=${NEAR:-near}
export NEAR_HOME=${NEAR_HOME:-$PWD/.tools/home}
n() { HOME="$NEAR_HOME" "$NEAR" "$@" 2>&1; }
tx() { awk '/Transaction ID/ && !f {print $NF; f=1}'; }
panic() { grep -o 'Smart contract panicked: [^"│]*' | head -1; }
ev() { grep -o "EVENT_JSON:[^│]*\"event\":\"$1\"[^│]*" | head -1 | sed 's/[[:space:]]*$//'; }
FUNDER=${FUNDER:?}; C=${CONTRACT:?}; DEMO_DATE=${DEMO_DATE:?}
call() { n contract call-function as-transaction $C "$1" json-args "$2" prepaid-gas "$3" attached-deposit "$4" sign-as "$5" network-config testnet sign-with-legacy-keychain send; }
view() { n contract call-function as-read-only $C "$1" json-args "$2" network-config testnet now | awk '/return value/ {f=1; next} f && /^Here is/ {exit} f && NF {print}'; }
compact() { python3 -c "import json,sys; print(json.dumps(json.loads(sys.stdin.read()), separators=(',', ':')))"; }
CFG=$(view get_outlayer_config '{}' | compact)
field() { echo "$CFG" | python3 -c "import json,sys; print(json.load(sys.stdin)['$1'])"; }
echo "now $(date -u +%FT%TZ); contract $C; current pin: $CFG"

if [ "${PHASE:?}" = propose ]; then
  REPIN="{\"outlayer_id\":\"$(field outlayer_id)\",\"wasm_url\":\"$(field wasm_url)\",\"wasm_hash\":\"$(field wasm_hash)\",\"attestation_api\":\"$(field attestation_api)\",\"max_execution_seconds\":90}"
  echo "-- owner proposes a re-pin (time limit 90 s); it cannot be applied for pin_delay_secs = $(view get_pin_delay_secs '{}')"
  OUT=$(call propose_pin "$REPIN" '30 Tgas' '0 NEAR' $C); echo "$OUT" | tx; echo "$OUT" | ev pin_proposed
  echo "-- NEGATIVE: applying it now is refused"
  call apply_pin '{}' '30 Tgas' '0 NEAR' $C | panic
  view get_pin_proposal '{}' | compact
  exit 0
fi

echo "-- owner applies the re-pin (event worker_pinned, from_ns = this block's time, which is after $DEMO_DATE 00:00 UTC)"
OUT=$(call apply_pin '{}' '30 Tgas' '0 NEAR' $C); echo "$OUT" | tx; echo "$OUT" | ev worker_pinned
LATER=$(date -u -d "$DEMO_DATE +1 day" +%F)
echo "pin for $DEMO_DATE: $(view get_pin_for_date "{\"date\":\"$DEMO_DATE\"}" | compact)"
echo "pin for $LATER: $(view get_pin_for_date "{\"date\":\"$LATER\"}" | compact)"

read -r B B28 <<<"$(python3 -I worker/scripts/first_block.py $DEMO_DATE --pair)"
echo "-- $FUNDER requests the attested snapshot of $DEMO_DATE (blocks $B, $B28); it must run under the old entry (60 s)"
OUT=$(call request_snapshot "{\"date\":\"$DEMO_DATE\",\"block_height\":$B,\"block_height_28d\":$B28}" '300 Tgas' '0.03 NEAR' $FUNDER)
TX=$(echo "$OUT" | tx); echo "$TX"; echo "$OUT" | grep -o 'Execution completed successfully[^│]*' | head -1 | sed 's/[[:space:]]*$//'
echo "$OUT" | ev storage_settled; echo "$OUT" | awk '/return value/ {f=1; next} f && NF {print; exit}'
python3 - "$TX" "$FUNDER" <<'EOF'
import base64, json, sys, urllib.request
body = {"jsonrpc": "2.0", "id": 1, "method": "EXPERIMENTAL_tx_status", "params": {"tx_hash": sys.argv[1], "sender_account_id": sys.argv[2], "wait_until": "FINAL"}}
r = json.load(urllib.request.urlopen(urllib.request.Request("https://rpc.testnet.near.org", json.dumps(body).encode(), {"content-type": "application/json"})))["result"]
for rc in r["receipts"]:
    for a in rc["receipt"].get("Action", {}).get("actions", []):
        fc = a.get("FunctionCall")
        if fc and fc["method_name"] == "request_execution":
            args = json.loads(base64.b64decode(fc["args"]))
            print("request_execution sent to", rc["receiver_id"], "with resource_limits", json.dumps(args["resource_limits"]), "and input", args["input_data"])
EOF

echo "-- anyone resolves the predictions that wait for $DEMO_DATE"
for id in $(view list_predictions '{}' | python3 -c "import json,sys; print(' '.join(str(p['id']) for p in json.load(sys.stdin) if p['resolve_after'] == '$DEMO_DATE' and p['status'] == 'pending'))"); do
  OUT=$(call resolve_prediction "{\"id\":$id}" '30 Tgas' '0 NEAR' $FUNDER)
  echo "prediction $id: $(echo "$OUT" | tx) $(echo "$OUT" | ev prediction_resolved)"
done
echo "-- NEGATIVE: P1 (resolve_after 2026-12-31) cannot be resolved from any other day's snapshot"
call resolve_prediction '{"id":0}' '30 Tgas' '0 NEAR' $FUNDER | panic
echo "-- predictions now"
view list_predictions '{}' | python3 -c "
import json, sys
for p in json.load(sys.stdin):
    r = p['resolution'] or {}
    print(p['id'], p['resolve_after'], p['metric'], p['comparator'], p['threshold'], '->', p['status'], 'observed', r.get('observed'), 'from', r.get('snapshot_date'))"
echo "-- third-party verification of the deciding snapshot"
python3 -I contract/scripts/verify_attestation.py $C $DEMO_DATE --wasm worker/res/nearbooks-worker.wasm ${DCAP:+--dcap $DCAP}
