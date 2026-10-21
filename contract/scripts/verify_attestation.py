"""Third-party check of an Attested snapshot in the nearbooks contract (stdlib only, network, no keys).

    python3 -I contract/scripts/verify_attestation.py CONTRACT [DATE] [--wasm worker/res/nearbooks-worker.wasm]
                                                     [--dcap /path/to/dcap-qvl] [--register worker.outlayer.testnet]

Reads the attested snapshot (default: the latest to arrive) and the pinned config from the contract (testnet RPC view
calls), fetches the public OutLayer attestation it links to, then checks:
  1. attestation.wasm_hash == snapshot wasm_hash == executed_wasm_sha256 (and == sha256 of a local build, --wasm)
  2. attestation.input_hash == sha256('{"n":"<nonce>","d":"<date>","b":"<block>","b28":"<block 28 days earlier>"}')
     (what the contract sent)
  3. attestation.output_hash == snapshot output_sha256   (the contract hashed the bytes it stored)
  4. the OutLayer receipt in the attestation's transaction returned exactly those bytes, and the stored metrics are
     what those bytes say (yocto / 1e18 = micro-NEAR, and the 28-day metrics recomputed from the two observations);
     that transaction called this contract's request_snapshot (or keeper_request_snapshot)
  5. the TDX quote's report_data[0:32] == sha256 of the task fields (task_type, task_id, build_target, wasm_hash,
     input_hash, output_hash, block_height, caller, timestamp, attached_usd), i.e. the quote is bound to this run
  6. the quote's MRTD + RTMR0-3 are in OutLayer's on-chain approved list (register contract, --register)
  7. with --dcap: the quote's Intel DCAP signature chain verifies (Phala's dcap-qvl CLI, `cargo install dcap-qvl-cli`)
Optionally, independently: the observation block is the first mainnet block of DATE (worker/scripts/first_block.py).
Exit code 0 only if every check that ran passes.
"""
import base64, hashlib, json, os, struct, subprocess, sys, tempfile, time, urllib.error, urllib.request

RPC = "https://rpc.testnet.near.org"
ARCHIVAL_RPC = "https://archival-rpc.testnet.near.org"  # the regular RPC drops transactions after a few days
ATTESTATION_BASES = {"https://testnet-api.outlayer.ai", "https://api.outlayer.ai"}
MAX_RESPONSE = 256 * 1024


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, f"redirect to {newurl!r} refused", headers, fp)


FALLBACK_RPC = {RPC: "https://rpc.testnet.fastnear.com"}  # the public RPC answers 429 under load


def post(url, payload, tries=4):
    body = json.dumps(payload).encode()
    for i in range(tries):
        u = url if i % 2 == 0 else FALLBACK_RPC.get(url, url)
        try:
            req = urllib.request.Request(u, data=body, headers={"content-type": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code != 429 or i == tries - 1:
                raise
            time.sleep(2 * (i + 1))


def view(contract, method, args):
    r = post(RPC, {"jsonrpc": "2.0", "id": 1, "method": "query", "params": {
        "request_type": "call_function", "finality": "final", "account_id": contract, "method_name": method,
        "args_base64": base64.b64encode(json.dumps(args).encode()).decode()}})
    return json.loads(bytes(r["result"]["result"]))


def opt(argv, name, default=None):
    if name in argv:
        i = argv.index(name)
        v = argv[i + 1]
        del argv[i:i + 2]
        return v
    return default


def task_hash(att):
    """report_data binding, as OutLayer's worker computes it (worker/src/tdx_attestation.rs, V1 layout)."""
    h = hashlib.sha256()
    h.update(att["task_type"].encode())
    h.update(struct.pack("<q", att["task_id"]))
    for k in ("build_target", "wasm_hash", "input_hash"):
        if att.get(k):
            h.update(att[k].encode())
    h.update(att["output_hash"].encode())
    if att.get("block_height") is not None:
        h.update(struct.pack("<Q", att["block_height"]))
    if att.get("caller_account_id"):
        h.update(att["caller_account_id"].encode())
    h.update(struct.pack("<q", att["timestamp"]))
    if att.get("attached_usd"):
        h.update(att["attached_usd"].encode())
    return h.hexdigest()


def main(argv):
    wasm, dcap = opt(argv, "--wasm"), opt(argv, "--dcap")
    register = opt(argv, "--register", "worker.outlayer.testnet")
    api = opt(argv, "--attestation-api", "https://testnet-api.outlayer.ai")
    contract = argv[1]
    if len(argv) > 2:
        snap = view(contract, "get_attested_snapshot", {"date": argv[2]})
    else:
        snap = view(contract, "list_attested_snapshots", {"from_index": view(contract, "attested_count", {}) - 1, "limit": 1})[0]
    if not snap or "attested" not in (snap["source"] if isinstance(snap["source"], dict) else {}):
        sys.exit("no attested snapshot found")
    a = snap["source"]["attested"]
    cfg = view(contract, "get_pin_for_date", {"date": snap["date"]})
    print(f"snapshot {snap['date']}: nonce {a['nonce']}, OutLayer request {a['request_id']}, mainnet block {a['block_height']} (ts {a['block_timestamp_ns']})")
    # Never fetch a URL taken from contract state: rebuild it from the allowlisted API base and the numeric request id.
    rid = a["request_id"]
    if not isinstance(rid, int) or isinstance(rid, bool) or not 0 <= rid < 2**63 or api not in ATTESTATION_BASES:
        sys.exit(f"refused: request id {rid!r} / attestation API {api!r} (allowed: {sorted(ATTESTATION_BASES)})")
    url = f"{api}/attestations/by-request/{rid}"
    print(f"attestation: {url}")
    with urllib.request.build_opener(NoRedirect).open(url, timeout=30) as r:
        body = r.read(MAX_RESPONSE + 1)
    if len(body) > MAX_RESPONSE:
        sys.exit("attestation response too large")
    att = json.loads(body)

    checks = []
    def check(name, ok, detail=""):
        checks.append(bool(ok))
        print(f"{'PASS' if ok else 'FAIL'}  {name}{('  ' + detail) if detail else ''}")

    check("snapshot's attestation link == the allowlisted URL for its request id", a["attestation"] == url)

    check("attestation.request_id == snapshot request_id", att.get("request_id") == a["request_id"], str(att.get("request_id")))
    check("attestation.wasm_hash == snapshot wasm_hash", att.get("wasm_hash") == a["wasm_hash"], a["wasm_hash"])
    check("attestation.executed_wasm_sha256 == wasm_hash", att.get("executed_wasm_sha256") == a["wasm_hash"])
    check("snapshot wasm_hash == the contract's pin for that date", cfg["wasm_hash"] == a["wasm_hash"], cfg["outlayer_id"])
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "known_workers.json")) as f:
        known = json.load(f)["worker_sha256"]
    check("wasm_hash is a worker this repo vouches for (contract/known_workers.json)", a["wasm_hash"] in known, known.get(a["wasm_hash"], "not listed"))
    now = view(contract, "get_outlayer_config", {})
    print(f"INFO  current pin: {now['wasm_hash']} ({'same' if now['wasm_hash'] == a['wasm_hash'] else 're-pinned for later dates'}); "
          f"waiting proposal: {view(contract, 'get_pin_proposal', {})}")
    if wasm:
        local = hashlib.sha256(open(wasm, "rb").read()).hexdigest()
        check("local worker build sha256 == wasm_hash", local == a["wasm_hash"], local)
    m = {x["key"]: int(x["value"]) for x in snap["metrics"]}
    b28 = m["mainnet_block_height_28d"] // 10**6
    inp = '{"n":"%d","d":"%s","b":"%d","b28":"%d"}' % (a["nonce"], snap["date"], a["block_height"], b28)
    check("attestation.input_hash == sha256(contract input)", att.get("input_hash") == hashlib.sha256(inp.encode()).hexdigest(), inp)
    check("attestation.output_hash == snapshot output_sha256", att.get("output_hash") == a["output_sha256"], a["output_sha256"])

    # The output bytes live in the transaction: OutLayer's on_execution_response receipt returns them.
    txh, signer = att["transaction_hash"], att["caller_account_id"]
    tx = post(ARCHIVAL_RPC, {"jsonrpc": "2.0", "id": 1, "method": "EXPERIMENTAL_tx_status",
                    "params": {"tx_hash": txh, "sender_account_id": signer, "wait_until": "FINAL"}})["result"]
    fc = tx["transaction"]["actions"][0].get("FunctionCall", {})
    check("transaction calls this contract's request_snapshot or keeper_request_snapshot", tx["transaction"]["receiver_id"] == contract and fc.get("method_name") in ("request_snapshot", "keeper_request_snapshot"), f"{txh} ({fc.get('method_name')})")
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
        y = lambda k: int(o[k])
        q = lambda num, den: -((-num) // den) if num < 0 else num // den  # Rust's integer division truncates toward zero
        iss = y("sup") - y("sup28")
        gain = (y("fe") + y("bb") + y("cs")) - (y("fe28") + y("bb28") + y("cs28"))
        want = {"supply_total_near": y("sup") // 10**18, "buyback_wallet_near": y("bb") // 10**18, "fefundsadmin_wallet_near": y("fe") // 10**18,
                "onecsfundsadmin_wallet_near": y("cs") // 10**18, "supply_total_near_28d_ago": y("sup28") // 10**18,
                "intents_reserves_tokens_checked": y("rc") * 10**6, "intents_reserves_tokens_in_deficit": y("rd") * 10**6,
                "intents_reserves_max_deficit_ppm": y("rm") * 10**6, "mainnet_block_height": y("h") * 10**6, "mainnet_block_height_28d": y("h28") * 10**6,
                "net_issuance_near_per_day": q(iss, 10**18 * 28), "revenue_wallets_gain_near_28d": q(gain, 10**18)}
        if iss > 0 and abs(gain * 10**6) <= 2**127 - 1:  # the contract omits it when gain x 1e6 overflows i128
            want["revenue_wallets_gain_over_net_issuance"] = q(gain * 10**6, iss)
        same = (m == want and int(o["h"]) == a["block_height"] and o["d"] == snap["date"] and o["sha"] == snap["evidence_hash"] and o["n"] == str(a["nonce"]))
        check("stored metrics == attested output (all 13, incl. the 28-day ones recomputed here)" if iss > 0 else "stored metrics == attested output", same, out.decode())

    q = base64.b64decode(att.get("tdx_quote", ""))
    body = q[48:48 + 584]  # TDX v4 quote: 48-byte header, then the TD report body
    rd, mrtd = body[520:552].hex(), body[136:184].hex()
    rtmr = [body[328 + 48 * i:376 + 48 * i].hex() for i in range(4)]
    print(f"INFO  TDX quote {len(q)} bytes, version {int.from_bytes(q[:2], 'little') if q else '-'}, MRTD {mrtd[:16]}..., RTMR3 {rtmr[3][:16]}... (= worker_measurement)")
    check("quote report_data == sha256(task fields): the quote is bound to this run", rd == task_hash(att), rd)
    approved = view(register, "get_approved_measurements", {})
    tup = {"mrtd": mrtd, "rtmr0": rtmr[0], "rtmr1": rtmr[1], "rtmr2": rtmr[2], "rtmr3": rtmr[3]}
    check(f"quote MRTD+RTMR0-3 are in {register} approved measurements", tup in approved, f"({len(approved)} approved sets)")
    if dcap:
        with tempfile.NamedTemporaryFile(suffix=".quote") as f:
            f.write(q)
            f.flush()
            r = subprocess.run([dcap, "verify", f.name], capture_output=True, text=True, timeout=120)
        status = next((json.loads(line).get("status") for line in r.stdout.splitlines() if line.startswith("{")), None)
        check("Intel DCAP signature chain verifies (dcap-qvl)", r.returncode == 0 and "Quote verified" in r.stdout + r.stderr, f"TCB status {status}")
    else:
        print("SKIP  Intel DCAP signature chain (pass --dcap /path/to/dcap-qvl)")
    print(f"{sum(checks)}/{len(checks)} checks pass")
    return 0 if all(checks) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
