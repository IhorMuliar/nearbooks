"""Compare contract/deployment.json with NEAR testnet and with local builds (stdlib only, network, no keys).

    python3 -I contract/scripts/check_deployment.py        # `make verify-deploy`

Checks: the account's on-chain code_hash is the base58 of `code_sha256`; the contract's pin for today is
`worker_sha256` (re-pins are timelocked, see get_pin_proposal) and is in contract/known_workers.json; local
contract/res and worker/res builds, if present, have the same hashes.
"""
import hashlib, json, os, sys, time
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "mcp"))
import chain  # noqa: E402

ROOT = chain.ROOT
B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58(b):
    n, out = int.from_bytes(b, "big"), ""
    while n:
        n, r = divmod(n, 58)
        out = B58[r] + out
    return "1" * (len(b) - len(b.lstrip(b"\0"))) + out


def main():
    d = json.load(open(os.path.join(ROOT, "contract", "deployment.json")))
    known = json.load(open(os.path.join(ROOT, "contract", "known_workers.json")))["worker_sha256"]
    acc = chain.rpc("query", {"request_type": "view_account", "finality": "final", "account_id": d["contract"]})
    results = []
    def check(name, ok, detail):
        results.append(ok)
        print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")
    want = b58(bytes.fromhex(d["code_sha256"]))
    check("on-chain code_hash == base58(deployment code_sha256)", acc["code_hash"] == want, f"{acc['code_hash']} vs {want}")
    pin = chain.view("get_pin_for_date", {"date": time.strftime("%Y-%m-%d", time.gmtime())}, d["contract"])[0]
    check("pin for today == deployment worker_sha256", pin and pin["wasm_hash"] == d["worker_sha256"], pin and pin["wasm_hash"])
    check("pinned worker is in contract/known_workers.json", pin and pin["wasm_hash"] in known, "")
    print(f"INFO  waiting pin proposal: {chain.view('get_pin_proposal', {}, d['contract'])[0]}")
    for rel, h in (("contract/res/nearbooks.wasm", d["code_sha256"]), ("worker/res/nearbooks-worker.wasm", d["worker_sha256"])):
        p = os.path.join(ROOT, rel)
        if os.path.exists(p):
            got = hashlib.sha256(open(p, "rb").read()).hexdigest()
            check(f"local {rel} == deployed", got == h, got)
        else:
            print(f"SKIP  {rel} not built")
    print(f"{sum(results)}/{len(results)} deployment checks pass")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
