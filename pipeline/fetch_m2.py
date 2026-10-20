"""Fetch the M2 inputs into their own snapshot directory (separate manifest).

    python3 pipeline/fetch_m2.py --main data/live --out data/live-m2

1. Revenue-wallet transfers before the year start (FastNear transfers API), so the all-time per-wallet
   figures on revenue.near.org can be rebuilt for the two DAO wallets that existed before 2026.
2. A stratified, seeded NEARData block sample (one stratum per calendar month from the year start to the head
   block of --main), reduced to raw extracts (see neardata.py). NEARData throttles hard (HTTP 429 after a short
   burst), so requests are spaced by --interval seconds; the run is resumable.
"""
import argparse, datetime, json, os, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import CONFIG, http_json, height_at, block, block_at_or_after, sha256_file, load_json
import neardata

E = CONFIG["endpoints"]
S = CONFIG["neardata_sample"]


def utc(day):
    return datetime.datetime.fromisoformat(day).replace(tzinfo=datetime.timezone.utc).timestamp()


def pull_before(account, out, until_ns):
    n, tok, calls = 0, None, 0
    with open(out, "w") as f:
        while True:
            body = {"account_id": account, "limit": 100, "from_timestamp_ms": 0}
            if tok:
                body["resume_token"] = tok
            d = http_json(E["transfers"], body)
            calls += 1
            time.sleep(0.4)
            done = False
            for t in d["transfers"]:
                if int(t["block_timestamp"]) < until_ns:
                    f.write(json.dumps(t, sort_keys=True) + "\n"); n += 1
                else:
                    done = True
            tok = d.get("resume_token")
            if done or not tok or not d["transfers"]:
                break
    print(f"  pre-{CONFIG['year_start']} transfers {account}: {n} rows, {calls} calls", flush=True)


def month_starts(y0, as_of):
    d = datetime.date.fromisoformat(y0)
    end = datetime.date.fromisoformat(as_of)
    while d <= end:
        yield d.isoformat()
        d = (d.replace(day=28) + datetime.timedelta(days=4)).replace(day=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--main", required=True, help="main snapshot dir (head height, as_of)")
    ap.add_argument("--out", required=True)
    ap.add_argument("--per-stratum", type=int, default=S["blocks_per_stratum"])
    ap.add_argument("--interval", type=float, default=S["request_interval_s"])
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    t0 = time.time()
    man = load_json(os.path.join(a.main, "manifest.json"))
    y0, head_h, head_ts = man["year_start"], int(man["head_height"]), int(man["head_timestamp_ns"]) / 1e9

    # 1. pre-year wallet history
    for w in CONFIG["revenue_wallets"]:
        pull_before(w, os.path.join(a.out, f"transfers_pre_{w}.jsonl"), int(utc(y0) * 1e9))

    # 2. strata = calendar months, bounded by the first block at/after each month start
    plan_path = os.path.join(a.out, "neardata_plan.json")
    if os.path.exists(plan_path):
        P = load_json(plan_path)
    else:
        starts, hint = [], (head_h, head_ts)
        for m in month_starts(y0, man["as_of"]):
            b = height_at(utc(m), *hint)
            while int(b["timestamp_nanosec"]) < utc(m) * 1e9:      # first block at/after midnight, not merely near it
                b = block_at_or_after(int(b["height"]) + 1)
            while True:
                prev = block(int(b["prev_height"])) if b.get("prev_height") else None
                if not prev or int(prev["timestamp_nanosec"]) < utc(m) * 1e9:
                    break
                b = prev
            starts.append((m[:7], int(b["height"]), int(b["timestamp_nanosec"])))
            hint = (int(b["height"]), int(b["timestamp_nanosec"]) / 1e9)
        strata = []
        for i, (m, h, ts) in enumerate(starts):
            end = starts[i + 1][1] if i + 1 < len(starts) else head_h + 1
            strata.append({"month": m, "start_height": h, "end_height": end, "start_timestamp_ns": str(ts)})
        P = {"source": neardata.URL, "seed": S["seed"], "blocks_per_stratum": a.per_stratum, "strata": strata,
             "head_height": head_h, "note": "end_height is exclusive; heights drawn with random.Random(f'{seed}:{month}').sample"}
        json.dump(P, open(plan_path, "w"), indent=1)
    heights = neardata.plan(P["seed"], P["strata"], P["blocks_per_stratum"])
    todo = [(m, h) for m in sorted(heights) for h in heights[m]]
    print(f"  plan: {len(P['strata'])} strata x {P['blocks_per_stratum']} = {len(todo)} blocks", flush=True)

    # 3. fetch + extract (resumable: keeps lines already written)
    out_path = os.path.join(a.out, "neardata_sample.jsonl")
    done = set()
    if os.path.exists(out_path):
        for line in open(out_path):
            done.add(json.loads(line)["requested"])
    th = neardata.Throttle(a.interval)
    t1 = time.time()
    with open(out_path, "a") as f:
        for i, (m, h) in enumerate(todo):
            if h in done:
                continue
            ex = neardata.fetch_extract(th, h)
            f.write(json.dumps(ex, sort_keys=True) + "\n"); f.flush()
            if (i + 1) % 25 == 0:
                rate = (i + 1 - len(done)) / (time.time() - t1)
                print(f"  {i+1}/{len(todo)} blocks ({m}), {rate:.2f} blocks/s, {th.n429} x 429", flush=True)
    # order lines by plan so the file is deterministic regardless of resume history
    lines = {json.loads(l)["requested"]: l for l in open(out_path)}
    with open(out_path, "w") as f:
        for _, h in todo:
            f.write(lines[h] if lines[h].endswith("\n") else lines[h] + "\n")

    files = sorted(f for f in os.listdir(a.out) if f != "manifest.json")
    json.dump({"as_of": man["as_of"], "head_height": head_h, "main_manifest_sha256": sha256_file(os.path.join(a.main, "manifest.json")),
               "fetched_unix": int(time.time()), "runtime_s": round(time.time() - t0), "neardata_http_429": th.n429,
               "sha256": {f: sha256_file(os.path.join(a.out, f)) for f in files}},
              open(os.path.join(a.out, "manifest.json"), "w"), indent=1)
    print(f"done in {time.time()-t0:.0f}s -> {a.out}")


if __name__ == "__main__":
    main()
