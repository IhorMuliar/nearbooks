"""Print the height of the first NEAR mainnet block of a UTC day (stdlib only, network).

    python3 -I worker/scripts/first_block.py YYYY-MM-DD

That block is the canonical observation point the worker checks (prev.timestamp < D 00:00 UTC <= block.timestamp)
and the `block_height` argument of `request_snapshot(date, block_height)`. Binary search over heights on the archival
RPC, skipping heights that have no block, backing off on HTTP 429.
"""
import calendar, json, sys, time, urllib.error, urllib.request

RPCS = ["https://archival-rpc.mainnet.fastnear.com", "https://rpc.mainnet.near.org"]


def rpc(params):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "block", "params": params}).encode()
    for attempt in range(10):
        url = RPCS[attempt % 2]
        try:
            req = urllib.request.Request(url, data=body, headers={"content-type": "application/json"})
            with urllib.request.urlopen(req, timeout=20) as r:
                res = json.load(r)
            if "result" in res:
                return res["result"]["header"]
            if "UNKNOWN_BLOCK" in json.dumps(res.get("error")):
                return None  # no block at this height (skipped) or not on this node
        except urllib.error.URLError:
            pass
        time.sleep(min(0.25 * 2 ** attempt, 4))
    raise SystemExit("RPC unavailable")


def block_at_or_after(h, top):
    """(height, timestamp_ns) of the first existing block with height >= h."""
    while h <= top:
        hd = rpc({"block_id": h})
        if hd:
            return hd["height"], int(hd["timestamp_nanosec"])
        h += 1
    return None


def main():
    date = sys.argv[1]
    midnight = calendar.timegm(time.strptime(date, "%Y-%m-%d")) * 10**9
    top = rpc({"finality": "final"})
    if int(top["timestamp_nanosec"]) < midnight:
        raise SystemExit(f"{date} has not started on mainnet yet")
    span = (int(top["timestamp_nanosec"]) - midnight) // 10**9 * 2 + 10_000  # mainnet makes < 2 blocks/s
    lo, hi = max(1, top["height"] - span), top["height"]
    while block_at_or_after(lo, hi)[1] >= midnight:  # make sure lo starts before midnight
        lo = max(1, lo - span)
    while lo < hi:  # invariant: the answer is in [lo, hi]
        mid = (lo + hi) // 2
        h, ts = block_at_or_after(mid, hi)
        if ts >= midnight:
            hi = h
        else:
            lo = h + 1
    h, ts = block_at_or_after(lo, top["height"])
    print(h)


if __name__ == "__main__":
    main()
