"""Stratified, seeded block sample over NEARData: plan, fetch, extract (network) and parse (pure).

The plan is a pure function of (seed, strata, blocks per stratum): compute.py rebuilds it and refuses a sample
whose heights differ. Strata are calendar months (UTC); within a month heights are drawn uniformly without
replacement. A height with no block (NEAR skips heights) is replaced by the next existing height.

Per sampled block we keep a small raw extract (one JSON line):
  - every EVENT_JSON log emitted by receipts executed on intents.near, verbatim, and
  - for every transaction in the block's chunks: receiver and the list of action kinds.
"""
import gzip, json, random, time, urllib.error, urllib.request

URL = "https://mainnet.neardata.xyz/v0/block/{}"
UA = "nearbooks-pipeline/0.2"
INTENTS = "intents.near"


def plan(seed, strata, per_stratum):
    """strata: [{"month", "start_height", "end_height"}] (end exclusive) -> {month: sorted heights}."""
    out = {}
    for s in strata:
        rng = random.Random(f"{seed}:{s['month']}")
        out[s["month"]] = sorted(rng.sample(range(s["start_height"], s["end_height"]), per_stratum))
    return out


class Throttle:
    """At most one request per `interval` seconds; on HTTP 429 back off exponentially (honouring Retry-After)."""

    def __init__(self, interval):
        self.interval, self.last, self.n429 = interval, 0.0, 0

    def get(self, url, tries=12):
        for i in range(tries):
            wait = self.last + self.interval - time.time()
            if wait > 0:
                time.sleep(wait)
            self.last = time.time()
            try:
                req = urllib.request.Request(url, headers={"user-agent": UA, "accept-encoding": "gzip"})
                with urllib.request.urlopen(req, timeout=60) as r:
                    raw = r.read()
                    enc = r.headers.get("content-encoding")
                return json.loads(gzip.decompress(raw) if enc == "gzip" else raw)
            except urllib.error.HTTPError as e:
                if e.code == 429 or e.code >= 500:
                    self.n429 += e.code == 429
                    ra = e.headers.get("retry-after")
                    time.sleep(float(ra) if ra and ra.replace(".", "").isdigit() else min(2.0 * 2 ** i, 60))
                    continue
                if e.code == 404:
                    return None
                raise
            except Exception:  # network hiccup
                time.sleep(min(2 + i, 15))
        raise RuntimeError(f"{url}: failed after {tries} tries")


def extract(blk, requested):
    hdr = blk["block"]["header"]
    logs, txs = [], []
    for sh in blk["shards"]:
        for o in sh.get("receipt_execution_outcomes") or []:
            if o["receipt"]["receiver_id"] == INTENTS:
                logs += [l for l in o["execution_outcome"]["outcome"]["logs"] if l.startswith("EVENT_JSON:")]
        for t in (sh.get("chunk") or {}).get("transactions") or []:
            tr = t["transaction"]
            txs.append([tr["receiver_id"], [a if isinstance(a, str) else next(iter(a)) for a in tr["actions"]]])
    return {"requested": requested, "height": hdr["height"], "timestamp_ns": str(hdr["timestamp_nanosec"]),
            "intents_logs": logs, "txs": txs}


def fetch_extract(th, h, max_skip=20):
    for k in range(max_skip):
        blk = th.get(URL.format(h + k))
        if blk:
            return extract(blk, h)
    raise RuntimeError(f"no block within {max_skip} heights of {h}")


# ---------------------------------------------------------------- pure parsing (used by compute.py)

def referred_swaps(ex):
    """token_diff entries that carry a `referral` tag (the tag 1Click sets for the integrating channel)."""
    out = []
    for l in ex["intents_logs"]:
        try:
            e = json.loads(l[len("EVENT_JSON:"):])
        except ValueError:
            continue
        if e.get("standard") == "dip4" and e.get("event") == "token_diff":
            for d in e.get("data") or []:
                if "referral" in d:
                    out.append({"intent_hash": d.get("intent_hash"), "referral": d["referral"], "has_fee": bool(d.get("fees_collected"))})
    return out


def event_counts(ex):
    c = {}
    for l in ex["intents_logs"]:
        try:
            e = json.loads(l[len("EVENT_JSON:"):])
        except ValueError:
            continue
        k = f"{e.get('standard')}:{e.get('event')}"
        c[k] = c.get(k, 0) + (len(e["data"]) if isinstance(e.get("data"), list) else 1)
    return c


def delegate_counts(ex):
    """(transactions, transactions containing a Delegate action, i.e. NEP-366 meta-transactions)."""
    return len(ex["txs"]), sum(1 for _, acts in ex["txs"] if "Delegate" in acts)
