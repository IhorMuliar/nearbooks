"""Keeper (keeper/keeper.py): signing, transaction encoding, the first-block search and the run logic, against fakes
(no network)."""
import base64, calendar, hashlib, io, json, os, sys, time, unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "keeper"))
import keeper  # noqa: E402

CONTRACT, ACCOUNT = "nb.testnet", "keeper.testnet"
NOW = calendar.timegm((2026, 10, 21, 0, 30, 0)) * 1.0  # 2026-10-21 00:30 UTC
SEED = bytes(range(32))
KEY = keeper.Key("ed25519:" + keeper.b58encode(SEED + keeper.ed25519_public(SEED)))
FC = {"FunctionCall": {"receiver_id": CONTRACT, "method_names": ["keeper_request_snapshot", "resolve_prediction"], "allowance": "500000000000000000000000"}}


def pred(i, date, status="pending"):
    return {"id": i, "resolve_after": date, "status": status}


class FakeChain:
    """Testnet contract + access key. Stores a snapshot when keeper_request_snapshot is broadcast."""

    def __init__(self, preds=(), snapshots=(), permission=FC, budget=10**23, cap=6 * 10**22, spent=0, refuse=None):
        self.preds, self.snaps, self.permission, self.refuse = list(preds), set(snapshots), permission, refuse
        self.keeper = {"keeper_id": ACCOUNT, "budget": str(budget), "daily_cap": str(cap), "spent_today": str(spent), "request_cost": str(3 * 10**22), "day": "2026-10-21"}
        self.sent = []

    def call(self, method, params):
        if method == "query" and params["request_type"] == "view_access_key":
            return {"nonce": 41, "permission": self.permission}
        if method == "query":
            args = json.loads(base64.b64decode(params["args_base64"]))
            m = params["method_name"]
            val = {"list_predictions": lambda: self.preds[args["from_index"]:args["from_index"] + args["limit"]],
                   "get_attested_snapshot": lambda: {"date": args["date"]} if args["date"] in self.snaps else None,
                   "get_keeper": lambda: self.keeper}[m]()
            return {"result": list(json.dumps(val).encode())}
        if method == "block":
            return {"header": {"hash": keeper.b58encode(b"\x07" * 32)}}
        if method == "send_tx":
            assert params["wait_until"] == "INCLUDED"
            if self.refuse:
                raise keeper.RpcError(json.dumps({"name": "HANDLER_ERROR", "cause": {"name": "INVALID_TRANSACTION"}, "data": self.refuse}))
            raw = base64.b64decode(params["signed_tx_base64"])
            tx, sig = raw[:-65], raw[-64:]
            self.sent.append((tx, sig))
            m, args = self.decode(tx)
            if m == "keeper_request_snapshot":
                self.snaps.add(args["date"])
                return keeper.b58encode(hashlib.sha256(tx).digest())
            self.preds = [dict(p, status="confirmed") if p["id"] == args["id"] else p for p in self.preds]
            return "ok"
        if method == "EXPERIMENTAL_tx_status":
            return {"final_execution_status": "FINAL", "status": {"SuccessValue": base64.b64encode(b'"stored attested snapshot"').decode()}}
        raise AssertionError(method)

    @staticmethod
    def decode(tx):
        """Method and args of a function_call_tx (enough of borsh for these tests)."""
        i = 0
        def take(n):
            nonlocal i
            i += n
            return tx[i - n:i]
        def string():
            return take(int.from_bytes(take(4), "little")).decode()
        assert string() == ACCOUNT and take(1) == b"\0" and take(32) == KEY.public
        assert int.from_bytes(take(8), "little") == 42, "nonce = access key nonce + 1"
        assert string() == CONTRACT and take(32) == b"\x07" * 32 and take(4) == (1).to_bytes(4, "little") and take(1) == b"\x02"
        method, args = string(), json.loads(take(int.from_bytes(take(4), "little")))
        gas, deposit = int.from_bytes(take(8), "little"), int.from_bytes(take(16), "little")
        assert deposit == 0 and i == len(tx)
        assert gas == (keeper.REQUEST_GAS if method == "keeper_request_snapshot" else keeper.RESOLVE_GAS)
        return method, args


class FakeMainnet:
    """Blocks every ~1.1 s from height 1 at `t0` seconds (genesis), some heights skipped."""

    def __init__(self, t0, n=200_000, skip=lambda h: h % 7 == 3):
        self.t0, self.n, self.skip, self.calls = t0, n, skip, 0

    def ts(self, h):
        return int((self.t0 + (h - 1) * 1.1) * 10**9) + 123

    def call(self, method, params):
        self.calls += 1
        top = self.n
        h = top if "finality" in params else params["block_id"]
        if h < 1 or h > top or (self.skip(h) and h != top):
            raise keeper.RpcError('{"cause": {"name": "UNKNOWN_BLOCK"}}')
        return {"header": {"height": h, "timestamp_nanosec": str(self.ts(h))}}


def run(chain, mainnet=None, key=KEY, dry=False, date=None, resolve=True):
    k = keeper.Keeper(chain, mainnet or FakeMainnet(NOW - 30 * 86_400, n=2_500_000), CONTRACT, ACCOUNT, key, dry, wait_secs=5, sleep=lambda s: None, now=lambda: NOW)
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = k.run(date, resolve)
    return code, [json.loads(l) for l in buf.getvalue().splitlines()]


class Crypto(unittest.TestCase):
    def test_ed25519_matches_rfc8032_vectors(self):
        for seed, pub, msg, sig in [
            ("9d61b19deffd5a60ba844af492ec2cc44449c5697b326919703bac031cae7f60", "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a", "",
             "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"),
            ("c5aa8df43f9f837bedb7442f31dcb7b166d38535076f094b85ce3a2e0b4458f7", "fc51cd8e6218a1a38da47ed00230f0580816ed13ba3303ac5deb911548908025", "af82",
             "6291d657deec24024827e69c3abe01a30ce548a284743a445e3680d7db5ac3ac18ff9b538d16f290ae67f760984dc6594a7c15e9716ed28dc027beceea1ec40a")]:
            self.assertEqual(keeper.ed25519_public(bytes.fromhex(seed)).hex(), pub)
            self.assertEqual(keeper.ed25519_sign(bytes.fromhex(seed), bytes.fromhex(msg)).hex(), sig)

    def test_base58_roundtrip_with_leading_zeros(self):
        for b in (b"", b"\0\0\x01", bytes(range(64)), b"\xff" * 32):
            self.assertEqual(keeper.b58decode(keeper.b58encode(b)), b)

    def test_key_parsing(self):
        self.assertEqual(keeper.Key("ed25519:" + keeper.b58encode(SEED)).public, KEY.public)
        with self.assertRaises(ValueError):
            keeper.Key("ed25519:" + keeper.b58encode(SEED + b"\1" * 32))  # public half does not match
        with self.assertRaises(ValueError):
            keeper.Key("secp256k1:abc")

    def test_signed_tx_is_tx_bytes_plus_ed25519_signature_of_its_sha256(self):
        tx = keeper.function_call_tx(ACCOUNT, KEY.public, 42, CONTRACT, b"\x07" * 32, "resolve_prediction", b'{"id":4}', keeper.RESOLVE_GAS)
        self.assertEqual(FakeChain.decode(tx), ("resolve_prediction", {"id": 4}))
        b64, h = keeper.signed_tx(KEY, tx)
        raw = base64.b64decode(b64)
        self.assertEqual((raw[:len(tx)], raw[len(tx)], h), (tx, 0, keeper.b58encode(hashlib.sha256(tx).digest())))
        self.assertEqual(raw[len(tx) + 1:], keeper.ed25519_sign(SEED, hashlib.sha256(tx).digest()))


class FirstBlock(unittest.TestCase):
    def test_finds_the_first_block_at_or_after_midnight_with_skipped_heights(self):
        midnight = calendar.timegm((2026, 10, 20, 0, 0, 0))
        for offset in (0.05, 0.6, 1.0, 50_000.3):
            m = FakeMainnet(midnight - offset - 100_000)
            h = keeper.first_block(m, "2026-10-20")
            self.assertFalse(m.skip(h))
            self.assertGreaterEqual(m.ts(h), midnight * 10**9)
            prev = max(x for x in range(1, h) if not m.skip(x))
            self.assertLess(m.ts(prev), midnight * 10**9)
            self.assertLess(m.calls, 200, "binary search, not a scan")

    def test_refuses_a_day_that_has_not_started(self):
        with self.assertRaises(keeper.RpcError):
            keeper.first_block(FakeMainnet(calendar.timegm((2026, 10, 25, 0, 0, 0)), n=100), "2026-10-26")

    def test_rpc_backs_off_and_rotates_on_transport_errors(self):
        r = keeper.Rpc(["https://127.0.0.1:1/a", "https://127.0.0.1:1/b"], attempts=3, timeout=0.2, sleep=lambda s: waits.append(s))
        waits = []
        with self.assertRaises(keeper.RpcError):
            r.call("block", {"finality": "final"})
        self.assertEqual(waits, [0.5, 1.0, 2.0])


class Run(unittest.TestCase):
    def test_requests_the_missing_snapshot_then_resolves_what_it_decides(self):
        chain = FakeChain(preds=[pred(0, "2026-12-31"), pred(4, "2026-10-21"), pred(5, "2026-10-21")])
        code, logs = run(chain)
        self.assertEqual(code, 0)
        methods = [FakeChain.decode(tx) for tx, _ in chain.sent]
        self.assertEqual(methods[0][0], "keeper_request_snapshot")
        self.assertEqual(methods[0][1]["date"], "2026-10-21")
        self.assertLess(methods[0][1]["block_height_28d"], methods[0][1]["block_height"])
        self.assertEqual([m for m in methods[1:]], [("resolve_prediction", {"id": 4}), ("resolve_prediction", {"id": 5})])
        self.assertEqual([p["status"] for p in chain.preds], ["pending", "confirmed", "confirmed"], "the 2026-12-31 prediction stays pending")
        for tx, sig in chain.sent:  # every transaction is signed by the keeper key
            self.assertEqual(sig, keeper.ed25519_sign(SEED, hashlib.sha256(tx).digest()))

    def test_a_refused_transaction_fails_the_run_at_once(self):
        chain = FakeChain(refuse={"TxExecutionError": {"InvalidTxError": {"NotEnoughBalance": {}}}})
        code, logs = run(chain)
        self.assertEqual((code, chain.sent), (1, []))
        self.assertIn("NotEnoughBalance", json.dumps(logs))
        self.assertNotIn("tx_sent", [l["event"] for l in logs])

    def test_idempotent_when_the_snapshot_exists(self):
        chain = FakeChain(snapshots={"2026-10-21"})
        code, logs = run(chain)
        self.assertEqual((code, chain.sent), (0, []))
        self.assertIn("snapshot_exists", [l["event"] for l in logs])

    def test_catches_up_an_earlier_pending_resolve_date_but_never_a_future_one(self):
        chain = FakeChain(preds=[pred(1, "2026-10-19"), pred(2, "2026-10-22")])
        run(chain, resolve=False)
        self.assertEqual(sorted(FakeChain.decode(tx)[1]["date"] for tx, _ in chain.sent), ["2026-10-19", "2026-10-21"])

    def test_dry_run_signs_nothing_and_needs_no_key(self):
        chain = FakeChain(preds=[pred(4, "2026-10-21")])
        code, logs = run(chain, key=None, dry=True)
        self.assertEqual((code, chain.sent), (0, []))
        planned = [l["call"] for l in logs if l["event"] == "dry_run"]
        self.assertEqual(planned, ["keeper_request_snapshot"], "nothing to resolve yet: the snapshot does not exist")

    def test_refuses_a_full_access_key(self):
        with self.assertRaises(SystemExit) as e:
            run(FakeChain(permission="FullAccess"))
        self.assertIn("full-access", str(e.exception))

    def test_refuses_a_key_with_a_wider_scope(self):
        wide = {"FunctionCall": dict(FC["FunctionCall"], method_names=["keeper_request_snapshot", "resolve_prediction", "fund_keeper"])}
        with self.assertRaises(SystemExit):
            run(FakeChain(permission=wide))

    def test_stops_when_the_daily_cap_or_budget_is_used_up(self):
        for chain in (FakeChain(spent=4 * 10**22), FakeChain(budget=10**22)):
            code, logs = run(chain)
            self.assertEqual((code, chain.sent), (1, []))
            self.assertTrue(any(l["event"] == "error" and "exhausted" in l["reason"] for l in logs))

    def test_refuses_a_date_that_has_not_started(self):
        chain = FakeChain()
        code, _ = run(chain, date="2026-10-22")
        self.assertEqual((code, chain.sent), (1, []))

    def test_logs_are_json_lines(self):
        _, logs = run(FakeChain(snapshots={"2026-10-21"}))
        self.assertEqual([logs[0]["event"], logs[-1]["event"]], ["start", "done"])
        self.assertTrue(all("ts" in l for l in logs))


if __name__ == "__main__":
    unittest.main()
