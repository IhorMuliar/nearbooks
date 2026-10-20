"""Fetch every raw input the metrics need into one directory.

    python3 pipeline/fetch.py --out data/live

Sources (all keyless): FastNear transfers API, FastNear archival JSON-RPC,
DefiLlama (fees/revenue, volume, price). Writes manifest.json with sha256 of each file.
"""
import argparse, datetime, json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import CONFIG, YOCTO, http_json, block, block_at_or_after, height_at, view_account_yocto, day_of, sha256_file

E = CONFIG["endpoints"]


def utc_ms(day):
    return int(datetime.datetime.fromisoformat(day).replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)


def pull_transfers(account, out, from_ms, until_ns):
    """Paginate the FastNear transfers API (ascending) into JSONL; stop past until_ns."""
    n, tok, calls = 0, None, 0
    with open(out, "w") as f:
        while True:
            body = {"account_id": account, "limit": 100, "from_timestamp_ms": from_ms}
            if tok:
                body["resume_token"] = tok
            d = http_json(E["transfers"], body)
            calls += 1
            time.sleep(0.4)  # the API rate-limits bursts (HTTP 429)
            for t in d["transfers"]:
                if int(t["block_timestamp"]) <= until_ns:
                    f.write(json.dumps(t, sort_keys=True) + "\n")
                    n += 1
            tok = d.get("resume_token")
            if not tok or not d["transfers"]:
                break
    print(f"  transfers {account}: {n} rows, {calls} calls", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    t0 = time.time()

    head = block("final")
    head_h, head_ts = int(head["height"]), int(head["timestamp_nanosec"])
    as_of = day_of(head_ts / 1e9)
    print(f"head block {head_h} at {as_of}", flush=True)

    # 1. transfers for the revenue wallets, from the start of the year
    for w in CONFIG["revenue_wallets"]:
        pull_transfers(w, os.path.join(a.out, f"transfers_{w}.jsonl"), utc_ms(CONFIG["year_start"]), head_ts)

    # 2. DefiLlama
    for key, name in (("llama_revenue", "llama_revenue.json"), ("llama_holders_revenue", "llama_holders_revenue.json"),
                      ("llama_volume", "llama_volume.json"), ("price", "price.json")):
        json.dump(http_json(E[key]), open(os.path.join(a.out, name), "w"))
        print(f"  {name} ok", flush=True)

    # 3. total_supply samples every N blocks since year start (archival RPC block headers)
    y0 = block_at_or_after(int(head_h - (head_ts / 1e9 - utc_ms(CONFIG["year_start"]) / 1000) / 0.6))
    # refine the year-start block with the secant search
    y0 = height_at(utc_ms(CONFIG["year_start"]) / 1000, int(y0["height"]), int(y0["timestamp_nanosec"]) / 1e9)
    samples, h = [], int(y0["height"])
    while h < head_h:
        b = block_at_or_after(h)
        samples.append({"height": int(b["height"]), "timestamp_ns": b["timestamp_nanosec"], "total_supply": b["total_supply"]})
        h += CONFIG["supply_sample_step_blocks"]
    samples.append({"height": head_h, "timestamp_ns": head["timestamp_nanosec"], "total_supply": head["total_supply"]})
    json.dump(samples, open(os.path.join(a.out, "rpc_supply_samples.json"), "w"), indent=1)
    print(f"  rpc_supply_samples.json: {len(samples)} samples", flush=True)

    # 4. HSP-029 method: total_supply and treasury.near balance at head and head-N days
    hsp = {}
    for days in [0] + CONFIG["hsp029_windows_days"]:
        if days == 0:
            b = head
        else:
            b = height_at(head_ts / 1e9 - days * 86400, head_h, head_ts / 1e9)
        hh = int(b["height"])
        hsp[str(days)] = {"height": hh, "timestamp_ns": b["timestamp_nanosec"], "total_supply": b["total_supply"],
                          "treasury_yocto": str(view_account_yocto(CONFIG["treasury_account"], hh))}
    json.dump(hsp, open(os.path.join(a.out, "rpc_hsp029.json"), "w"), indent=1)
    print("  rpc_hsp029.json ok", flush=True)

    # 5. revenue-wallet balances at head
    bal = {w: str(view_account_yocto(w, head_h)) for w in CONFIG["revenue_wallets"]}
    json.dump({"height": head_h, "amount_yocto": bal}, open(os.path.join(a.out, "rpc_balances.json"), "w"), indent=1)

    files = sorted(f for f in os.listdir(a.out) if f != "manifest.json")
    json.dump({"as_of": as_of, "head_height": head_h, "head_timestamp_ns": str(head_ts),
               "fetched_unix": int(time.time()), "year_start": CONFIG["year_start"],
               "sha256": {f: sha256_file(os.path.join(a.out, f)) for f in files}},
              open(os.path.join(a.out, "manifest.json"), "w"), indent=1)
    print(f"done in {time.time()-t0:.0f}s -> {a.out}")


if __name__ == "__main__":
    main()
