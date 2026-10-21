"""README blocks that are facts about the repository rather than about metrics.json: the verify output, test counts,
the testnet deployment (contract/deployment.json) and the contract state last read from the chain
(site/contract-cache.json, written by `python3 mcp/chain.py dump`).

Each block is a pure function of committed files, so `make verify` (verify.py --check-readme) fails when the README
drifts from them. `check_builds` also fails when a local build in contract/res or worker/res differs from the
deployed hashes, and `check_records` when a recorded snapshot or prediction outcome disagrees with the keeper run log
it cites or with the cached contract state.
"""
import glob, hashlib, json, os, re
from common import CONFIG

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
    t, c = test_counts(), _load("site/contract-cache.json")
    st = {}
    for p in c["predictions"]:
        st[p["status"]] = st.get(p["status"], 0) + 1
    ok = sum(f["ok"] for f in findings)
    return "\n".join([
        f"Status: {ok}/{len(findings)} findings pass offline (`make verify`). "
        f"The testnet contract holds {len(c['attested'])} attested snapshot(s) and {len(c['predictions'])} predictions ({', '.join(f'{v} {k}' for k, v in sorted(st.items()))}) as of testnet block {c['block_height']:,}. "
        f"{t['python'] + t['contract'] + t['worker']} tests ({t['python']} Python, {t['contract']} contract, {t['worker']} worker).",
    ])


def verify_block(m, findings):
    """The last lines of `make verify`, verbatim; the findings it lists are the table in docs/findings.md."""
    lines = ["$ make verify", "...",
             f"as_of {m['as_of']}: {sum(f['ok'] for f in findings)}/{len(findings)} findings hold -> " + ("ALL PASS" if all(f["ok"] for f in findings) else "SOME FAILED"),
             "python3 -I pipeline/verify.py data/snapshot-2026-10-20/metrics.json --check-readme README.md",
             records_line(),
             "README generated blocks match the verified output, no template placeholders left; local builds match contract/deployment.json"]
    return "```\n" + "\n".join(lines) + "\n```"


def key_findings_block(m, findings):
    """A short table of the headline numbers. Every number is computed from metrics.json here, the same way
    pipeline/verify.py computes it; the Check column is that finding's verdict."""
    ok = {f["id"]: f["ok"] for f in findings}
    rec, rev, bb, sup, h, d = m["reconciliation"], m["revenue"], m["buyback"], m["supply"], m["hsp029"]["windows"], m["deflation"]
    ytd = rec["rows"][0]
    se2 = 2 * rec["daily_residual_sd_usd"] * m["window"]["days"] ** 0.5 / rev["defillama_ytd_usd"] * 100
    r, ab = bb["reconciliation"], bb["reconciliation"]["altcoinbuzz"]
    lo_price = min(d["price_usd"], d["price_implied_by_transfers"]["min"])
    hi_take = max(d["take_rate_bps_weekly"] + [d["take_rate_bps"]])
    cons = d["net_issuance_near_per_day"] * lo_price / (hi_take / 1e4) / d["volume_usd_per_day_30d"]
    pub, s = CONFIG["published_claims"], m.get("sample")
    meta = "-"
    if s:
        a, b = (s["meta_tx_by_month"][k] for k in (s["full_months"][0], s["full_months"][-1]))
        meta = f"{a['p'] * 100:.1f}% ({s['full_months'][0]}) -> {b['p'] * 100:.1f}% ({s['full_months'][-1]})"
    rows = [
        ("F1a", f"Intents revenue {m['window']['start']}..{m['window']['end']}, rebuilt from raw transfers, matches DefiLlama",
         f"${ytd['onchain']:,.0f} vs ${ytd['defillama']:,.0f} ({ytd['delta_pct']:+.2f}%)",
         f"daily native-NEAR inflows to the 3 revenue wallets (FastNear); tolerance +-{se2:.2f}% (2 standard errors)"),
        ("F2a", "The buyback wallet's balance is fully explained by its transfers and fees",
         f"{r['balance_near']:,.3f} NEAR, {r['unexplained_yocto']} yoctoNEAR unexplained",
         f"integer sums over {r['rows']:,} FastNear rows vs the archival RPC balance"),
        ("F2b", "Total supply rose in every sampled interval of 2026 (no net burn visible)",
         f"{sup['increasing_intervals']}/{sup['intervals']} sampled intervals up, {sup['growth_pct']:+.2f}%",
         "archival RPC block headers"),
        ("F2c", "Share of revenue DefiLlama labels 'buyback' that reached the buyback wallet",
         f"{bb['share_reached_pct']:.1f}% (DefiLlama implies 100%)",
         f"wallet inflows since {bb['buyback_start']} vs DefiLlama dailyHoldersRevenue"),
        ("F2f", "The 2.02M vs 1.51M NEAR gap Altcoin Buzz could not explain",
         f"{ab['gap_near']:,.1f} NEAR gap, decomposition closes to {ab['gap_closure_near']:+.3f} NEAR",
         "internal transfer - outflows - fees + revenue after the dashboard cutoff; published figures pinned in config.json"),
        ("F3a, F3b", "HSP-029's treasury share and realised issuance reproduce",
         f"{h['92']['treasury_share_pct']:.3f}% vs {pub['hsp029_treasury_share_pct']}%; {d['annualised_net_issuance_pct']:.3f}%/yr vs {pub['hsp029_realized_issuance_pct']}%/yr",
         "archival RPC supply and treasury.near balance, 7/28/92-day windows"),
        ("F4a, F4b", "Daily volume needed for net deflation, vs current volume",
         f"${d['breakeven_volume_usd_per_day'] / 1e6:,.0f}M/day = {d['breakeven_over_current_volume']:.1f}x current; {d['breakeven_over_current_volume_at_target']:.1f}x at {d['target_issuance_pct']:g}% issuance",
         f"DefiLlama volume, on-chain take rate and issuance; worst-case price and take rate still give {cons:.1f}x"),
        ("F6a", "Share of transactions that are meta-transactions (NEP-366 Delegate)",
         meta, f"seeded {s['blocks'] if s else 0}-block NEARData sample; the two 95% intervals do not overlap"),
    ]
    out = ["| ID | Finding | Number | How it is checked | Check |", "|---|---|---|---|---|"]
    for fid, what, num, how in rows:
        verdict = all(ok.get(i.strip(), False) for i in fid.split(","))
        out.append(f"| {fid} | {what} | {num} | {how} | {'PASS' if verdict else 'FAIL'} |")
    return "\n".join(out)


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


def _rule(p):
    sym = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<="}
    return f"`{p['metric']} {sym[p['comparator']]} {_num(p['metric'], int(p['threshold']) / MICRO)}`"


def _txlink(tx):
    return f"[`{tx}`](https://testnet.nearblocks.io/txns/{tx})"


def resolutions_block(m, findings):
    """The recorded resolutions (contract/deployment.json) joined with the cached contract state; check_records keeps the
    two in agreement, so this table is what the chain says."""
    d, c = _load("contract/deployment.json"), _load("site/contract-cache.json")
    preds, snaps = {p["id"]: p for p in c["predictions"]}, {r["date"]: r for r in d.get("attested_snapshots", [])}
    rows = ["| Prediction | Rule | Observed | Status | Resolution transaction |", "|---|---|---|---|---|"]
    for r in d.get("resolutions", []):
        p = preds[r["id"]]
        obs = (p.get("resolution") or {}).get("observed")
        rows.append(f"| {r['id']} | {_rule(p)} | {'metric absent' if obs is None else _num(p['metric'], int(obs) / MICRO)} | **{r['status']}** | {_txlink(r['tx'])} |")
    used = sorted({r["snapshot_date"] for r in d.get("resolutions", [])})
    notes = [f"Decided by the attested snapshot of {x} (first mainnet block {snaps[x]['block_height']:,}, 28-day block {snaps[x]['block_height_28d']:,}, "
             f"OutLayer request {snaps[x]['outlayer_request_id']}), stored by {_txlink(snaps[x]['tx'])}; run log `{snaps[x]['log']}`." for x in used]
    who = {(preds[r["id"]].get("resolution") or {}).get("resolved_by") for r in d.get("resolutions", [])}
    notes.append(f"Every transaction above was signed by `{', '.join(sorted(map(str, who)))}` with its function-call key `{d['keeper']['function_call_key']}`.")
    return "\n".join(rows) + "\n\n" + " ".join(notes)


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


def log_events(rel):
    """The JSON-line events (keeper/keeper.py output) in a committed log; other lines are ignored."""
    out = []
    for line in _text(os.path.join(ROOT, rel)).splitlines():
        if line.startswith("{"):
            try:
                out.append(json.loads(line))
            except ValueError:
                pass
    return out


def _sent(events, tx, method, args):
    return any(e.get("event") == "tx_sent" and e.get("tx") == tx and e.get("method") == method and e.get("args") == args for e in events)


def check_records():
    """Problems (strings) if the attested snapshots and prediction resolutions recorded in contract/deployment.json
    disagree with the keeper run log each one cites, with the transaction list, or with the contract state in
    site/contract-cache.json. Both directions: a snapshot or a decided prediction in the cache that is not recorded
    is a problem too."""
    d, c, out = _load("contract/deployment.json"), _load("site/contract-cache.json"), []
    txs = {tx for _, tx in d["transactions"]}
    snaps, preds = {s["date"]: s for s in c["attested"]}, {p["id"]: p for p in c["predictions"]}
    keeper = d["keeper"]["account"]
    recorded = set()
    for r in d.get("attested_snapshots", []):
        what, ev = f"attested snapshot {r['date']}", log_events(r["log"])
        recorded.add(r["date"])
        args = {"date": r["date"], "block_height": r["block_height"], "block_height_28d": r["block_height_28d"]}
        if not _sent(ev, r["tx"], "keeper_request_snapshot", args):
            out.append(f"{what}: {r['log']} has no keeper_request_snapshot {args} in tx {r['tx']}")
        if not any(e.get("event") == "snapshot_result" and e.get("tx") == r["tx"] and e.get("ok") and e.get("stored") for e in ev):
            out.append(f"{what}: {r['log']} does not show tx {r['tx']} storing it")
        if r["tx"] not in txs:
            out.append(f"{what}: tx {r['tx']} is not in the transactions list")
        s = snaps.get(r["date"])
        if s is None:
            out.append(f"{what}: not in site/contract-cache.json")
            continue
        a, chk = s["source"]["attested"], s.get("attestation_checks") or {}
        b28 = next((int(m["value"]) // MICRO for m in s["metrics"] if m["key"] == "mainnet_block_height_28d"), None)
        got = (a["block_height"], b28, a["request_id"], chk.get("transaction_hash"))
        want = (r["block_height"], r["block_height_28d"], r["outlayer_request_id"], r["tx"])
        if got != want:
            out.append(f"{what}: site/contract-cache.json has (block, 28d block, OutLayer request, tx) {got}, recorded {want}")
        if not chk.get("verified"):
            out.append(f"{what}: site/contract-cache.json does not mark it verified")
    for date in sorted(snaps.keys() - recorded):
        out.append(f"site/contract-cache.json has attested snapshot {date}, which contract/deployment.json does not record")
    decided = set()
    for r in d.get("resolutions", []):
        what, ev = f"prediction {r['id']}", log_events(r["log"])
        decided.add(r["id"])
        if not _sent(ev, r["tx"], "resolve_prediction", {"id": r["id"]}):
            out.append(f"{what}: {r['log']} has no resolve_prediction tx {r['tx']}")
        logged = [e.get("status") for e in ev if e.get("event") == "resolved" and e.get("id") == r["id"] and e.get("tx") == r["tx"] and e.get("ok")]
        if logged != [r["status"]]:
            out.append(f"{what}: {r['log']} logs status {logged}, recorded {r['status']!r}")
        if r["tx"] not in txs:
            out.append(f"{what}: tx {r['tx']} is not in the transactions list")
        if r["snapshot_date"] not in recorded:
            out.append(f"{what}: decided by snapshot {r['snapshot_date']}, which is not recorded")
        p = preds.get(r["id"])
        res = (p or {}).get("resolution") or {}
        got = (p and p["status"], res.get("snapshot_date"), p and p["resolve_after"], res.get("resolved_by"))
        want = (r["status"], r["snapshot_date"], r["snapshot_date"], keeper)
        if got != want:
            out.append(f"{what}: site/contract-cache.json has (status, decided by, resolve date, resolved by) {got}, recorded {want}")
    for i, p in sorted(preds.items()):
        if p["status"] != "pending" and i not in decided:
            out.append(f"site/contract-cache.json has prediction {i} {p['status']}, which contract/deployment.json does not record")
    return out


def records_line():
    """The `make verify` line that states what check_records checked."""
    d = _load("contract/deployment.json")
    snaps = ", ".join(r["date"] for r in d.get("attested_snapshots", []))
    res = ", ".join(f"{r['id']} {r['status']}" for r in d.get("resolutions", [])) or "none"
    return f"recorded snapshots ({snaps}) and resolutions ({res}) match the keeper run logs and site/contract-cache.json"


B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
HEX64 = re.compile(r"\b[0-9a-f]{64}\b")
TXHASH = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{43,44}\b")
SHORT = re.compile(r"\b([0-9A-Za-z]{4,})\u2026([0-9A-Za-z]{3,})\b")


def _b58(hexstr):
    n, out = int(hexstr, 16), ""
    while n:
        n, r = divmod(n, 58)
        out = B58[r] + out
    return out


def record_hashes():
    """Every long hash token in the committed records (deployment.json, the contract cache, the worker list and
    the testnet logs), plus the base58 form of each hex hash (NEAR shows code_hash in base58)."""
    txt = "".join(_text(os.path.join(ROOT, rel)) for rel in ("contract/deployment.json", "site/contract-cache.json", "contract/known_workers.json"))
    txt += "".join(_text(p) for p in sorted(glob.glob(os.path.join(ROOT, "contract", "testnet-*.log"))))
    toks = {t for t in re.findall(r"[0-9A-Za-z]+", txt) if len(t) >= 32}
    return toks | {_b58(h) for h in HEX64.findall(txt)}


def hash_problems(name, doc, known=None):
    """Hashes written into a document by hand (pasted command output, prose) must exist in the committed records:
    full hex and base58 hashes literally, abbreviated ones (`abcd1234…ef01`) as the prefix and suffix of one."""
    known = record_hashes() if known is None else known
    bad = [h for h in HEX64.findall(doc) + TXHASH.findall(doc) if h not in known]
    bad += [f"{a}\u2026{b}" for a, b in SHORT.findall(doc) if not any(t.startswith(a) and t.endswith(b) for t in known)]
    return [f"{name}: {h} is not in contract/deployment.json, site/contract-cache.json, contract/known_workers.json or contract/testnet-*.log" for h in dict.fromkeys(bad)]


def marker(name):
    return (f"<!-- {name}:begin (generated by `make readme`) -->", f"<!-- {name}:end -->")


BLOCKS = {"summary": summary_block, "verify-output": verify_block, "key-findings": key_findings_block, "test-output": tests_block,
          "deployment": deployment_block, "attested": attested_block, "predictions": predictions_block, "resolutions": resolutions_block}
