#!/usr/bin/env bash
# End-to-end run on NEAR TESTNET only. Uses the faucet-sponsored account service, so no funds of yours are involved.
# Needs: near-cli-rs (`near`) on PATH or $NEAR, python3, and the built contract/res/nearbooks.wasm (./build.sh).
# Keys are written under $NEAR_HOME (default .tools/home, git-ignored). Prints every transaction id.
set -uo pipefail
cd "$(dirname "$0")/../.."
NEAR=${NEAR:-near}
export NEAR_HOME=${NEAR_HOME:-$PWD/.tools/home}; mkdir -p "$NEAR_HOME"
n() { HOME="$NEAR_HOME" "$NEAR" "$@" 2>&1; }
# retry a transaction-sending command (the testnet RPC occasionally drops one and the faucet rate-limits with HTTP 429) until it yields a tx id; abort the demo if it never does
rtx() { local T; for attempt in 1 2 3 4 5; do T=$("$@" | tx); [ -n "$T" ] && { echo "$T"; return 0; }; sleep $((attempt*20)); done; echo "FAILED: $*" >&2; exit 1; }
tx() { awk '/Transaction ID/ && !f {print $NF; f=1}'; }   # reads all input (no SIGPIPE on the CLI)
METRICS=${METRICS:-data/snapshot-2026-10-20/metrics.json}
R=${SUFFIX:-$RANDOM}; OWNER=nbproto-owner-$R.testnet; REP=nbproto-rep-$R.testnet
echo "owner/contract account: $OWNER   reporter account: $REP"

# REUSE=1 SUFFIX=<n> FCPK=<reporter function-call public key>: skip account/key/prediction creation and only redeploy the
# current wasm onto accounts from an earlier run (used when the faucet rate-limits with HTTP 429). State must survive the redeploy.
REUSE=${REUSE:-0}
if [ "$REUSE" = 1 ]; then
  echo "-- REUSE mode: redeploying the current build onto the existing accounts (no init call)"
  rtx n contract deploy $OWNER use-file contract/res/nearbooks.wasm without-init-call network-config testnet sign-with-legacy-keychain send
  FCFILE=$(grep -l "$FCPK" $NEAR_HOME/.near-credentials/testnet/$REP/*.json | head -1)
  FCSECRET=$(python3 -c "import json;print(json.load(open('$FCFILE'))['private_key'])")
  SNAP=$(python3 contract/scripts/make_args.py snapshot $METRICS)
  call() { n contract call-function as-transaction $OWNER "$1" json-args "$2" prepaid-gas '30 Tgas' attached-deposit '0 NEAR' sign-as "$3" network-config testnet "${@:4}" send; }
  echo "-- state survived the redeploy (snapshot_count, predictions)"
  n contract call-function as-read-only $OWNER snapshot_count json-args '{}' network-config testnet now | grep -A1 "return value" | tail -1
  n contract call-function as-read-only $OWNER list_predictions json-args '{}' network-config testnet now | grep -E '"status"' | sort | uniq -c
  echo "-- NEGATIVE: the owner is not the reporter"
  call submit_snapshot "$SNAP" $OWNER sign-with-legacy-keychain | grep -o 'Smart contract panicked: [^"]*'
  echo "-- NEGATIVE: same date again (append-only), signed with the function-call key"
  call submit_snapshot "$SNAP" $REP sign-with-plaintext-private-key "$FCSECRET" | grep -o 'Smart contract panicked: [^"]*'
  echo "-- NEGATIVE: resolve P1 before a snapshot on/after 2026-12-31 exists"
  call resolve_prediction '{"id":0}' $OWNER sign-with-legacy-keychain | grep -o 'Smart contract panicked: [^"]*'
  echo "-- NEGATIVE: the reporter's function-call key cannot call anything but submit_snapshot"
  call resolve_prediction '{"id":0}' $REP sign-with-plaintext-private-key "$FCSECRET" | grep -o "Transaction method name.*access key" | head -1
  exit 0
fi
echo "-- create accounts (faucet-sponsored)"
rtx n account create-account sponsor-by-faucet-service $OWNER autogenerate-new-keypair save-to-legacy-keychain network-config testnet create
rtx n account create-account sponsor-by-faucet-service $REP autogenerate-new-keypair save-to-legacy-keychain network-config testnet create

echo "-- deploy + init (owner=$OWNER reporter=$REP)"
rtx n contract deploy $OWNER use-file contract/res/nearbooks.wasm with-init-call new json-args "{\"owner_id\":\"$OWNER\",\"reporter_id\":\"$REP\"}" prepaid-gas '30 Tgas' attached-deposit '0 NEAR' network-config testnet sign-with-legacy-keychain send

echo "-- give the reporter a function-call key that can only call submit_snapshot on the contract"
BEFORE=$(ls $NEAR_HOME/.near-credentials/testnet/$REP)
rtx n account add-key $REP grant-function-call-access --allowance '0.5 NEAR' --contract-account-id $OWNER --function-names submit_snapshot autogenerate-new-keypair save-to-legacy-keychain network-config testnet sign-with-legacy-keychain send
FCFILE=$(comm -13 <(echo "$BEFORE") <(ls $NEAR_HOME/.near-credentials/testnet/$REP) | head -1)
FCSECRET=$(python3 -c "import json;print(json.load(open('$NEAR_HOME/.near-credentials/testnet/$REP/$FCFILE'))['private_key'])")
FCPK=$(python3 -c "import json;print(json.load(open('$NEAR_HOME/.near-credentials/testnet/$REP/$FCFILE'))['public_key'])")
echo "function-call key: $FCPK"

SNAP=$(python3 contract/scripts/make_args.py snapshot $METRICS)
call() { n contract call-function as-transaction $OWNER "$1" json-args "$2" prepaid-gas '30 Tgas' attached-deposit '0 NEAR' sign-as "$3" network-config testnet "${@:4}" send; }

echo "-- NEGATIVE: the owner is not the reporter"
call submit_snapshot "$SNAP" $OWNER sign-with-legacy-keychain | grep -o 'Smart contract panicked: [^"]*'
echo "-- reporter submits the snapshot using ONLY its function-call key"
rtx call submit_snapshot "$SNAP" $REP sign-with-plaintext-private-key "$FCSECRET"
echo "-- NEGATIVE: same date again (append-only)"
call submit_snapshot "$SNAP" $REP sign-with-plaintext-private-key "$FCSECRET" | grep -o 'Smart contract panicked: [^"]*'

echo "-- owner registers the predictions"
N=$(python3 contract/scripts/make_args.py count $METRICS)
for i in $(seq 0 $((N-1))); do
  rtx call add_prediction "$(python3 contract/scripts/make_args.py prediction $METRICS $i)" $OWNER sign-with-legacy-keychain
done
echo "-- NEGATIVE: anyone tries to resolve P1 before a snapshot on/after 2026-12-31 exists"
call resolve_prediction '{"id":0}' $OWNER sign-with-legacy-keychain | grep -o 'Smart contract panicked: [^"]*'
echo "-- NEGATIVE: the reporter's function-call key cannot call anything but submit_snapshot"
call resolve_prediction '{"id":0}' $REP sign-with-plaintext-private-key "$FCSECRET" | grep -o "Transaction method name.*access key" | head -1
echo "-- views"
n contract call-function as-read-only $OWNER snapshot_count json-args '{}' network-config testnet now | grep -A1 "return value" | tail -1
n contract call-function as-read-only $OWNER list_predictions json-args '{}' network-config testnet now | grep -E '"(id|status|resolve_after)"' | head -12
