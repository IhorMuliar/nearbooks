#!/usr/bin/env python3
"""Minimal read-only MCP server (stdio, newline-delimited JSON-RPC 2.0). Standard library only.

    python3 mcp/server.py [path/to/metrics.json]      # default: $NEARBOOKS_METRICS, else newest data/*/metrics.json

Tools: get_metrics, get_reconciliation, list_predictions. It serves what pipeline/compute.py wrote; it does not
fetch anything and does not talk to the chain.
"""
import glob, json, os, sys

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
    return {"note": "Predictions are resolved on-chain by the nearbooks contract (anyone may call resolve_prediction); this server only lists them.", "predictions": p}


TOOLS = {
    "get_metrics": (tool_get_metrics, "Headline NEAR Intents revenue / buyback / supply / deflation metrics as of the latest verified snapshot. Optional `section` returns a full section (revenue, reconciliation, buyback, supply, hsp029, deflation, snapshot).",
                    {"type": "object", "properties": {"section": {"type": "string"}}}),
    "get_reconciliation": (tool_get_reconciliation, "On-chain-rebuilt revenue vs DefiLlama: summary rows, monthly deltas and correlation by lag.", {"type": "object", "properties": {}}),
    "list_predictions": (tool_list_predictions, "Dated, falsifiable predictions (claim, metric, comparator, threshold, resolve_after) with the current value of each metric.",
                         {"type": "object", "properties": {"metric": {"type": "string"}}}),
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
                    "serverInfo": {"name": "nearbooks", "version": "0.1.0"}})
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
