"""Compute metrics.json from a raw data directory produced by fetch.py (or a committed snapshot).

    python3 pipeline/compute.py --data data/snapshot-2026-10-20 --out data/snapshot-2026-10-20/metrics.json

Pure function of the input directory: no network, no clock. Standard library only.
"""
import argparse, collections, datetime, json, math, os, statistics, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import CONFIG, YOCTO, day_of, sha256_file, load_json
import ledger, neardata, stats

W3 = CONFIG["revenue_wallets"]
BUYBACK = CONFIG["buyback_wallet"]
MICRO = 10 ** 6
S_TOP = CONFIG["neardata_sample"]["top_channels"]


def llama_series(path):
    d = load_json(path)
    return {day_of(t): float(v) for t, v in d["totalDataChart"]}, d


def load_transfers(data):
    rows = []
    for w in W3:
        with open(os.path.join(data, f"transfers_{w}.jsonl")) as f:
            for line in f:
                r = json.loads(line)
                r["_day"] = day_of(int(r["block_timestamp"]) / 1e9)
                rows.append(r)
    return rows


def pearson(xs, ys):
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    num = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    den = math.sqrt(sum((a - mx) ** 2 for a in xs) * sum((b - my) ** 2 for b in ys))
    return num / den


def day_range(a, b):
    d, end = datetime.date.fromisoformat(a), datetime.date.fromisoformat(b)
    while d <= end:
        yield d.isoformat()
        d += datetime.timedelta(days=1)


def shift(day, n):
    return (datetime.date.fromisoformat(day) + datetime.timedelta(days=n)).isoformat()


def check_integrity(data, man):
    """Every raw file must hash to what fetch.py recorded, so a snapshot cannot be edited silently."""
    for name, want in man["sha256"].items():
        got = sha256_file(os.path.join(data, name))
        if got != want:
            raise SystemExit(f"integrity failure: {name} sha256 {got} != manifest {want}")


def _match_day(led, near_value):
    """UTC day whose end-of-day cumulative external inflow is closest to a published NEAR figure."""
    best = min(led["timeline"], key=lambda d: abs(led["timeline"][d]["external_in"] / YOCTO - near_value))
    return best, led["timeline"][best]["external_in"] / YOCTO - near_value


def dashboard_definition_near(rows, wallet, cutoff):
    """Our reading of revenue.near.org's per-wallet 'Total NEAR': all native NEAR received from accounts outside
    the three revenue wallets up to the end of `cutoff`, excluding Sputnik DAO proposal bonds (AttachedDeposit,
    refunded when the proposal resolves)."""
    return sum(int(r["amount"]) for r in rows if r["account_id"] == wallet and r["asset_id"] == "native:near"
               and int(r["amount"]) > 0 and r["other_account_id"] not in W3 and r["transfer_type"] != "AttachedDeposit"
               and day_of(int(r["block_timestamp"]) / 1e9) <= cutoff) / YOCTO


def buyback_reconciliation(rows, led, win_end, pre_rows=None):
    """Balance = external revenue + internal transfers - outflows + fees, and the published figures that match each part."""
    pub = CONFIG["published_figures"]
    L = led[BUYBACK]
    n = ledger.to_near
    steps = L["nontransfer_steps"]
    out = {"wallet": BUYBACK, "balance_near": n(L["balance"]), "opening_near": n(L["opening"]),
           "external_in_near": n(L["external_in"]), "internal_in_near": n(L["internal_in"]),
           "internal_by_source_near": {k: n(v) for k, v in L["internal_by_source"].items()},
           "out_near": n(L["out"]), "out_by_destination_near": {k: n(v) for k, v in L["out_by_destination"].items()},
           "smallest_outflow_near": min((-int(r["amount"]) for r in rows if r["account_id"] == BUYBACK and r["asset_id"] == "native:near" and int(r["amount"]) < 0), default=0) / YOCTO,
           "nontransfer_near": n(L["nontransfer_total"]), "nontransfer_steps": len(steps),
           "nontransfer_negative_steps": sum(1 for s in steps if s["yocto"] < 0),
           "largest_nontransfer_step_near": max((abs(s["yocto"]) for s in steps), default=0) / YOCTO,
           "tail_after_last_row_yocto": L["tail_after_last_row"], "residual_near": n(L["residual"]), "unexplained_yocto": L["unexplained"],
           "rows": L["rows"]}
    # revenue.near.org "Wallet Breakdown (All-time)": compare each wallet with our external inflow at the dashboard cutoff
    rd = pub["revenue_dashboard"]
    cutoff = shift(rd["fetched_on"], -1)          # last complete UTC day before the fetch
    half = 0.5 * 10 ** -rd["display_decimals"]
    dash = {}
    for w, v in rd["wallet_total_near"].items():
        Lw = led[w]
        ext_c = ledger.cumulative_at(Lw, cutoff, "external_in") / YOCTO
        tl = [Lw["timeline"][d] for d in sorted(Lw["timeline"]) if d <= cutoff]
        eob_c = tl[-1]["eob"] / YOCTO if tl else None
        day, diff = _match_day(Lw, v - n(Lw["opening"]))
        dash[w] = {"dashboard_near": v, "external_in_to_cutoff_near": ext_c, "opening_near": n(Lw["opening"]),
                   "bracket_low_near": ext_c, "bracket_high_near": ext_c + n(Lw["opening"]),
                   "in_bracket": ext_c - half <= v <= ext_c + n(Lw["opening"]) + half,
                   "balance_at_cutoff_near": eob_c, "diff_vs_external_near": ext_c - v,
                   "best_matching_day": day, "best_matching_diff_near": diff, "history_complete": Lw["opening"] == 0}
        if pre_rows is not None:
            allrows = pre_rows + rows
            full = ledger.wallet_ledger(allrows, w, W3, int(Lw["balance"]))
            dash[w].update({"all_time_definition_near": dashboard_definition_near(allrows, w, cutoff),
                            "all_time_diff_near": dashboard_definition_near(allrows, w, cutoff) - v,
                            "all_time_ledger_unexplained_yocto": full["unexplained"], "all_time_opening_yocto": full["opening"],
                            "all_time_rows": full["rows"], "all_time_nontransfer_near": full["nontransfer_total"] / YOCTO})
    out["dashboard"] = {"url": rd["url"], "fetched_on": rd["fetched_on"], "cutoff_day": cutoff, "rounding_near": half, "wallets": dash,
                        "full_history": pre_rows is not None, "claim_text": rd["claim_text"]}
    # Altcoin Buzz 2026-10-19: NearBlocks balance at a block vs the dashboard figure the same day
    ab = pub["altcoinbuzz_2026_10_19"]
    B = ab["nearblocks_block"]
    ours_b = ledger.balance_at(L, B) / YOCTO
    d_day, d_diff = _match_day(L, ab["dashboard_buyback_near"])
    comp = {k: ledger.cumulative_at_height(rows, BUYBACK, W3, B, k) / YOCTO for k in ("external_in", "internal_in", "out")}
    fees_b = sum(s["yocto"] for s in steps if s["height"] <= B) / YOCTO
    ext_d = ledger.cumulative_at(L, d_day, "external_in") / YOCTO
    gap = ab["nearblocks_balance_near"] - ab["dashboard_buyback_near"]
    parts = {"internal_in_near": comp["internal_in"], "outflows_near": -comp["out"], "fees_near": fees_b,
             "revenue_after_dashboard_cutoff_near": comp["external_in"] - ext_d}
    out["altcoinbuzz"] = {"url": ab["url"], "published": ab["published"], "nearblocks_block": B, "nearblocks_balance_near": ab["nearblocks_balance_near"],
                          "ours_balance_at_block_near": ours_b, "nearblocks_diff_near": ours_b - ab["nearblocks_balance_near"],
                          "nearblocks_rounding_near": 0.5 * 10 ** -ab["nearblocks_decimals"], "dashboard_buyback_near": ab["dashboard_buyback_near"],
                          "dashboard_matching_day": d_day, "dashboard_diff_near": d_diff, "dashboard_rounding_near": 0.5 * 10 ** -ab["dashboard_decimals"],
                          "gap_near": gap, "gap_parts": parts, "gap_closure_near": gap - sum(parts.values())}
    return out


def load_m2(m2, man_path):
    """The M2 snapshot: pre-year wallet history + the NEARData block sample. Bound to one main snapshot by hash."""
    man2 = load_json(os.path.join(m2, "manifest.json"))
    check_integrity(m2, man2)
    if man2["main_manifest_sha256"] != sha256_file(man_path):
        raise SystemExit(f"{m2} was fetched against a different main snapshot (manifest hash mismatch)")
    pre = []
    for w in W3:
        with open(os.path.join(m2, f"transfers_pre_{w}.jsonl")) as f:
            pre += [json.loads(l) for l in f]
    plan = load_json(os.path.join(m2, "neardata_plan.json"))
    with open(os.path.join(m2, "neardata_sample.jsonl")) as f:
        sample = [json.loads(l) for l in f]
    return {"manifest": man2, "pre_rows": pre, "plan": plan, "sample": sample}


def sample_estimates(M2):
    """Channel mix (dip4 token_diff `referral`) and meta-transaction share per month, from the NEARData block sample."""
    P, smp = M2["plan"], M2["sample"]
    want = neardata.plan(P["seed"], P["strata"], P["blocks_per_stratum"])
    got = collections.defaultdict(list)
    month_of = {}
    for st in P["strata"]:
        for h in want[st["month"]]:
            month_of[h] = st["month"]
    order = [h for m in sorted(want) for h in want[m]]
    if [x["requested"] for x in smp] != order:
        raise SystemExit("neardata sample does not match the seeded plan (heights or order differ)")
    skipped = sum(1 for x in smp if x["height"] != x["requested"])
    bad_skip = [x["requested"] for x in smp if not 0 <= x["height"] - x["requested"] < 20]
    off_month = sum(1 for x in smp if day_of(int(x["timestamp_ns"]) / 1e9)[:7] != month_of[x["requested"]])
    for x in smp:
        got[month_of[x["requested"]]].append(x)
    size = {st["month"]: st["end_height"] - st["start_height"] for st in P["strata"]}

    per_block = {m: [] for m in want}
    names = collections.Counter()
    for m, xs in got.items():
        for x in xs:
            sw = neardata.referred_swaps(x)
            tx, dl = neardata.delegate_counts(x)
            c = collections.Counter(s["referral"] for s in sw)
            names.update(c)
            per_block[m].append({"tx": tx, "delegate": dl, "swaps": len(sw), "by_ref": c,
                                 "no_fee": sum(1 for s in sw if not s["has_fee"]),
                                 "executed": neardata.event_counts(x).get("dip4:intents_executed", 0)})
    top = [k for k, _ in names.most_common(S_TOP)]
    channels = top + ["other"]
    lab = lambda r: r if r in top else "other"
    months = sorted(want)
    mix = {}
    for m in months:
        B = per_block[m]
        ms = [b["swaps"] for b in B]
        row = {"blocks": len(B), "referred_swaps": sum(ms), "intents_executed": sum(b["executed"] for b in B), "channels": {}}
        for ch in channels:
            ys = [sum(v for k, v in b["by_ref"].items() if lab(k) == ch) for b in B]
            row["channels"][ch] = stats.ratio_share(ys, ms, size[m])
        mix[m] = row
    pooled = {}
    for ch in channels:
        pooled[ch] = stats.stratified_ratio([([sum(v for k, v in b["by_ref"].items() if lab(k) == ch) for b in per_block[m]],
                                              [b["swaps"] for b in per_block[m]], size[m]) for m in months])
    meta = {m: stats.ratio_share([b["delegate"] for b in per_block[m]], [b["tx"] for b in per_block[m]], size[m]) for m in months}
    meta_pooled = stats.stratified_ratio([([b["delegate"] for b in per_block[m]], [b["tx"] for b in per_block[m]], size[m]) for m in months])
    full = [m for m in months if m < max(months)]                         # the last stratum is a partial month
    return {"source": P["source"], "seed": P["seed"], "blocks_per_stratum": P["blocks_per_stratum"], "strata": P["strata"],
            "blocks": len(smp), "skipped_heights_replaced": skipped, "bad_replacements": bad_skip, "blocks_outside_their_month": off_month,
            "plan_matches": True, "fetch": {k: M2["manifest"].get(k) for k in ("fetched_unix", "runtime_s", "neardata_http_429")},
            "channels": channels, "referral_names_seen": len(names), "referral_counts": dict(names.most_common()),
            "referred_swaps_without_fee": sum(b["no_fee"] for m in months for b in per_block[m]),
            "channel_mix_by_month": mix, "channel_mix_pooled": pooled,
            "meta_tx_by_month": meta, "meta_tx_pooled": meta_pooled, "full_months": full}


def compute(data, m2=None):
    man = load_json(os.path.join(data, "manifest.json"))
    check_integrity(data, man)
    M2 = load_m2(m2, os.path.join(data, "manifest.json")) if m2 else None
    head_ns = int(man["head_timestamp_ns"])
    rev_llama, rev_raw = llama_series(os.path.join(data, "llama_revenue.json"))
    hold_llama, _ = llama_series(os.path.join(data, "llama_holders_revenue.json"))
    vol_llama, vol_raw = llama_series(os.path.join(data, "llama_volume.json"))
    rows = [r for r in load_transfers(data) if int(r["block_timestamp"]) <= head_ns]
    y0 = man["year_start"]
    win_end = max(d for d in rev_llama)          # last complete day DefiLlama publishes
    days = [d for d in day_range(y0, win_end)]
    d30 = days[-30:]

    # ---- on-chain captured revenue: native NEAR arriving at the three wallets from outside the three wallets
    is_near = lambda r: r["asset_id"] == "native:near"
    ext_in = [r for r in rows if is_near(r) and int(r["amount"]) > 0 and r["other_account_id"] not in W3]
    int_in = [r for r in rows if is_near(r) and int(r["amount"]) > 0 and r["other_account_id"] in W3]
    day_usd = collections.defaultdict(float)
    per_wallet = {w: {"near": 0.0, "usd": 0.0, "n": 0} for w in W3}
    for r in ext_in:
        if r["_day"] <= win_end:
            day_usd[r["_day"]] += r["usd_amount"] or 0
            pw = per_wallet[r["account_id"]]
            pw["near"] += r["human_amount"]; pw["usd"] += r["usd_amount"] or 0; pw["n"] += 1
    onc = [day_usd.get(d, 0.0) for d in days]
    llm = [rev_llama.get(d, 0.0) for d in days]
    ytd_on, ytd_ll = sum(onc), sum(llm)
    on30, ll30 = sum(day_usd.get(d, 0.0) for d in d30), sum(rev_llama.get(d, 0.0) for d in d30)
    vol30 = sum(vol_llama.get(d, 0.0) for d in d30)
    vol_ytd = sum(vol_llama.get(d, 0.0) for d in days)

    # lag test: correlation of on-chain day t with DefiLlama day t+lag (lag 0 should be the best alignment)
    lagr = {}
    for lag in range(-3, 4):
        pairs = [(day_usd.get(d, 0.0), rev_llama[shift(d, lag)]) for d in days if shift(d, lag) in rev_llama]
        lagr[str(lag)] = pearson([p[0] for p in pairs], [p[1] for p in pairs])
    resid = [a - b for a, b in zip(onc, llm)]
    sd = statistics.pstdev(resid)
    bias_z = sum(resid) / (sd * math.sqrt(len(resid))) if sd else 0.0   # z of the summed daily error vs zero-mean noise

    months = sorted({d[:7] for d in days})
    monthly = []
    for m in months:
        a = sum(day_usd.get(d, 0.0) for d in days if d.startswith(m)); b = sum(rev_llama.get(d, 0.0) for d in days if d.startswith(m))
        monthly.append({"month": m, "onchain_usd": a, "defillama_usd": b, "delta_pct": (a / b - 1) * 100 if b else None})

    # ---- buyback wallet
    bal = load_json(os.path.join(data, "rpc_balances.json"))
    bb_rows = [r for r in rows if r["account_id"] == BUYBACK and is_near(r)]
    bb_in_ext = [r for r in bb_rows if int(r["amount"]) > 0 and r["other_account_id"] not in W3]
    bb_in_int = [r for r in bb_rows if int(r["amount"]) > 0 and r["other_account_id"] in W3]
    bb_out = [r for r in bb_rows if int(r["amount"]) < 0]
    bb_last_eob = int(max(bb_rows, key=lambda r: int(r["block_timestamp"]))["end_of_block_balance"]) / YOCTO
    bb_monthly = collections.defaultdict(lambda: {"external_near": 0.0, "internal_near": 0.0})
    for r in bb_in_ext: bb_monthly[r["_day"][:7]]["external_near"] += r["human_amount"]
    for r in bb_in_int: bb_monthly[r["_day"][:7]]["internal_near"] += r["human_amount"]
    hold_start = min((d for d, v in hold_llama.items() if v > 0), default=None)
    in_window = lambda r: hold_start and hold_start <= r["_day"] <= win_end
    rev_near_since = sum(r["human_amount"] for r in ext_in if in_window(r))              # all three wallets
    reached_bb_near = sum(r["human_amount"] for r in bb_in_ext + bb_in_int if in_window(r))  # external + internal hops
    rev_usd_since = sum(r["usd_amount"] or 0 for r in ext_in if in_window(r))
    hold_ytd = sum(v for d, v in hold_llama.items() if hold_start and hold_start <= d <= win_end)
    other = {}
    for w in W3:
        wr = [r for r in rows if r["account_id"] == w and is_near(r)]
        outs = [r for r in wr if int(r["amount"]) < 0]
        first = min(wr, key=lambda r: int(r["block_timestamp"]))
        opening = int(first["start_of_block_balance"] or 0) / YOCTO
        net = sum(r["human_amount"] for r in wr)
        other[w] = {"balance_near": int(bal["amount_yocto"][w]) / YOCTO, "opening_near": opening, "net_transfers_near": net,
                    "balance_residual_near": int(bal["amount_yocto"][w]) / YOCTO - opening - net, "outflow_count": len(outs),
                    "outflow_near": -sum(r["human_amount"] for r in outs),
                    "outflow_top_counterparties": collections.Counter(r["other_account_id"][:16] + ("..." if len(r["other_account_id"]) > 16 else "") for r in outs).most_common(3)}
    # ---- buyback wallet, exact ledger (integer yocto) and the published figures it is compared with
    led = {w: ledger.wallet_ledger(rows, w, W3, int(bal["amount_yocto"][w])) for w in W3}
    bb_rec = buyback_reconciliation(rows, led, win_end, M2["pre_rows"] if M2 else None)
    non_near_usd = sum(r["usd_amount"] or 0 for r in rows if not is_near(r) and int(r["amount"]) > 0 and r["other_account_id"] not in W3 and r["_day"] <= win_end)

    # ---- supply from archival block headers
    S = load_json(os.path.join(data, "rpc_supply_samples.json"))
    sup = [int(s["total_supply"]) / YOCTO for s in S]
    ts = [int(s["timestamp_ns"]) / 1e9 for s in S]
    inc = sum(1 for a, b in zip(sup, sup[1:]) if b > a)
    supply = {"samples": len(S), "first_near": sup[0], "last_near": sup[-1], "growth_near": sup[-1] - sup[0],
              "growth_pct": (sup[-1] / sup[0] - 1) * 100, "increasing_intervals": inc, "intervals": len(S) - 1,
              "annualised_growth_pct": ((sup[-1] / sup[0]) ** (365 * 86400 / (ts[-1] - ts[0])) - 1) * 100,
              "series": [{"height": s["height"], "date": day_of(t), "total_supply_near": v} for s, t, v in zip(S, ts, sup)]}

    # ---- HSP-029 method: treasury share of supply growth, per window
    H = load_json(os.path.join(data, "rpc_hsp029.json"))
    head = H["0"]
    windows = {}
    for k, v in H.items():
        if k == "0": continue
        ds = (int(head["timestamp_ns"]) - int(v["timestamp_ns"])) / 1e9
        dsup = (int(head["total_supply"]) - int(v["total_supply"])) / YOCTO
        dtre = (int(head["treasury_yocto"]) - int(v["treasury_yocto"])) / YOCTO
        windows[k] = {"days": ds / 86400, "supply_growth_near": dsup, "treasury_inflow_near": dtre,
                      "treasury_share_pct": dtre / dsup * 100, "net_issuance_near_per_day": dsup / (ds / 86400)}
    ref = windows["28"]
    net_per_day = ref["net_issuance_near_per_day"]
    supply_now = int(head["total_supply"]) / YOCTO

    # ---- deflation break-even
    price_doc = load_json(os.path.join(data, "price.json"))["coins"]["coingecko:near"]
    price = float(price_doc["price"])
    last7 = [r for r in ext_in if r["_day"] >= shift(win_end, -6) and r["human_amount"] > 0 and r["usd_amount"]]
    implied = [r["usd_amount"] / r["human_amount"] for r in last7]
    take = on30 / vol30                                        # captured revenue / routed volume (30d)
    wk = [d30[i:i + 7] for i in range(2, 30, 7)]               # four whole weeks inside the 30d window
    take_weekly = [sum(day_usd.get(d, 0.0) for d in w) / sum(vol_llama.get(d, 0.0) for d in w) * 1e4 for w in wk]
    cap, target = CONFIG["protocol_issuance_cap_pct"], CONFIG["proposed_issuance_target_pct"]
    fee_burn_per_day = supply_now * cap / 100 / 365 - net_per_day   # what the 2.5% cap would mint minus what supply actually grew
    net_at_target = supply_now * target / 100 / 365 - fee_burn_per_day
    breakeven = lambda n: n * price / take
    vol_per_day = vol30 / len(d30)
    deflation = {"price_usd": price, "price_implied_by_transfers": {"n": len(implied), "median": statistics.median(implied), "min": min(implied), "max": max(implied)},
                 "take_rate_bps": take * 1e4, "take_rate_bps_weekly": take_weekly, "net_issuance_near_per_day": net_per_day,
                 "annualised_net_issuance_pct": net_per_day * 365 / supply_now * 100, "net_issuance_usd_per_day": net_per_day * price,
                 "captured_revenue_usd_per_day_30d": on30 / len(d30), "volume_usd_per_day_30d": vol_per_day,
                 "breakeven_volume_usd_per_day": breakeven(net_per_day), "breakeven_over_current_volume": breakeven(net_per_day) / vol_per_day,
                 "issuance_cap_pct": cap, "target_issuance_pct": target, "fee_burn_near_per_day_estimate": fee_burn_per_day,
                 "net_issuance_at_target_near_per_day": net_at_target, "breakeven_volume_usd_per_day_at_target": breakeven(net_at_target),
                 "breakeven_over_current_volume_at_target": breakeven(net_at_target) / vol_per_day}

    # ---- on-chain snapshot (what the oracle contract stores): integer micro-units, keyed by as_of date
    sn = {"supply_total_near": supply_now, "buyback_wallet_near": other[BUYBACK]["balance_near"], "revenue_ytd_usd": ytd_on,
          "revenue_30d_usd": on30, "net_issuance_near_per_day": net_per_day,
          "breakeven_volume_usd_per_day": deflation["breakeven_volume_usd_per_day"],
          "breakeven_over_current_volume": deflation["breakeven_over_current_volume"], "take_rate_bps": take * 1e4}
    snapshot = {"date": man["as_of"], "metrics": {k: round(v * MICRO) for k, v in sn.items()}, "unit": "value x 1e6 (micro-units)"}

    preds = []
    for p in load_json(os.path.join(os.path.dirname(os.path.abspath(__file__)), "predictions.json"))["predictions"]:
        p = dict(p)
        if isinstance(p["threshold"], dict):
            p["threshold"] = sn[p["threshold"]["from_metric"]]
        p["threshold_micro"] = round(p["threshold"] * MICRO)
        p["current_value"] = sn[p["metric"]]
        p["status"] = "pending (resolved only on-chain, by anyone, after resolve_after)"
        preds.append(p)

    reconciliation = [
        {"item": f"Captured revenue {y0}..{win_end}", "onchain": ytd_on, "defillama": ytd_ll, "delta_pct": (ytd_on / ytd_ll - 1) * 100, "unit": "USD"},
        {"item": f"Captured revenue last 30d (to {win_end})", "onchain": on30, "defillama": ll30, "delta_pct": (on30 / ll30 - 1) * 100, "unit": "USD"},
        {"item": f"DefiLlama 'buyback' (HoldersRevenue) {hold_start}..{win_end} vs all captured revenue same days", "onchain": rev_usd_since, "defillama": hold_ytd,
         "delta_pct": (rev_usd_since / hold_ytd - 1) * 100 if hold_ytd else None, "unit": "USD"},
        {"item": f"Share of that revenue (in NEAR) that reached the buyback wallet {hold_start}..{win_end}", "onchain": reached_bb_near / rev_near_since * 100,
         "defillama": 100.0, "delta_pct": None, "unit": "% of NEAR (DefiLlama labels all of it as buyback)"},
        {"item": "Daily Pearson r (on-chain vs DefiLlama revenue)", "onchain": lagr["0"], "defillama": None, "delta_pct": None, "unit": f"r over {len(days)} days"},
    ]
    return {
        "as_of": man["as_of"], "head_height": man["head_height"], "window": {"start": y0, "end": win_end, "days": len(days), "last30_start": d30[0]},
        "revenue": {"ytd_usd": ytd_on, "ytd_near": sum(w["near"] for w in per_wallet.values()), "last30d_usd": on30, "per_wallet": per_wallet,
                    "non_near_inflows_usd_excluded": non_near_usd, "defillama_ytd_usd": ytd_ll, "defillama_last30d_usd": ll30,
                    "defillama_total30d_field_usd": rev_raw.get("total30d"), "volume_ytd_usd": vol_ytd, "volume_last30d_usd": vol30,
                    "daily": [{"date": d, "onchain_usd": a, "defillama_usd": b} for d, a, b in zip(days, onc, llm)]},
        "reconciliation": {"rows": reconciliation, "monthly": monthly, "pearson_by_lag": lagr, "pearson_lag0": lagr["0"], "bias_z": bias_z,
                           "daily_residual_sd_usd": sd, "defillama_methodology_holders": "NEAR buyback text quoted from DefiLlama methodology: 'used to buy back $NEAR on the open market (not burned)'"},
        "buyback": {"wallet": BUYBACK, "balance_near_rpc": other[BUYBACK]["balance_near"], "balance_near_from_last_transfer": bb_last_eob,
                    "inflow_external_near": sum(r["human_amount"] for r in bb_in_ext), "inflow_external_usd": sum(r["usd_amount"] or 0 for r in bb_in_ext),
                    "inflow_internal_near": sum(r["human_amount"] for r in bb_in_int),
                    "revenue_near_since_buyback_start": rev_near_since, "reached_buyback_wallet_near_since_start": reached_bb_near,
                    "share_reached_pct": reached_bb_near / rev_near_since * 100, "buyback_start": hold_start, "inflow_internal_n": len(bb_in_int),
                    "largest_internal_hop": max(((r["human_amount"], r["_day"], r["other_account_id"]) for r in bb_in_int), default=None),
                    "outflow_count": len(bb_out), "outflow_near": -sum(r["human_amount"] for r in bb_out),
                    "monthly_inflow": dict(sorted(bb_monthly.items())), "all_wallets": other, "reconciliation": bb_rec},
        "supply": supply, "hsp029": {"windows": windows, "reference_window_days": 28},
        "deflation": deflation, "snapshot": snapshot, "predictions": preds,
        "sample": sample_estimates(M2) if M2 else None,
        "provenance": {"manifest": man, "m2_manifest": M2["manifest"] if M2 else None},
    }


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--data", required=True); ap.add_argument("--out", required=True)
    ap.add_argument("--m2", help="M2 snapshot dir (pre-year wallet history + NEARData block sample)")
    a = ap.parse_args()
    m = compute(a.data, a.m2)
    with open(a.out, "w") as f:
        json.dump(m, f, indent=1)
    print(f"wrote {a.out}: as_of {m['as_of']}, revenue {m['window']['start']}..{m['window']['end']} = ${m['revenue']['ytd_usd']:,.0f} "
          f"(DefiLlama ${m['revenue']['defillama_ytd_usd']:,.0f}), buyback wallet {m['buyback']['balance_near_rpc']:,.0f} NEAR")


if __name__ == "__main__":
    main()
