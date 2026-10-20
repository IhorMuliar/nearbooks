"""Third-party check of an Attested snapshot in the nearbooks contract (stdlib only, network, no keys).

    python3 -I contract/scripts/verify_attestation.py CONTRACT [SNAPSHOT_INDEX] [--wasm worker/res/nearbooks-worker.wasm]

Reads the snapshot and the pinned config from the contract (testnet RPC view calls), fetches the public OutLayer
attestation it links to, then checks:
  1. attestation.wasm_hash == snapshot.source.wasm_hash == executed_wasm_sha256 (and == sha256 of a local build if --wasm)
  2. attestation.input_hash == sha256('{"n":"<nonce>"}')   (the exact input the contract sent)
  3. attestation.output_hash == snapshot.source.output_sha256   (the contract hashed the bytes it stored)
  4. the OutLayer receipt in the attestation's transaction returned exactly those output bytes, and the stored
     metrics are what those bytes say (yocto / 1e18 = micro-NEAR)
  5. the attestation's request_id matches, and that transaction was sent to this contract's request_snapshot
It prints the TDX quote size and worker measurement; verifying the quote's Intel DCAP signature chain is a separate
step (docs/attestation.md). Exit code 0 only if every check passes.
"""
import base64, hashlib, json, sys, urllib.request

RPC = "https://rpc.testnet.near.org"


def post(url, payload):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def view(contract, method, args):
    r = post(RPC, {"jsonrpc": "2.0", "id": 1, "method": "query", "params": {
        "request_type": "call_function", "finality": "final", "account_id": contract, "method_name": method,
        "args_base64": base64.b64encode(json.dumps(args).encode()).decode()}})
    return json.loads(bytes(r["result"]["result"]))


def main(argv):
    wasm = None
    if "--wasm" in argv:
        i = argv.index("--wasm"); wasm = argv[i + 1]; del argv[i:i + 2]
    contract = argv[1]
    count = view(contract, "snapshot_count", {})
    idx = int(argv[2]) if len(argv) > 2 else count - 1
    snap = view(contract, "list_snapshots", {"from_index": idx, "limit": 1})[0]
    src = snap["source"]
    if not isinstance(src, dict) or "attested" not in src:
        sys.exit(f"snapshot {idx} ({snap['date']}) is {src!r}, not attested")
    a = src["attested"]
    cfg = view(contract, "get_outlayer_config", {})
    print(f"snapshot #{idx} date {snap['date']} nonce {a['nonce']} request_id {a['request_id']} mainnet block {a['block_height']}")
    print(f"attestation: {a['attestation']}")
    with urllib.request.urlopen(a["attestation"], timeout=30) as r:
        att = json.load(r)

    checks = []
    def check(name, ok, detail=""):
        checks.append(ok)
        print(f"{'PASS' if ok else 'FAIL'}  {name}{('  ' + detail) if detail else ''}")

    check("attestation.request_id == snapshot request_id", att.get("request_id") == a["request_id"], str(att.get("request_id")))
    check("attestation.wasm_hash == snapshot wasm_hash", att.get("wasm_hash") == a["wasm_hash"], a["wasm_hash"])
    check("attestation.executed_wasm_sha256 == wasm_hash", att.get("executed_wasm_sha256") == a["wasm_hash"])
    print(f"INFO  hash pinned in contract config now: {cfg['wasm_hash']} ({'same' if cfg['wasm_hash'] == a['wasm_hash'] else 're-pinned since'})")
    if wasm:
        local = hashlib.sha256(open(wasm, "rb").read()).hexdigest()
        check("local worker build sha256 == wasm_hash", local == a["wasm_hash"], local)
    inp = '{"n":"%d"}' % a["nonce"]
    check("attestation.input_hash == sha256(contract input)", att.get("input_hash") == hashlib.sha256(inp.encode()).hexdigest(), inp)
    check("attestation.output_hash == snapshot output_sha256", att.get("output_hash") == a["output_sha256"], a["output_sha256"])

    # The output bytes live in the transaction: OutLayer's on_execution_response receipt returns them.
    txh, signer = att["transaction_hash"], att["caller_account_id"]
    tx = post(RPC, {"jsonrpc": "2.0", "id": 1, "method": "EXPERIMENTAL_tx_status",
                    "params": {"tx_hash": txh, "sender_account_id": signer, "wait_until": "FINAL"}})["result"]
    fc = tx["transaction"]["actions"][0].get("FunctionCall", {})
    check("transaction calls this contract's request_snapshot", tx["transaction"]["receiver_id"] == contract and fc.get("method_name") == "request_snapshot", txh)
    out = None
    for ro in tx["receipts_outcome"]:
        sv = ro["outcome"]["status"].get("SuccessValue")
        if ro["outcome"]["executor_id"] == cfg["outlayer_id"] and sv:
            b = base64.b64decode(sv)
            if hashlib.sha256(b).hexdigest() == att.get("output_hash"):
                out = b
    check("OutLayer receipt returned bytes hashing to output_hash", out is not None)
    if out is not None:
        o = json.loads(out)
        m = {x["key"]: int(x["value"]) for x in snap["metrics"]}
        same = (m["supply_total_near"] == int(o["sup"]) // 10**18 and m["buyback_wallet_near"] == int(o["bb"]) // 10**18
                and m["fefundsadmin_wallet_near"] == int(o["fe"]) // 10**18 and m["onecsfundsadmin_wallet_near"] == int(o["cs"]) // 10**18
                and int(o["h"]) == a["block_height"] and o["sha"] == snap["evidence_hash"] and o["n"] == str(a["nonce"]))
        check("stored metrics == attested output", same, out.decode())
    q = base64.b64decode(att.get("tdx_quote", ""))
    print(f"INFO  TDX quote {len(q)} bytes, version {int.from_bytes(q[:2], 'little') if q else '-'}, worker_measurement {att.get('worker_measurement')}")
    print(f"{sum(checks)}/{len(checks)} checks pass")
    return 0 if all(checks) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
