"""Generated README blocks besides the findings table: cross-source reconciliation, channel mix, meta-transactions.

Each block is a pure function of metrics.json (plus the published figures pinned in config.json).
update_readme.py writes them between markers; verify.py --check-readme fails if any block is stale.
"""
from common import CONFIG


def _usd(v):
    return f"${v:,.0f}"


def _pct(p, lo=None, hi=None, d=0):
    if p is None:
        return "-"
    s = f"{p*100:.{d}f}%"
    return s + (f" [{lo*100:.{d}f}-{hi*100:.{d}f}]" if lo is not None else "")


def sources_table(m):
    pub = CONFIG["published_figures"]
    ab, rd = pub["altcoinbuzz_2026_10_19"], pub["revenue_dashboard"]
    hsp = CONFIG["published_claims"]
    rec, rows = m["reconciliation"], m["reconciliation"]["rows"]
    bb = m["buyback"]; r = bb["reconciliation"]; W = r["dashboard"]["wallets"]
    h92 = m["hsp029"]["windows"]["92"]["treasury_share_pct"]
    d = m["deflation"]
    win = m["window"]
    T = [("Item", "On-chain (this repo)", "DefiLlama", "revenue.near.org", "HSP-029 text", "Other published", "Finding")]
    T.append((f"Captured revenue {win['start']}..{win['end']}", _usd(rows[0]["onchain"]), f"{_usd(rows[0]['defillama'])} ({rows[0]['delta_pct']:+.2f}%)",
              "not read (chart rendered client-side)", "-", "-", "F1a"))
    T.append((f"Captured revenue, 30 days to {win['end']}", _usd(rows[1]["onchain"]), f"{_usd(rows[1]['defillama'])} ({rows[1]['delta_pct']:+.2f}%)", "not read", "-",
              f"Altcoin Buzz: ${ab['defillama_30d_revenue_usd']/1e6:.2f}M (quoting DefiLlama)", "F1a, F4d"))
    T.append((f"'Buyback' revenue since {bb['buyback_start']}", f"{_usd(rows[2]['onchain'])} (all captured revenue, same days)", f"{_usd(rows[2]['defillama'])} (HoldersRevenue)",
              "-", "-", f"Altcoin Buzz: ${ab['defillama_cumulative_holders_revenue_usd']/1e6:.2f}M cumulative (DefiLlama)", "F2c"))
    for w in CONFIG["revenue_wallets"]:
        v = W[w]
        ours = v.get("all_time_definition_near")
        T.append((f"Revenue into `{w}`, all time, to end of {r['dashboard']['cutoff_day']}", f"{ours:,.2f} NEAR" if ours is not None else "-", "-",
                  f"{v['dashboard_near']:,.1f} NEAR (read {rd['fetched_on']})", "-", "-", "F2e"))
    T.append(("Buyback wallet balance", f"{r['balance_near']:,.3f} NEAR at block {m['head_height']:,}; {r['altcoinbuzz']['ours_balance_at_block_near']:,.3f} at {ab['nearblocks_block']:,}",
              "-", "not shown (the wallet row is revenue in, see F2e)", "-", f"NearBlocks via Altcoin Buzz: {ab['nearblocks_balance_near']:,.3f} NEAR at block {ab['nearblocks_block']:,}", "F2a, F2f"))
    T.append((f"Share of captured NEAR that reached the buyback wallet since {bb['buyback_start']}", f"{bb['share_reached_pct']:.1f}%", "100% (labels all captured revenue as buyback)",
              "-", "-", "-", "F2c"))
    T.append(("Buyback NEAR burned", "0 (balance fully reconciled; supply rose in every sampled interval)", "\"not burned\" (methodology)",
              f"\"{rd['claim_text'].split(', ')[1].rstrip('.')}\"", f"\"{pub['hsp029_buyback_text']['quote'].split(', stay')[0].split(', and')[0]} ... stay removed\"",
              f"SVRN CEO (Stocktwits {pub['held_not_burned_context']['published']}): held, not destroyed", "F2a, F2b"))
    T.append(("Treasury share of supply growth", f"{h92:.3f}% (92 days)", "-", "-", f"{hsp['hsp029_treasury_share_pct']}%", "-", "F3a"))
    T.append(("Realised net issuance", f"{d['annualised_net_issuance_pct']:.3f}%/yr (28 days)", "-", "-", f"{hsp['hsp029_realized_issuance_pct']}%/yr", "-", "F3b"))
    return _md(T)


def channel_table(m):
    s = m["sample"]
    chs = s["channels"]
    T = [("Month", "Blocks", "Referred swaps") + tuple(f"`{c}`" if c != "other" else "other" for c in chs)]
    for mo in sorted(s["channel_mix_by_month"]):
        row = s["channel_mix_by_month"][mo]
        cells = tuple(_pct(row["channels"][c]["p"], row["channels"][c]["lo"], row["channels"][c]["hi"]) for c in chs)
        T.append((mo + (" (partial)" if mo not in s["full_months"] else ""), str(row["blocks"]), str(row["referred_swaps"])) + cells)
    P = s["channel_mix_pooled"]
    T.append(("**2026 pooled** (stratified)", str(s["blocks"]), str(sum(r["referred_swaps"] for r in s["channel_mix_by_month"].values())))
             + tuple(f"**{_pct(P[c]['p'], P[c]['lo'], P[c]['hi'])}**" for c in chs))
    return _md(T)


def meta_table(m):
    s = m["sample"]
    T = [("Month", "Blocks", "Transactions", "Meta-transaction (Delegate) share [95% CI]", "Design effect")]
    for mo in sorted(s["meta_tx_by_month"]):
        v = s["meta_tx_by_month"][mo]
        T.append((mo + (" (partial)" if mo not in s["full_months"] else ""), str(v["blocks"]), f"{v['units']:,}", _pct(v["p"], v["lo"], v["hi"], 1),
                  f"{v['deff']:.1f}" if v["deff"] else "-"))
    P = s["meta_tx_pooled"]
    T.append(("**2026 pooled** (stratified)", str(s["blocks"]), f"{P['units']:,}", f"**{_pct(P['p'], P['lo'], P['hi'], 1)}**", "-"))
    return _md(T)


def _md(T):
    out = ["| " + " | ".join(T[0]) + " |", "|" + "---|" * len(T[0])]
    out += ["| " + " | ".join(r) + " |" for r in T[1:]]
    return "\n".join(out)


BLOCKS = {
    "sources": ("<!-- sources:begin (generated by `make readme`) -->", "<!-- sources:end -->", sources_table),
    "channels": ("<!-- channels:begin (generated by `make readme`) -->", "<!-- channels:end -->", channel_table),
    "metatx": ("<!-- metatx:begin (generated by `make readme`) -->", "<!-- metatx:end -->", meta_table),
}
