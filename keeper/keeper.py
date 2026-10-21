#!/usr/bin/env python3
"""NEAR Books keeper: once per UTC day, get the attested snapshot of the day and resolve what it decides.

    python3 -I keeper/keeper.py --dry-run                       # plan only: no key needed, nothing signed
    NEARBOOKS_KEEPER_KEY=ed25519:... python3 -I keeper/keeper.py # run (mainnet: nearbooks.near)
    ... keeper/keeper.py --network testnet                      # the testnet deployment

Standard library only. Each run:
  1. checks its own key on chain: it must be a function-call access key whose receiver is the nearbooks contract and
     whose methods are exactly keeper_request_snapshot and resolve_prediction (a full-access key is refused);
  2. for the target date (default: today UTC) and every earlier resolve date of a Pending prediction that has no
     attested snapshot yet: skips dates that already have one (idempotent), finds the first NEAR mainnet block at or
     after 00:00 UTC of the date and of the date 28 days earlier (binary search over mainnet RPC, backoff on errors),
     checks the contract's keeper budget and daily cap, calls keeper_request_snapshot(date, B, B28) and waits for
     the attested result;
  3. calls resolve_prediction for every Pending prediction whose resolve_after date now has an attested snapshot.

Spending is bounded by the chain, not by this program: the key cannot attach a deposit or call any other method,
its allowance caps the gas it can ever burn, and the contract draws each request's deposit from a prepaid budget
under a per-day cap that only the owner sets. Logs are JSON lines on stdout. Exit status 0 = nothing failed.
"""
import argparse, base64, calendar, hashlib, json, os, sys, time, urllib.error, urllib.request

# Per network: the contract, the keeper account and the RPCs the contract lives on. Mainnet block data always comes
# from MAINNET_RPCS (archival: the 28-day block is in the past).
NETWORKS = {
    "mainnet": ("nearbooks.near", "keeper.nearbooks.near", ["https://free.rpc.fastnear.com", "https://rpc.mainnet.near.org"]),
    "testnet": ("nearbooks-v6.okf-kill-1792497882.testnet", "nearbooks-keeper.okf-kill-1792497882.testnet",
                ["https://rpc.testnet.near.org", "https://rpc.testnet.fastnear.com"]),
}
TESTNET_RPCS = NETWORKS["testnet"][2]
MAINNET_RPCS = ["https://archival-rpc.mainnet.fastnear.com", "https://rpc.mainnet.near.org"]
METHODS = {"keeper_request_snapshot", "resolve_prediction"}
REQUEST_GAS = 300 * 10**12
RESOLVE_GAS = 30 * 10**12
LOOKBACK_DAYS = 28


def log(event, **kw):
    print(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "event": event, **kw}, separators=(",", ":")), flush=True)


# ------------------------------------------------------------------ JSON-RPC


class RpcError(Exception):
    pass


class Rpc:
    """JSON-RPC over a list of endpoints, rotating and backing off (exponential, capped) on transport errors and 429."""

    def __init__(self, urls, attempts=8, timeout=20, sleep=time.sleep):
        self.urls, self.attempts, self.timeout, self.sleep = urls, attempts, timeout, sleep

    def call(self, method, params):
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
        last = None
        for attempt in range(self.attempts):
            url = self.urls[attempt % len(self.urls)]
            try:
                req = urllib.request.Request(url, data=body, headers={"content-type": "application/json"})
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    res = json.load(r)
                if "error" not in res:
                    return res["result"]
                err = res["error"]
                cause = (err.get("cause") or {}).get("name", "")
                if cause in ("TIMEOUT_ERROR", "INTERNAL_ERROR", "NO_SYNCED_BLOCKS") or err.get("code") == -429:
                    last = err  # transient
                else:
                    raise RpcError(json.dumps(err))
            except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError) as e:
                last = e
            self.sleep(min(0.5 * 2 ** attempt, 8))
        raise RpcError(f"{method}: no endpoint answered ({last})")


def view(rpc, contract, method, args=None):
    r = rpc.call("query", {"request_type": "call_function", "finality": "final", "account_id": contract, "method_name": method,
                           "args_base64": base64.b64encode(json.dumps(args or {}).encode()).decode()})
    return json.loads(bytes(r["result"]))


# ------------------------------------------------------- first mainnet block of a day


def day_start_ns(date):
    return calendar.timegm(time.strptime(date, "%Y-%m-%d")) * 10**9


def days_before(date, n):
    return time.strftime("%Y-%m-%d", time.gmtime(calendar.timegm(time.strptime(date, "%Y-%m-%d")) - n * 86400))


def first_block(rpc, date):
    """Height of the first mainnet block B with prev.timestamp < date 00:00 UTC <= B.timestamp."""
    midnight = day_start_ns(date)

    def header(params):
        try:
            return rpc.call("block", params)["header"]
        except RpcError as e:
            if "UNKNOWN_BLOCK" in str(e):
                return None  # skipped height (or not on this node)
            raise

    top = header({"finality": "final"})
    if int(top["timestamp_nanosec"]) < midnight:
        raise RpcError(f"{date} has not started on mainnet yet (final block {top['height']})")

    def at_or_after(h):
        while h <= top["height"]:
            hd = header({"block_id": h})
            if hd:
                return hd["height"], int(hd["timestamp_nanosec"])
            h += 1
        return top["height"], int(top["timestamp_nanosec"])

    span = (int(top["timestamp_nanosec"]) - midnight) // 10**9 * 2 + 10_000  # mainnet makes < 2 blocks/s
    lo, hi = max(1, top["height"] - span), top["height"]
    while at_or_after(lo)[1] >= midnight and lo > 1:  # make sure lo starts before midnight
        lo = max(1, lo - span)
    while lo < hi:  # invariant: the answer is in [lo, hi]
        h, ts = at_or_after((lo + hi) // 2)
        if ts >= midnight:
            hi = h
        else:
            lo = h + 1
    return at_or_after(lo)[0]


# ------------------------------------------------------------- keys and signing


B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58encode(b):
    n, out = int.from_bytes(b, "big"), ""
    while n:
        n, r = divmod(n, 58)
        out = B58[r] + out
    return "1" * (len(b) - len(b.lstrip(b"\0"))) + out


def b58decode(s):
    n = 0
    for ch in s:
        n = n * 58 + B58.index(ch)
    body = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    return b"\0" * (len(s) - len(s.lstrip("1"))) + body


# Ed25519 (RFC 8032, section 5.1), pure Python. Only signing is needed; tests check it against the RFC vectors.
_P = 2**255 - 19
_L = 2**252 + 27742317777372353535851937790883648493
_D = -121665 * pow(121666, _P - 2, _P) % _P
_GY = 4 * pow(5, _P - 2, _P) % _P


def _recover_x(y, sign):
    x2 = (y * y - 1) * pow(_D * y * y + 1, _P - 2, _P) % _P
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P:
        x = x * pow(2, (_P - 1) // 4, _P) % _P
    if x & 1 != sign:
        x = _P - x
    return x


_G = (_recover_x(_GY, 0), _GY, 1, _recover_x(_GY, 0) * _GY % _P)


def _add(p, q):
    a = (p[1] - p[0]) * (q[1] - q[0]) % _P
    b = (p[1] + p[0]) * (q[1] + q[0]) % _P
    c = 2 * p[3] * q[3] * _D % _P
    d = 2 * p[2] * q[2] % _P
    e, f, g, h = b - a, d - c, d + c, b + a
    return (e * f % _P, g * h % _P, f * g % _P, e * h % _P)


def _mul(s, p):
    q = (0, 1, 1, 0)
    while s:
        if s & 1:
            q = _add(q, p)
        p, s = _add(p, p), s >> 1
    return q


def _encode(p):
    zi = pow(p[2], _P - 2, _P)
    x, y = p[0] * zi % _P, p[1] * zi % _P
    return (y | ((x & 1) << 255)).to_bytes(32, "little")


def _expand(seed):
    h = hashlib.sha512(seed).digest()
    a = int.from_bytes(h[:32], "little")
    a &= (1 << 254) - 8
    a |= 1 << 254
    return a, h[32:]


def ed25519_public(seed):
    return _encode(_mul(_expand(seed)[0], _G))


def ed25519_sign(seed, msg):
    a, prefix = _expand(seed)
    pub = _encode(_mul(a, _G))
    r = int.from_bytes(hashlib.sha512(prefix + msg).digest(), "little") % _L
    rs = _encode(_mul(r, _G))
    k = int.from_bytes(hashlib.sha512(rs + pub + msg).digest(), "little") % _L
    return rs + ((r + k * a) % _L).to_bytes(32, "little")


class Key:
    """An `ed25519:<base58>` secret key as near-cli stores it (64 bytes: seed + public key, or a 32-byte seed)."""

    def __init__(self, secret):
        if not secret.startswith("ed25519:"):
            raise ValueError("only ed25519 keys are supported")
        raw = b58decode(secret.split(":", 1)[1])
        if len(raw) not in (32, 64):
            raise ValueError("an ed25519 secret key is 32 or 64 bytes")
        self.seed = raw[:32]
        self.public = ed25519_public(self.seed)
        if len(raw) == 64 and raw[32:] != self.public:
            raise ValueError("the secret key's public half does not match its seed")
        self.public_str = "ed25519:" + b58encode(self.public)

    def sign(self, msg):
        return ed25519_sign(self.seed, msg)


# ------------------------------------------------------------- transactions (borsh)


def _u32(n):
    return n.to_bytes(4, "little")


def _str(s):
    b = s.encode()
    return _u32(len(b)) + b


def function_call_tx(signer, public_key, nonce, receiver, block_hash, method, args, gas, deposit=0):
    """Borsh bytes of a NEAR Transaction (V0) with one FunctionCall action."""
    action = b"\x02" + _str(method) + _u32(len(args)) + args + gas.to_bytes(8, "little") + deposit.to_bytes(16, "little")
    return (_str(signer) + b"\x00" + public_key + nonce.to_bytes(8, "little") + _str(receiver) + block_hash
            + _u32(1) + action)


def signed_tx(key, tx_bytes):
    """(base64 SignedTransaction, base58 tx hash)."""
    h = hashlib.sha256(tx_bytes).digest()
    return base64.b64encode(tx_bytes + b"\x00" + key.sign(h)).decode(), b58encode(h)


# ------------------------------------------------------------------- the keeper


class Keeper:
    def __init__(self, rpc, mainnet, contract, account, key=None, dry_run=False, wait_secs=240, sleep=time.sleep, now=time.time):
        self.rpc, self.mainnet, self.contract, self.account, self.key = rpc, mainnet, contract, account, key
        self.dry_run, self.wait_secs, self.sleep, self.now = dry_run, wait_secs, sleep, now
        self.failures = 0

    def today(self):
        return time.strftime("%Y-%m-%d", time.gmtime(self.now()))

    def view(self, method, args=None):
        return view(self.rpc, self.contract, method, args)

    def check_key(self):
        """Refuses any key that is not a function-call key limited to this contract and the two keeper methods."""
        if self.key is None:
            if not self.dry_run:
                raise SystemExit("no key: set NEARBOOKS_KEEPER_KEY (or --key-file), or pass --dry-run")
            log("key_skipped", reason="dry run without a key")
            return
        ak = self.rpc.call("query", {"request_type": "view_access_key", "finality": "final", "account_id": self.account, "public_key": self.key.public_str})
        perm = ak["permission"]
        if perm == "FullAccess" or "FunctionCall" not in perm:
            raise SystemExit(f"refusing to run with a full-access key for {self.account}; give the keeper a function-call key")
        fc = perm["FunctionCall"]
        if fc["receiver_id"] != self.contract or set(fc["method_names"]) != METHODS:
            raise SystemExit(f"key scope {fc['receiver_id']} {fc['method_names']} is not {self.contract} {sorted(METHODS)}")
        log("key_ok", account=self.account, public_key=self.key.public_str, receiver=fc["receiver_id"], methods=sorted(fc["method_names"]), allowance=fc.get("allowance"))

    def predictions(self):
        out, i = [], 0
        while True:
            page = self.view("list_predictions", {"from_index": i, "limit": 100})
            out += page
            if len(page) < 100:
                return out
            i += 100

    def has_snapshot(self, date):
        return self.view("get_attested_snapshot", {"date": date}) is not None

    def targets(self, date, preds):
        """The date asked for plus every earlier Pending resolve date; dates that have not started are left out."""
        today = self.today()
        dates = {date} | {p["resolve_after"] for p in preds if p["status"] == "pending" and p["resolve_after"] <= date}
        return sorted(d for d in dates if d <= today)

    def send(self, method, args, gas):
        """Signs and broadcasts one call, waits until it is final; returns (tx hash, outcome)."""
        ak = self.rpc.call("query", {"request_type": "view_access_key", "finality": "final", "account_id": self.account, "public_key": self.key.public_str})
        block_hash = b58decode(self.rpc.call("block", {"finality": "final"})["header"]["hash"])
        tx = function_call_tx(self.account, self.key.public, ak["nonce"] + 1, self.contract, block_hash, method, json.dumps(args, separators=(",", ":")).encode(), gas)
        b64, tx_hash = signed_tx(self.key, tx)
        # send_tx waits until the transaction is in a block, so a transaction the network refuses (NotEnoughBalance,
        # NotEnoughAllowance, InvalidNonce, ...) fails here; broadcast_tx_async would return its hash and drop it.
        try:
            self.rpc.call("send_tx", {"signed_tx_base64": b64, "wait_until": "INCLUDED"})
        except RpcError as e:
            if "TIMEOUT" not in str(e):
                raise RpcError(f"{method} refused: {e}") from None
        log("tx_sent", method=method, args=args, tx=tx_hash)
        deadline = self.now() + self.wait_secs
        while True:
            try:
                r = self.rpc.call("EXPERIMENTAL_tx_status", {"tx_hash": tx_hash, "sender_account_id": self.account, "wait_until": "FINAL"})
                if r.get("final_execution_status") == "FINAL" or "SuccessValue" in r.get("status", {}) or "Failure" in r.get("status", {}):
                    return tx_hash, r
            except RpcError as e:
                if "UNKNOWN_TRANSACTION" not in str(e) and "TIMEOUT" not in str(e) and "no endpoint" not in str(e):
                    raise
            if self.now() > deadline:
                raise RpcError(f"{tx_hash} not final after {self.wait_secs} s")
            self.sleep(5)

    @staticmethod
    def result(outcome):
        st = outcome.get("status", {})
        if "Failure" in st:
            return False, json.dumps(st["Failure"])[:300]
        v = base64.b64decode(st.get("SuccessValue") or "")
        try:
            return True, json.loads(v) if v else None
        except ValueError:
            return True, v.decode(errors="replace")

    def snapshot(self, date):
        if self.has_snapshot(date):
            log("snapshot_exists", date=date)
            return True
        k = self.view("get_keeper")
        cost, budget, cap, spent = (int(k[x]) for x in ("request_cost", "budget", "daily_cap", "spent_today"))
        if k["keeper_id"] != self.account:
            log("error", date=date, reason=f"the contract's keeper is {k['keeper_id']}, not {self.account}")
            return False
        if budget < cost or spent + cost > cap:
            log("error", date=date, reason="keeper budget or daily cap exhausted", budget=str(budget), spent_today=str(spent), daily_cap=str(cap), request_cost=str(cost))
            return False
        b, b28 = first_block(self.mainnet, date), first_block(self.mainnet, days_before(date, LOOKBACK_DAYS))
        args = {"date": date, "block_height": b, "block_height_28d": b28}
        log("first_blocks", date=date, block_height=b, block_height_28d=b28)
        if self.dry_run:
            log("dry_run", call="keeper_request_snapshot", args=args, gas=REQUEST_GAS, budget=str(budget), request_cost=str(cost))
            return True
        tx_hash, outcome = self.send("keeper_request_snapshot", args, REQUEST_GAS)
        ok, value = self.result(outcome)
        stored = self.has_snapshot(date)
        log("snapshot_result", date=date, tx=tx_hash, ok=ok, value=value, stored=stored)
        return stored

    def resolve(self, preds):
        ok, have = True, {}
        for p in preds:
            if p["status"] != "pending" or p["resolve_after"] > self.today():
                continue
            d = p["resolve_after"]
            if d not in have:
                have[d] = self.has_snapshot(d)
            if not have[d]:
                log("resolve_waiting", id=p["id"], resolve_after=d, reason="no attested snapshot for that date yet")
                continue
            if self.dry_run:
                log("dry_run", call="resolve_prediction", args={"id": p["id"]}, gas=RESOLVE_GAS)
                continue
            tx_hash, outcome = self.send("resolve_prediction", {"id": p["id"]}, RESOLVE_GAS)
            good, value = self.result(outcome)
            log("resolved", id=p["id"], tx=tx_hash, ok=good, status=(value or {}).get("status") if isinstance(value, dict) else value)
            ok &= good
        return ok

    def run(self, date=None, resolve=True):
        date = date or self.today()
        log("start", contract=self.contract, keeper=self.account, date=date, dry_run=self.dry_run)
        if date > self.today():
            log("error", date=date, reason="that date has not started yet")
            return 1
        self.check_key()
        preds = self.predictions()
        for d in self.targets(date, preds):
            try:
                if not self.snapshot(d):
                    self.failures += 1
            except RpcError as e:
                log("error", date=d, reason=str(e)[:300])
                self.failures += 1
        if resolve:
            try:
                if not self.resolve(self.predictions()):
                    self.failures += 1
            except RpcError as e:
                log("error", step="resolve", reason=str(e)[:300])
                self.failures += 1
        log("done", failures=self.failures)
        return 1 if self.failures else 0


def load_key(args):
    secret = os.environ.get("NEARBOOKS_KEEPER_KEY")
    if args.key_file:
        with open(args.key_file) as f:
            secret = json.load(f)["private_key"]
    return Key(secret) if secret else None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--date", help="UTC date YYYY-MM-DD (default: today)")
    ap.add_argument("--network", choices=sorted(NETWORKS), default=os.environ.get("NEARBOOKS_NETWORK", "mainnet"))
    ap.add_argument("--contract", default=os.environ.get("NEARBOOKS_CONTRACT"), help="default: the network's contract")
    ap.add_argument("--account", default=os.environ.get("NEARBOOKS_KEEPER_ACCOUNT"),
                    help="the keeper account (holds only a function-call key); default: the network's keeper")
    ap.add_argument("--key-file", help="near-cli credentials JSON with private_key (default: $NEARBOOKS_KEEPER_KEY)")
    ap.add_argument("--dry-run", action="store_true", help="read and plan only; sign nothing")
    ap.add_argument("--no-resolve", action="store_true", help="skip resolve_prediction")
    ap.add_argument("--wait", type=int, default=240, help="seconds to wait for a transaction to become final")
    a = ap.parse_args(argv)
    contract, keeper, rpcs = NETWORKS[a.network]
    k = Keeper(Rpc(rpcs), Rpc(MAINNET_RPCS), a.contract or contract, a.account or keeper, load_key(a), a.dry_run, a.wait)
    return k.run(a.date, resolve=not a.no_resolve)


if __name__ == "__main__":
    sys.exit(main())
