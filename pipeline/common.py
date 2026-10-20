"""Shared helpers: HTTP with retry, NEAR JSON-RPC, config. Standard library only."""
import json, os, time, datetime, urllib.request, urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")) as _f:
    CONFIG = json.load(_f)
UA = "nearbooks-pipeline/0.1"
YOCTO = 10 ** 24


def load_json(path):
    with open(path) as f:
        return json.load(f)


def http_json(url, body=None, retries=10, timeout=60):
    """GET (body None) or POST JSON. Retries with backoff on 429/5xx/network errors."""
    data = None if body is None else json.dumps(body).encode()
    last = ""
    for i in range(retries):
        try:
            req = urllib.request.Request(url, data, {"content-type": "application/json", "user-agent": UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            last = f"HTTP {e.code}"
            if e.code in (429, 500, 502, 503, 504):
                time.sleep(min(1.0 * 2 ** i, 30))
                continue
            raise
        except Exception as e:  # network hiccup
            last = repr(e)[:160]
            time.sleep(min(1 + i, 10))
    raise RuntimeError(f"{url}: failed after {retries} tries ({last})")


def rpc(method, params, url=None):
    url = url or CONFIG["endpoints"]["archival_rpc"]
    return http_json(url, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})


def block(height_or_finality):
    """Block header dict, or None if the height has no block (NEAR skips heights)."""
    p = {"finality": "final"} if height_or_finality == "final" else {"block_id": height_or_finality}
    d = rpc("block", p)
    return None if "error" in d else d["result"]["header"]


def block_at_or_after(height):
    """First existing block at height >= `height` (skipped heights are common)."""
    for h in range(height, height + 50):
        b = block(h)
        if b:
            return b
    raise RuntimeError(f"no block within 50 heights of {height}")


def view_account_yocto(account, height):
    d = rpc("query", {"request_type": "view_account", "block_id": height, "account_id": account})
    if "error" in d:
        raise RuntimeError(f"view_account {account}@{height}: {d['error']}")
    return int(d["result"]["amount"])


def height_at(ts, hint_height, hint_ts, tol_s=2.0):
    """Block at the given unix time via secant iteration on (height, timestamp)."""
    h, t = hint_height, hint_ts
    for _ in range(12):
        est = h + int((ts - t) / 0.6)  # ~0.6 s/block, corrected each pass
        b = block_at_or_after(max(est, 1))
        h, t = int(b["height"]), int(b["timestamp_nanosec"]) / 1e9
        if abs(t - ts) <= tol_s:
            return b
    return b


def day_of(ts_s):
    return datetime.datetime.fromtimestamp(ts_s, datetime.timezone.utc).strftime("%Y-%m-%d")


def sha256_file(path):
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()
