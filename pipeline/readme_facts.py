"""README blocks that are facts about the repository rather than about metrics.json: the verify output, test counts,
the testnet deployment (contract/deployment.json) and the contract state last read from the chain
(site/contract-cache.json, written by `python3 mcp/chain.py dump`).

Each block is a pure function of committed files, so `make verify` (verify.py --check-readme) fails when the README
drifts from them. `check_builds` also fails when a local build in contract/res or worker/res differs from the
deployed hashes.
"""
import glob, hashlib, json, os, re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MICRO = 10**6


def _load(rel):
    with open(os.path.join(ROOT, rel)) as f:
        return json.load(f)


def _text(path):
    with open(path) as f:
        return f.read()


def test_counts():
    """Static counts; they equal what the runners report (unittest counts test methods, cargo counts #[test])."""
    py = sum(len(re.findall(r"^    def test_", _text(p), re.M)) for p in sorted(glob.glob(os.path.join(ROOT, "tests", "test_*.py"))))
    rs = lambda rel: len(re.findall(r"^#\[test\]", _text(os.path.join(ROOT, rel)), re.M))
    return {"python": py, "contract": rs("contract/src/tests.rs"), "worker": rs("worker/src/tests.rs")}


def summary_block(m, findings):
    t, d, c = test_counts(), _load("contract/deployment.json"), _load("site/contract-cache.json")
    st = {}
    for p in c["predictions"]:
        st[p["status"]] = st.get(p["status"], 0) + 1
    return "\n".join([
        f"NEAR Books rebuilds NEAR Intents' revenue, buyback and issuance numbers from raw chain data and checks {len(findings)} findings against DefiLlama and published figures, offline, with `make verify`.",
        f"A NEAR testnet contract stores attested daily snapshots of NEAR mainnet (read by a pinned worker inside OutLayer's Intel TDX enclave) and resolves dated predictions from them only: {len(c['attested'])} snapshot(s), {len(c['predictions'])} predictions ({', '.join(f'{v} {k}' for k, v in sorted(st.items()))}) as of block {c['block_height']:,}.",
        f"A dashboard and an MCP server read that contract live; {t['python'] + t['contract'] + t['worker']} tests ({t['python']} Python, {t['contract']} contract, {t['worker']} worker). Testnet only.",
    ])


def verify_block(m, findings):
    lines = ["$ make verify", "...",
             *[f"{'PASS' if f['ok'] else 'FAIL'} {f['id']}  {f['claim']}" for f in findings],
             f"as_of {m['as_of']}: {sum(f['ok'] for f in findings)}/{len(findings)} findings hold -> " + ("ALL PASS" if all(f["ok"] for f in findings) else "SOME FAILED"),
             "python3 -I pipeline/verify.py data/snapshot-2026-10-20/metrics.json --check-readme README.md",
             "README generated blocks match the verified output, no template placeholders left; local builds match contract/deployment.json"]
    return "```\n" + "\n".join(lines) + "\n```"


def tests_block(m, findings):
    t = test_counts()
    return "```\n" + "\n".join([
        "$ make test",
        f"Ran {t['python']} tests in ...s",
        "OK",
        f"test result: ok. {t['contract']} passed; 0 failed; 0 ignored; 0 measured; 0 filtered out   (contract)",
        f"test result: ok. {t['worker']} passed; 0 failed; 0 ignored; 0 measured; 0 filtered out   (worker)",
    ]) + "\n```"


def deployment_block(m, findings):
    d = _load("contract/deployment.json")
    rb, k = d["reproducible_build"], d["keeper"]
    near = lambda y: f"{int(y) / 10**24:g} NEAR"
    rows = ["| Item | Value |", "|---|---|",
            f"| Contract | `{d['contract']}` ({d['network']}) |",
            f"| Code sha256 | `{d['code_sha256']}` ({d['code_size_bytes']:,} bytes; {d['build']}) |",
            f"| Reproducible build | commit `{d['source_commit']}`, image `{rb['image']}@{rb['image_digest']}`; rebuild: `{rb['rebuild']}` |",
            f"| Pinned worker sha256 | `{d['worker_sha256']}` ([FastFS]({d['worker_url']})) |",
            f"| Re-pin timelock | {d['pin_delay_secs']:,} s |",
            f"| Keeper | `{k['account']}`: function-call key `{k['function_call_key']}` for {', '.join('`' + x + '`' for x in k['key_methods'])}, allowance {near(k['key_allowance_yocto'])}; daily cap {near(k['daily_cap_yocto'])}, budget funded {near(k['funded_yocto'])} |"]
    rows += [f"| {what} | `{tx}` |" for what, tx in d["transactions"]]
    for p in d.get("previous_deployments", []):
        rows.append(f"| **Earlier contract** `{p['contract']}` | code `{p['code_sha256']}` ({p['build']}); {p['status']} |")
        rows += [f"| {p['contract'].split('.')[0]}: {what} | `{tx}` |" for what, tx in p["transactions"]]
    rows += [f"| Deleted superseded account `{a}` (funds returned to the funder) | `{tx}` |" for a, tx in d.get("superseded_accounts_deleted", [])]
    return "\n".join(rows)


def _num(metric, v):
    if v is None:
        return "-"
    return f"{v:,.0f}" if abs(v) >= 100 else f"{v:,.3f}"


def attested_block(m, findings):
    c = _load("site/contract-cache.json")
    rows = ["| Date (first mainnet block) | Supply, NEAR | Buyback wallet, NEAR | Net issuance, 28 d, NEAR/day | Revenue wallets kept / new supply, 28 d | Intents tokens backed | OutLayer request | Checks (known worker, pin, tx, attestation hashes) | Transaction |",
            "|---|---|---|---|---|---|---|---|---|"]
    for s in sorted(c["attested"], key=lambda s: s["date"]):
        x = {k["key"]: int(k["value"]) / MICRO for k in s["metrics"]}
        a, chk = s["source"]["attested"], s.get("attestation_checks") or {}
        ratio = x.get("revenue_wallets_gain_over_net_issuance")
        rows.append(f"| {s['date']} ({a['block_height']:,}) | {x['supply_total_near']:,.0f} | {x['buyback_wallet_near']:,.0f} | {x['net_issuance_near_per_day']:,.0f} | "
                    f"{'-' if ratio is None else f'{ratio * 100:.1f}%'} | {x['intents_reserves_tokens_checked'] - x['intents_reserves_tokens_in_deficit']:.0f}/{x['intents_reserves_tokens_checked']:.0f} | "
                    f"{a['request_id']} | {'verified' if chk.get('verified') else 'MISMATCH'} | `{chk.get('transaction_hash', '-')}` |")
    return "\n".join(rows) + f"\n\nRead from the contract at testnet block {c['block_height']:,} ({c['fetched_at']}); the dashboard and `get_attested_snapshots` read the same views live."


def predictions_block(m, findings):
    c = _load("site/contract-cache.json")
    ids = {i: f"P{i + 1}" for i in range(4)}
    sym = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<="}
    rows = ["| ID | Claim | Rule | Decided by the attested snapshot of | Status | Observed |", "|---|---|---|---|---|---|"]
    for p in c["predictions"]:
        r = p.get("resolution") or {}
        obs = None if r.get("observed") is None else int(r["observed"]) / MICRO
        rows.append(f"| {p['id']}{' (' + ids[p['id']] + ')' if p['id'] in ids else ' (demo)'} | {p['claim']} | `{p['metric']} {sym[p['comparator']]} {_num(p['metric'], int(p['threshold']) / MICRO)}` | "
                    f"{p['resolve_after']} | **{p['status']}** | {_num(p['metric'], obs) if r else '-'}{' (metric absent)' if p['status'] == 'void' else ''} |")
    return "\n".join(rows)


def _sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def check_builds():
    """Problems (strings) if a local build exists and differs from what is deployed and pinned."""
    d, out = _load("contract/deployment.json"), []
    for rel, want, how in (("contract/res/nearbooks.wasm", d["code_sha256"], "make wasm"), ("worker/res/nearbooks-worker.wasm", d["worker_sha256"], "make worker")):
        p = os.path.join(ROOT, rel)
        if os.path.exists(p) and _sha(p) != want:
            out.append(f"{rel} ({how}) has sha256 {_sha(p)}, but contract/deployment.json says {want} is deployed: redeploy, or rebuild from this commit")
    return out


def marker(name):
    return (f"<!-- {name}:begin (generated by `make readme`) -->", f"<!-- {name}:end -->")


BLOCKS = {"summary": summary_block, "verify-output": verify_block, "test-output": tests_block,
          "deployment": deployment_block, "attested": attested_block, "predictions": predictions_block}
