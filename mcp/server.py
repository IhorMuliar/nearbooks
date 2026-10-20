#!/usr/bin/env python3
"""Minimal read-only MCP server (stdio, newline-delimited JSON-RPC 2.0). Standard library only.

    python3 mcp/server.py [path/to/metrics.json]      # default: $NEARBOOKS_METRICS, else newest data/*/metrics.json

Pipeline tools (historical, from metrics.json): get_metrics, get_reconciliation, list_predictions.
Contract tools (live, NEAR testnet JSON-RPC view calls; no keys): get_attested_snapshots, get_predictions,
get_contract_config. They fall back to the committed cache (site/contract-cache.json) when the RPC cannot be
reached, and every answer says which one it used ("mode": "live" or "offline"). Env: NEARBOOKS_CONTRACT,
NEARBOOKS_RPC (comma-separated), NEARBOOKS_CACHE.
"""
import glob, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import chain

SUPPORTED = ("2025-06-18", "2025-03-26", "2024-11-05")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def metrics_path():
    if len(sys.argv) > 1:
        return sys.argv[1]
    if os.environ.get("NEARBOOKS_METRICS"):
        return os.environ["NEARBOOKS_METRICS"]
    found = sorted(glob.glob(os.path.join(ROOT, "data", "*", "metrics.json")), key=os.path.getmtime)
    if not found:
        raise SystemExit("no metrics.json found; run `make verify` first")
    return found[-1]


def load():
    with open(metrics_path()) as f:
        return json.load(f)


def tool_get_metrics(args):
    m = load()
    summary = {
        "as_of": m["as_of"], "revenue_window": m["window"],
        "captured_revenue_ytd_usd": m["revenue"]["ytd_usd"], "captured_revenue_last30d_usd": m["revenue"]["last30d_usd"],
        "buyback_wallet": m["buyback"]["wallet"], "buyback_wallet_balance_near": m["buyback"]["balance_near_rpc"],
        "buyback_external_inflow_near": m["buyback"]["inflow_external_near"], "buyback_internal_inflow_near": m["buyback"]["inflow_internal_near"],
        "supply_growth_near_since_jan1": m["supply"]["growth_near"], "supply_growth_pct_since_jan1": m["supply"]["growth_pct"],
        "net_issuance_near_per_day_28d": m["deflation"]["net_issuance_near_per_day"],
        "deflation_breakeven_volume_usd_per_day": m["deflation"]["breakeven_volume_usd_per_day"],
        "deflation_breakeven_volume_usd_per_day_at_1_6pct": m["deflation"]["breakeven_volume_usd_per_day_at_target"],
        "current_volume_usd_per_day_30d": m["deflation"]["volume_usd_per_day_30d"],
        "sources": "FastNear transfers API, FastNear archival RPC, DefiLlama; see README findings table for the checks",
    }
    section = args.get("section")
    if section:
        if section not in m or section in ("provenance",):
            raise ValueError(f"unknown section {section!r}; choose from: " + ", ".join(k for k in m if k not in ("provenance",)))
        return m[section] if section != "revenue" else {k: v for k, v in m["revenue"].items() if k != "daily"}
    return summary


def tool_get_reconciliation(args):
    r = load()["reconciliation"]
    return {"rows": r["rows"], "monthly": r["monthly"], "pearson_by_lag": r["pearson_by_lag"]}


def tool_list_predictions(args):
    p = load()["predictions"]
    if args.get("metric"):
        p = [x for x in p if x["metric"] == args["metric"]]
    return {"note": "The pipeline's list with each metric's value in the 2026-10-20 snapshot. Live on-chain status: get_predictions.", "predictions": p}


def _source(state, mode):
    src = _source_mode(state, mode)
    if state["contract"] != chain.OFFICIAL_CONTRACT:
        src["warning"] = f"UNOFFICIAL CONTRACT: {state['contract']} is not {chain.OFFICIAL_CONTRACT}; its snapshots are not vouched for by this repository"
    src["official_contract"] = state["contract"] == chain.OFFICIAL_CONTRACT
    return src


def _source_mode(state, mode):
    if mode == "live":
        return {"mode": "live", "contract": state["contract"], "network": "testnet", "rpc": state["rpc"], "block_height": state["block_height"]}
    return {"mode": "offline", "contract": state["contract"], "network": "testnet", "cache_fetched_at": state["fetched_at"],
            "cache_block_height": state["block_height"], "reason": state.get("offline_reason")}


def _state(args, with_attestations=False):
    if args.get("offline"):
        return chain.load_cache(), "offline"
    return chain.state_or_cache(with_attestations=with_attestations)


def tool_get_attested_snapshots(args):
    state, mode = _state(args, with_attestations=bool(args.get("verify")))
    snaps = state["attested"]
    if args.get("date"):
        snaps = [x for x in snaps if x["date"] == args["date"]]
    return {**_source(state, mode),
            "note": "Each snapshot is NEAR mainnet state at the first block of its UTC date (and 28 days earlier), read by the pinned worker in OutLayer's TDX enclave. "
                    "verify=true fetches the public attestation and checks it against the snapshot, the contract's pin for that date, the repo's known-good worker list "
                    "(contract/known_workers.json) and the transaction on chain; attestation_checks.verified needs all of them. The TDX quote is checked by contract/scripts/verify_attestation.py.",
            "snapshots": [chain.snapshot_view(x) for x in sorted(snaps, key=lambda x: x["date"])]}


def tool_get_predictions(args):
    state, mode = _state(args)
    preds = [chain.prediction_view(p) for p in state["predictions"]]
    if args.get("status"):
        preds = [p for p in preds if p["status"] == args["status"]]
    return {**_source(state, mode), "rule": "Only the attested snapshot for exactly resolve_after decides; a metric it lacks makes the prediction void. Anyone may call resolve_prediction.",
            "predictions": preds}


def tool_get_contract_config(args):
    state, mode = _state(args)
    keys = ("owner_id", "reporter_id", "pin_delay_secs", "pins", "pin_proposal", "earliest_date")
    return {**_source(state, mode), **{k: state[k] for k in keys},
            "note": "A re-pin is proposed, waits pin_delay_secs, then governs only dates that start after it is applied (pins[i].from_ns)."}


TOOLS = {
    "get_metrics": (tool_get_metrics, "Headline NEAR Intents revenue / buyback / supply / deflation metrics as of the latest verified snapshot. Optional `section` returns a full section (revenue, reconciliation, buyback, supply, hsp029, deflation, snapshot).",
                    {"type": "object", "properties": {"section": {"type": "string"}}}),
    "get_reconciliation": (tool_get_reconciliation, "On-chain-rebuilt revenue vs DefiLlama: summary rows, monthly deltas and correlation by lag.", {"type": "object", "properties": {}}),
    "list_predictions": (tool_list_predictions, "Pipeline view of the dated, falsifiable predictions P1-P4 (claim, metric, comparator, threshold, resolve_after) with each metric's value in the 2026-10-20 snapshot.",
                         {"type": "object", "properties": {"metric": {"type": "string"}}}),
    "get_attested_snapshots": (tool_get_attested_snapshots, "Attested NEAR mainnet snapshots stored in the nearbooks testnet contract, read live over RPC (falls back to a committed cache, flagged mode=offline). Optional `date` (YYYY-MM-DD), `verify` (fetch the OutLayer attestation and compare hashes), `offline` (use the cache only).",
                               {"type": "object", "properties": {"date": {"type": "string"}, "verify": {"type": "boolean"}, "offline": {"type": "boolean"}}}),
    "get_predictions": (tool_get_predictions, "Every prediction registered in the nearbooks testnet contract with its on-chain status (pending, confirmed, refuted, void) and the observed value that decided it, read live over RPC (cache fallback, flagged). Optional `status`, `offline`.",
                        {"type": "object", "properties": {"status": {"type": "string", "enum": ["pending", "confirmed", "refuted", "void"]}, "offline": {"type": "boolean"}}}),
    "get_contract_config": (tool_get_contract_config, "Owner, reporter, pin history, waiting pin proposal, timelock and earliest_date of the nearbooks testnet contract, read live (cache fallback, flagged).",
                            {"type": "object", "properties": {"offline": {"type": "boolean"}}}),
}


def reply(id_, result=None, error=None):
    msg = {"jsonrpc": "2.0", "id": id_}
    msg.update({"error": error} if error else {"result": result})
    sys.stdout.write(json.dumps(msg) + "\n"); sys.stdout.flush()


def handle(req):
    method, id_, params = req.get("method"), req.get("id"), req.get("params") or {}
    if id_ is None:        # notification (e.g. notifications/initialized): no reply
        return
    if method == "initialize":
        v = params.get("protocolVersion")
        reply(id_, {"protocolVersion": v if v in SUPPORTED else SUPPORTED[0], "capabilities": {"tools": {}},
                    "serverInfo": {"name": "nearbooks", "version": "0.2.0"}})
    elif method == "ping":
        reply(id_, {})
    elif method == "tools/list":
        reply(id_, {"tools": [{"name": n, "description": d, "inputSchema": s} for n, (_, d, s) in TOOLS.items()]})
    elif method == "tools/call":
        name = params.get("name")
        if name not in TOOLS:
            return reply(id_, error={"code": -32602, "message": f"unknown tool {name!r}"})
        try:
            out = TOOLS[name][0](params.get("arguments") or {})
            reply(id_, {"content": [{"type": "text", "text": json.dumps(out, indent=1)}], "isError": False})
        except Exception as e:   # tool-level failure is reported in-band
            reply(id_, {"content": [{"type": "text", "text": f"error: {e}"}], "isError": True})
    else:
        reply(id_, error={"code": -32601, "message": f"method not found: {method}"})


def main():
    metrics_path()  # fail fast if there is nothing to serve
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            handle(json.loads(line))
        except json.JSONDecodeError:
            reply(None, error={"code": -32700, "message": "parse error"})


if __name__ == "__main__":
    main()
