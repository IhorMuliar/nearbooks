#!/usr/bin/env python3
"""Read the nearbooks contract on NEAR testnet over JSON-RPC view calls (standard library only, no keys).

    python3 mcp/chain.py state            # print the live contract state as JSON
    python3 mcp/chain.py dump OUT.json    # write it (plus attestation tx hashes) as the offline cache

Used by mcp/server.py (get_attested_snapshots, get_predictions). site/index.html does the same reads from the browser.
The offline cache (site/contract-cache.json) is what both fall back to when the RPC cannot be reached.
"""
import base64, hashlib, json, os, sys, time, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONTRACT = os.environ.get("NEARBOOKS_CONTRACT", "nearbooks-v5.okf-kill-1792497882.testnet")
RPCS = [u for u in os.environ.get("NEARBOOKS_RPC", "https://rpc.testnet.near.org,https://rpc.testnet.fastnear.com").split(",") if u]
CACHE = os.environ.get("NEARBOOKS_CACHE", os.path.join(ROOT, "site", "contract-cache.json"))
EXPLORER = "https://testnet.nearblocks.io"
MICRO = 10**6


class Unreachable(Exception):
    pass


def _post(url, payload, timeout):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def view(method, args=None, contract=None, timeout=10):
    """call_function view at final finality; tries each RPC in turn. Returns (value, rpc_url, block_height)."""
    params = {"request_type": "call_function", "finality": "final", "account_id": contract or CONTRACT, "method_name": method,
              "args_base64": base64.b64encode(json.dumps(args or {}).encode()).decode()}
    errors = []
    for url in RPCS:
        try:
            r = _post(url, {"jsonrpc": "2.0", "id": "nb", "method": "query", "params": params}, timeout)
        except (OSError, ValueError) as e:
            errors.append(f"{url}: {e}")
            continue
        res = r.get("result")
        if not res or "result" not in res:
            errors.append(f"{url}: {json.dumps(r.get('error') or res)[:200]}")
            continue
        return json.loads(bytes(res["result"])), url, res.get("block_height")
    raise Unreachable("; ".join(errors) or "no RPC configured")


def _all(method, contract):
    out, i = [], 0
    while True:
        page, url, height = view(method, {"from_index": i, "limit": 100}, contract)
        out += page
        if len(page) < 100:
            return out, url, height
        i += 100


def worker_input(snap):
    """The exact input the contract sent to the worker; its sha256 is the attestation's input_hash."""
    a = snap["source"]["attested"]
    b28 = next(int(m["value"]) for m in snap["metrics"] if m["key"] == "mainnet_block_height_28d") // MICRO
    return '{"n":"%d","d":"%s","b":"%d","b28":"%d"}' % (a["nonce"], snap["date"], a["block_height"], b28)


def attestation_checks(snap, timeout=15):
    """Fetches the public OutLayer attestation the snapshot links to and compares its hashes with the snapshot. The TDX
    quote itself is not checked here (contract/scripts/verify_attestation.py does that)."""
    a = snap["source"]["attested"]
    att = _post_get(a["attestation"], timeout)
    return {
        "transaction_hash": att.get("transaction_hash"),
        "wasm_hash_matches": att.get("wasm_hash") == a["wasm_hash"] == att.get("executed_wasm_sha256"),
        "input_hash_matches": att.get("input_hash") == hashlib.sha256(worker_input(snap).encode()).hexdigest(),
        "output_hash_matches": att.get("output_hash") == a["output_sha256"],
        "request_id_matches": att.get("request_id") == a["request_id"],
    }


def _post_get(url, timeout):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.load(r)


def fetch_state(contract=None, with_attestations=False):
    """Everything the dashboard and the MCP tools show, read live."""
    contract = contract or CONTRACT
    attested, url, height = _all("list_attested_snapshots", contract)
    predictions, _, _ = _all("list_predictions", contract)
    owner_reporter, _, _ = view("get_config", {}, contract)
    state = {
        "contract": contract, "network": "testnet", "rpc": url, "block_height": height, "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "owner_id": owner_reporter[0], "reporter_id": owner_reporter[1],
        "pin_delay_secs": view("get_pin_delay_secs", {}, contract)[0], "pins": view("list_pins", {}, contract)[0],
        "pin_proposal": view("get_pin_proposal", {}, contract)[0], "earliest_date": view("get_earliest_date", {}, contract)[0],
        "attested": attested, "predictions": predictions,
    }
    if with_attestations:
        for s in attested:
            try:
                s["attestation_checks"] = attestation_checks(s)
            except (OSError, ValueError) as e:
                s["attestation_checks"] = {"error": str(e)[:200]}
    return state


def load_cache():
    with open(CACHE) as f:
        return json.load(f)


def state_or_cache(with_attestations=False):
    """(state, mode): mode is 'live' or 'offline' (the committed cache; its fetched_at says how old it is)."""
    try:
        return fetch_state(with_attestations=with_attestations), "live"
    except Unreachable as e:
        s = load_cache()
        s["offline_reason"] = str(e)[:300]
        return s, "offline"


# ------------------------------------------------------------ presentation

def metrics_dict(snap):
    return {m["key"]: int(m["value"]) / MICRO for m in snap["metrics"]}


def snapshot_view(s):
    a = s["source"]["attested"]
    out = {"date": s["date"], "metrics": metrics_dict(s), "mainnet_block_height": a["block_height"], "wasm_hash": a["wasm_hash"],
           "outlayer_request_id": a["request_id"], "output_sha256": a["output_sha256"], "evidence_hash": s["evidence_hash"],
           "attestation_url": a["attestation"], "requested_by": s["submitted_by"], "stored_in_testnet_block": s["block_height"],
           "stored_block_url": f"{EXPLORER}/blocks/{s['block_height']}"}
    chk = s.get("attestation_checks")
    if chk:
        out["attestation_checks"] = chk
        if chk.get("transaction_hash"):
            out["transaction_url"] = f"{EXPLORER}/txns/{chk['transaction_hash']}"
    return out


def prediction_view(p):
    r = p.get("resolution")
    return {"id": p["id"], "claim": p["claim"], "metric": p["metric"], "comparator": p["comparator"], "threshold": int(p["threshold"]) / MICRO,
            "resolve_after": p["resolve_after"], "created_date": p["created_date"], "status": p["status"],
            "observed": (int(r["observed"]) / MICRO if r and r.get("observed") is not None else None),
            "decided_by_snapshot": r["snapshot_date"] if r else None, "resolved_by": r["resolved_by"] if r else None}


def main(argv):
    if len(argv) >= 2 and argv[1] == "state":
        print(json.dumps(fetch_state(with_attestations="--attestations" in argv), indent=1))
    elif len(argv) == 3 and argv[1] == "dump":
        s = fetch_state(with_attestations=True)
        with open(argv[2], "w") as f:
            json.dump(s, f, indent=1)
            f.write("\n")
        print(f"wrote {argv[2]}: {len(s['attested'])} attested snapshots, {len(s['predictions'])} predictions, block {s['block_height']}")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
