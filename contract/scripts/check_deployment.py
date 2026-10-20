"""Compare contract/deployment.json with NEAR testnet and with builds (stdlib only, network, no keys).

    python3 -I contract/scripts/check_deployment.py        # `make verify-deploy`

Checks: the account's on-chain code_hash is the base58 of `code_sha256`, and a reproducible build of `source_commit`
(contract/scripts/repro_build.sh: fresh clone, cargo-near reproducible-wasm in the digest-pinned image) has exactly
that sha256 (SKIP without docker or cargo-near, or with NEARBOOKS_SKIP_REPRO=1); the contract's pin for today is
`worker_sha256` (re-pins are timelocked, see get_pin_proposal) and is in contract/known_workers.json; the contract's
keeper and daily cap are the recorded ones and the keeper's key on chain is a function-call key limited to this
contract and the two keeper methods; earlier contracts that are still live run the code recorded for them; local
contract/res and worker/res builds, if present, have the deployed hashes.
"""
import hashlib, json, os, shutil, subprocess, sys, tempfile, time
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


def reproducible_build(commit):
    """sha256 of a reproducible build of `commit`, or (None, reason) when it cannot run here."""
    if os.environ.get("NEARBOOKS_SKIP_REPRO") == "1":
        return None, "NEARBOOKS_SKIP_REPRO=1"
    cargo_near = os.environ.get("CARGO_NEAR") or shutil.which("cargo-near")
    if not cargo_near or not shutil.which("docker"):
        return None, "needs docker and cargo-near (set CARGO_NEAR)"
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "nearbooks.wasm")
        for attempt in (1, 2, 3):  # the container fetches crates from crates.io, whose CDN sometimes answers 500
            if attempt > 1:
                time.sleep(30)
            r = subprocess.run([os.path.join(ROOT, "contract", "scripts", "repro_build.sh"), commit, out], env={**os.environ, "CARGO_NEAR": cargo_near},
                               capture_output=True, text=True)
            if r.returncode != 1:
                break
            errors = [l.strip() for l in r.stderr.splitlines() if l.strip().startswith("error")] or r.stderr.strip().splitlines()[-1:] or ["?"]
            print(f"INFO  reproducible build attempt {attempt} failed: {errors[0][:160]}")
        if r.returncode == 2:
            return None, r.stderr.strip()
        if r.returncode:
            return "build failed", "see the INFO lines above"
        env = next((l for l in r.stdout.splitlines() if l.startswith("CARGO_NEAR_BUILD_ENVIRONMENT=")), "")
        return hashlib.sha256(open(out, "rb").read()).hexdigest(), env.split("=", 1)[-1]


def main():
    d = json.load(open(os.path.join(ROOT, "contract", "deployment.json")))
    known = json.load(open(os.path.join(ROOT, "contract", "known_workers.json")))["worker_sha256"]
    account = lambda a: chain.rpc("query", {"request_type": "view_account", "finality": "final", "account_id": a})
    results = []
    def check(name, ok, detail):
        results.append(bool(ok))
        print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")
    want = b58(bytes.fromhex(d["code_sha256"]))
    acc = account(d["contract"])
    check("on-chain code_hash == base58(deployment code_sha256)", acc["code_hash"] == want, f"{acc['code_hash']} vs {want}")
    got, how = reproducible_build(d["source_commit"])
    if got is None:
        print(f"SKIP  reproducible build of {d['source_commit'][:7]} ({how})")
    else:
        check(f"reproducible build of {d['source_commit'][:7]} == deployed code", got == d["code_sha256"], f"{got} ({how})")
    pin = chain.view("get_pin_for_date", {"date": time.strftime("%Y-%m-%d", time.gmtime())}, d["contract"])[0]
    check("pin for today == deployment worker_sha256", pin and pin["wasm_hash"] == d["worker_sha256"], pin and pin["wasm_hash"])
    check("pinned worker is in contract/known_workers.json", pin and pin["wasm_hash"] in known, "")
    print(f"INFO  waiting pin proposal: {chain.view('get_pin_proposal', {}, d['contract'])[0]}")
    k, kd = chain.view("get_keeper", {}, d["contract"])[0], d["keeper"]
    check("contract keeper and daily cap == deployment", k["keeper_id"] == kd["account"] and k["daily_cap"] == kd["daily_cap_yocto"],
          f"{k['keeper_id']}, cap {int(k['daily_cap']) / 1e24:g} NEAR, budget {int(k['budget']) / 1e24:g} NEAR, spent today {int(k['spent_today']) / 1e24:g} NEAR")
    ak = chain.rpc("query", {"request_type": "view_access_key", "finality": "final", "account_id": kd["account"], "public_key": kd["function_call_key"]})
    fc = ak["permission"].get("FunctionCall", {}) if isinstance(ak["permission"], dict) else {}
    check("keeper key is a function-call key for this contract and the two keeper methods",
          fc.get("receiver_id") == d["contract"] and sorted(fc.get("method_names", [])) == sorted(kd["key_methods"]),
          f"allowance left {int(fc.get('allowance') or 0) / 1e24:.4f} of {int(kd['key_allowance_yocto']) / 1e24:g} NEAR")
    keys = chain.rpc("query", {"request_type": "view_access_key_list", "finality": "final", "account_id": kd["account"]})["keys"]
    full = sum(1 for x in keys if x["access_key"]["permission"] == "FullAccess")
    print(f"INFO  keeper account keys: {len(keys)} ({full} full-access, held offline by the deployer; CI holds only the function-call key)")
    for p in d.get("previous_deployments", []):
        prev = account(p["contract"])
        check(f"earlier contract {p['contract'].split('.')[0]} still runs its recorded code", prev["code_hash"] == b58(bytes.fromhex(p["code_sha256"])), prev["code_hash"])
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
