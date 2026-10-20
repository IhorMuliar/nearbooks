#!/usr/bin/env python3
"""Read the nearbooks contract on NEAR testnet over JSON-RPC view calls (standard library only, no keys).

    python3 mcp/chain.py state            # print the live contract state as JSON
    python3 mcp/chain.py dump OUT.json    # write it (plus attestation tx hashes) as the offline cache

Used by mcp/server.py (get_attested_snapshots, get_predictions). site/index.html does the same reads from the browser.
The offline cache (site/contract-cache.json) is what both fall back to when the RPC cannot be reached.
"""
import base64, hashlib, ipaddress, json, os, sys, time, urllib.error, urllib.parse, urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OFFICIAL_CONTRACT = "nearbooks-v5.okf-kill-1792497882.testnet"
CONTRACT = os.environ.get("NEARBOOKS_CONTRACT", OFFICIAL_CONTRACT)
RPCS = [u for u in os.environ.get("NEARBOOKS_RPC", "https://rpc.testnet.near.org,https://rpc.testnet.fastnear.com").split(",") if u]
CACHE = os.environ.get("NEARBOOKS_CACHE", os.path.join(ROOT, "site", "contract-cache.json"))
EXPLORER = "https://testnet.nearblocks.io"
MICRO = 10**6
# Network access is limited to these HTTPS hosts. URLs found in contract state are never fetched as given: the
# attestation URL is rebuilt from an allowlisted base and the snapshot's numeric request id.
# Transactions are looked up on archival nodes: the regular testnet RPCs drop data after a few days.
ARCHIVAL_RPCS = ["https://archival-rpc.testnet.near.org", "https://archival-rpc.testnet.fastnear.com"]
RPC_HOSTS = {"rpc.testnet.near.org", "rpc.testnet.fastnear.com", "test.rpc.fastnear.com", "archival-rpc.testnet.near.org", "archival-rpc.testnet.fastnear.com"}
ATTESTATION_BASES = {"https://testnet-api.outlayer.ai", "https://api.outlayer.ai"}
ATTESTATION_API = os.environ.get("NEARBOOKS_ATTESTATION_API", "https://testnet-api.outlayer.ai")
# Tests run a fake RPC on 127.0.0.1; only this explicit switch lets a loopback http:// RPC through.
ALLOW_LOOPBACK_RPC = os.environ.get("NEARBOOKS_RPC_ALLOW_LOOPBACK") == "1"
MAX_RESPONSE_BYTES = 256 * 1024
with open(os.path.join(ROOT, "contract", "known_workers.json")) as _f:
    KNOWN_WORKERS = set(json.load(_f)["worker_sha256"])


class Unreachable(Exception):
    pass


class RefusedUrl(ValueError):
    pass


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, f"redirect to {newurl!r} refused", headers, fp)


_OPENER = urllib.request.build_opener(_NoRedirect)


def _is_loopback(host):
    try:
        return host == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def check_url(url, hosts, loopback_ok=False):
    """Returns url if it is https on an allowlisted host (default port, no credentials); raises RefusedUrl otherwise."""
    u = urllib.parse.urlsplit(url)
    host = u.hostname or ""
    if u.username or u.password:
        raise RefusedUrl(f"refused {url!r}: credentials in URL")
    if loopback_ok and _is_loopback(host) and u.scheme in ("http", "https"):
        return url
    if u.scheme != "https":
        raise RefusedUrl(f"refused {url!r}: only https is allowed")
    if host not in hosts or u.port not in (None, 443):
        raise RefusedUrl(f"refused {url!r}: host not in the allowlist {sorted(hosts)}")
    return url


def _read(req, timeout):
    with _OPENER.open(req, timeout=timeout) as r:
        body = r.read(MAX_RESPONSE_BYTES + 1)
    if len(body) > MAX_RESPONSE_BYTES:
        raise ValueError(f"response larger than {MAX_RESPONSE_BYTES} bytes")
    return json.loads(body)


def _post(url, payload, timeout):
    check_url(url, RPC_HOSTS, loopback_ok=ALLOW_LOOPBACK_RPC)
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"content-type": "application/json"})
    return _read(req, timeout)


def view(method, args=None, contract=None, timeout=10):
    """call_function view at final finality; tries each RPC in turn. Returns (value, rpc_url, block_height)."""
    params = {"request_type": "call_function", "finality": "final", "account_id": contract or CONTRACT, "method_name": method,
              "args_base64": base64.b64encode(json.dumps(args or {}).encode()).decode()}
    errors = []
    for url in RPCS:
        try:
            r = _post(url, {"jsonrpc": "2.0", "id": "nb", "method": "query", "params": params}, timeout)
        except RefusedUrl as e:
            errors.append(str(e))
            continue
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


def attestation_url(snap):
    """The public attestation of a snapshot, built from the allowlisted API base and its numeric OutLayer request id
    (the URL string stored in the contract is only compared with this, never fetched)."""
    rid = snap["source"]["attested"]["request_id"]
    if not isinstance(rid, int) or isinstance(rid, bool) or not 0 <= rid < 2**63:
        raise RefusedUrl(f"refused request id {rid!r}")
    if ATTESTATION_API not in ATTESTATION_BASES:
        raise RefusedUrl(f"refused attestation API {ATTESTATION_API!r}: not in {sorted(ATTESTATION_BASES)}")
    return f"{ATTESTATION_API}/attestations/by-request/{rid}"


def rpc(method, params, timeout=10, urls=None):
    errors = []
    for url in urls or RPCS:
        try:
            r = _post(url, {"jsonrpc": "2.0", "id": "nb", "method": method, "params": params}, timeout)
        except (OSError, ValueError) as e:
            errors.append(f"{url}: {e}")
            continue
        if "result" in r:
            return r["result"]
        errors.append(f"{url}: {json.dumps(r.get('error'))[:200]}")
    raise Unreachable("; ".join(errors))


def tx_targets(tx_hash, sender, contract):
    """True if the transaction is `sender` calling `contract`.request_snapshot (read from the chain, not the attestation)."""
    t = rpc("EXPERIMENTAL_tx_status", {"tx_hash": tx_hash, "sender_account_id": sender, "wait_until": "FINAL"}, urls=ARCHIVAL_RPCS)["transaction"]
    acts = t.get("actions") or []
    fc = acts[0].get("FunctionCall", {}) if acts and isinstance(acts[0], dict) else {}
    return t.get("receiver_id") == contract and fc.get("method_name") == "request_snapshot"


def attestation_checks(snap, contract=None, timeout=15):
    """Fetches the public OutLayer attestation of the snapshot and compares it with the snapshot, the contract's pin for
    that date, the known-good worker list and the transaction on chain. `verified` needs all of them. The TDX quote
    itself is not checked here (contract/scripts/verify_attestation.py does that)."""
    contract = contract or CONTRACT
    a = snap["source"]["attested"]
    url = attestation_url(snap)
    att = _post_get(url, timeout)
    pin = view("get_pin_for_date", {"date": snap["date"]}, contract)[0] or {}
    tx = att.get("transaction_hash")
    c = {
        "transaction_hash": tx,
        "official_contract": contract == OFFICIAL_CONTRACT,
        "known_worker": a["wasm_hash"] in KNOWN_WORKERS,
        "pin_for_date_matches": pin.get("wasm_hash") == a["wasm_hash"],
        "tx_targets_contract": bool(tx) and tx_targets(tx, att.get("caller_account_id", ""), contract),
        "attestation_url_matches": a["attestation"] == url,
        "wasm_hash_matches": att.get("wasm_hash") == a["wasm_hash"] == att.get("executed_wasm_sha256"),
        "input_hash_matches": att.get("input_hash") == hashlib.sha256(worker_input(snap).encode()).hexdigest(),
        "output_hash_matches": att.get("output_hash") == a["output_sha256"],
        "request_id_matches": att.get("request_id") == a["request_id"],
    }
    c["verified"] = all(v for k, v in c.items() if k != "transaction_hash")
    return c


def _post_get(url, timeout):
    check_url(url, {urllib.parse.urlsplit(b).hostname for b in ATTESTATION_BASES})
    return _read(urllib.request.Request(url), timeout)


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
            except (OSError, ValueError, Unreachable) as e:  # RefusedUrl is a ValueError
                s["attestation_checks"] = {"error": str(e)[:200], "verified": False}
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
    out = {"date": s["date"], "known_worker": s["source"]["attested"]["wasm_hash"] in KNOWN_WORKERS, "metrics": metrics_dict(s), "mainnet_block_height": a["block_height"], "wasm_hash": a["wasm_hash"],
           "outlayer_request_id": a["request_id"], "output_sha256": a["output_sha256"], "evidence_hash": s["evidence_hash"],
           "attestation_url": _attestation_url_or_none(s), "requested_by": s["submitted_by"], "stored_in_testnet_block": s["block_height"],
           "stored_block_url": f"{EXPLORER}/blocks/{s['block_height']}"}
    chk = s.get("attestation_checks")
    if chk:
        out["attestation_checks"] = chk
        if chk.get("transaction_hash"):
            out["transaction_url"] = f"{EXPLORER}/txns/{chk['transaction_hash']}"
    return out


def _attestation_url_or_none(s):
    try:
        return attestation_url(s)
    except RefusedUrl:
        return None


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
