"""Compute metrics.json from a raw data directory produced by fetch.py (or a committed snapshot).

    python3 pipeline/compute.py --data data/snapshot-2026-10-20 --out data/snapshot-2026-10-20/metrics.json

Pure function of the input directory: no network, no clock. Standard library only.
"""
import argparse, collections, datetime, json, math, os, statistics, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import CONFIG, YOCTO, day_of, sha256_file, load_json

W3 = CONFIG["revenue_wallets"]
BUYBACK = CONFIG["buyback_wallet"]
MICRO = 10 ** 6


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


def compute(data):
    man = load_json(os.path.join(data, "manifest.json"))
    check_integrity(data, man)
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
                    "monthly_inflow": dict(sorted(bb_monthly.items())), "all_wallets": other},
        "supply": supply, "hsp029": {"windows": windows, "reference_window_days": 28},
        "deflation": deflation, "snapshot": snapshot, "predictions": preds,
        "provenance": {"manifest": man},
    }


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--data", required=True); ap.add_argument("--out", required=True)
    a = ap.parse_args()
    m = compute(a.data)
    with open(a.out, "w") as f:
        json.dump(m, f, indent=1)
    print(f"wrote {a.out}: as_of {m['as_of']}, revenue {m['window']['start']}..{m['window']['end']} = ${m['revenue']['ytd_usd']:,.0f} "
          f"(DefiLlama ${m['revenue']['defillama_ytd_usd']:,.0f}), buyback wallet {m['buyback']['balance_near_rpc']:,.0f} NEAR")


if __name__ == "__main__":
    main()
