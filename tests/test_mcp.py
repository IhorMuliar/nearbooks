"""Starts mcp/server.py as a subprocess and speaks MCP to it over stdio. Run: python3 -m unittest tests.test_mcp -v"""
import base64, hashlib, http.server, json, os, subprocess, sys, threading, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
METRICS = os.environ.get("NEARBOOKS_METRICS", os.path.join(ROOT, "data", "snapshot-2026-10-20", "metrics.json"))
CACHE = os.path.join(ROOT, "site", "contract-cache.json")
sys.path.insert(0, os.path.join(ROOT, "mcp"))
import chain  # noqa: E402

DEAD = "http://127.0.0.1:9"   # nothing listens on the discard port: connection refused


class McpSession:
    def __init__(self, env=None):
        e = dict(os.environ, **(env or {}))
        # -I ignores PYTHON* variables but not ours; pass the settings explicitly
        self.p = subprocess.Popen([sys.executable, "-I", os.path.join(ROOT, "mcp", "server.py"), METRICS],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, env=e)
        self.n = 0

    def call(self, method, params=None):
        self.n += 1
        self.p.stdin.write(json.dumps({"jsonrpc": "2.0", "id": self.n, "method": method, "params": params or {}}) + "\n"); self.p.stdin.flush()
        resp = json.loads(self.p.stdout.readline())
        assert resp["id"] == self.n
        return resp

    def close(self):
        self.p.stdin.close(); self.p.wait(timeout=5); self.p.stdout.close()


class TestMcp(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.s = McpSession()
        with open(METRICS) as f:
            cls.m = json.load(f)
        cls.init = cls.s.call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}})
        cls.s.p.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"); cls.s.p.stdin.flush()

    @classmethod
    def tearDownClass(cls):
        cls.s.close()

    def text(self, name, args=None):
        r = self.s.call("tools/call", {"name": name, "arguments": args or {}})["result"]
        self.assertFalse(r["isError"], r)
        return json.loads(r["content"][0]["text"])

    def test_initialize(self):
        self.assertEqual(self.init["result"]["serverInfo"]["name"], "nearbooks")
        self.assertIn("tools", self.init["result"]["capabilities"])

    def test_tools_listed(self):
        names = {t["name"] for t in self.s.call("tools/list")["result"]["tools"]}
        self.assertEqual(names, {"get_metrics", "get_reconciliation", "list_predictions", "get_attested_snapshots", "get_predictions", "get_contract_config"})

    def test_get_metrics_matches_file(self):
        out = self.text("get_metrics")
        self.assertEqual(out["as_of"], self.m["as_of"])
        self.assertAlmostEqual(out["captured_revenue_ytd_usd"], self.m["revenue"]["ytd_usd"])
        self.assertAlmostEqual(out["buyback_wallet_balance_near"], self.m["buyback"]["balance_near_rpc"])

    def test_get_metrics_section(self):
        self.assertIn("breakeven_volume_usd_per_day", self.text("get_metrics", {"section": "deflation"}))

    def test_bad_section_is_in_band_error(self):
        r = self.s.call("tools/call", {"name": "get_metrics", "arguments": {"section": "nope"}})["result"]
        self.assertTrue(r["isError"])

    def test_reconciliation(self):
        out = self.text("get_reconciliation")
        self.assertEqual(out["rows"][0]["onchain"], self.m["reconciliation"]["rows"][0]["onchain"])
        self.assertEqual(len(out["monthly"]), len(self.m["reconciliation"]["monthly"]))

    def test_predictions(self):
        out = self.text("list_predictions")
        self.assertEqual([p["id"] for p in out["predictions"]], [p["id"] for p in self.m["predictions"]])
        self.assertEqual([p["id"] for p in self.text("list_predictions", {"metric": "supply_total_near"})["predictions"]], ["P1"])

    def test_unknown_tool_and_method(self):
        self.assertIn("error", self.s.call("tools/call", {"name": "nope"}))
        self.assertEqual(self.s.call("bogus")["error"]["code"], -32601)


class FakeRpc(http.server.BaseHTTPRequestHandler):
    """A NEAR JSON-RPC endpoint that answers the contract's view calls from the committed cache."""
    state = None
    calls = []

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["content-length"])))
        p = req["params"]
        FakeRpc.calls.append(p["method_name"])
        args = json.loads(base64.b64decode(p["args_base64"]) or b"{}")
        s = FakeRpc.state
        value = {"list_attested_snapshots": s["attested"][args.get("from_index", 0):][:args.get("limit", 30)],
                 "list_predictions": s["predictions"][args.get("from_index", 0):][:args.get("limit", 30)],
                 "get_config": [s["owner_id"], s["reporter_id"]], "get_pin_delay_secs": s["pin_delay_secs"], "list_pins": s["pins"],
                 "get_pin_proposal": s["pin_proposal"], "get_earliest_date": s["earliest_date"]}[p["method_name"]]
        body = json.dumps({"jsonrpc": "2.0", "id": req["id"], "result": {"result": list(json.dumps(value).encode()), "logs": [], "block_height": 4242}}).encode()
        self.send_response(200); self.send_header("content-type", "application/json"); self.send_header("content-length", str(len(body))); self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


class TestMcpContract(unittest.TestCase):
    """get_attested_snapshots / get_predictions / get_contract_config against a fake RPC, an unreachable RPC and a fallback."""

    @classmethod
    def setUpClass(cls):
        with open(CACHE) as f:
            cls.cache = json.load(f)
        FakeRpc.state = cls.cache
        cls.httpd = http.server.HTTPServer(("127.0.0.1", 0), FakeRpc)
        cls.url = f"http://127.0.0.1:{cls.httpd.server_port}"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def ask(self, rpc, name, args=None):
        s = McpSession({"NEARBOOKS_RPC": rpc, "NEARBOOKS_CACHE": CACHE})
        try:
            r = s.call("tools/call", {"name": name, "arguments": args or {}})["result"]
        finally:
            s.close()
        self.assertFalse(r["isError"], r)
        return json.loads(r["content"][0]["text"])

    def test_live_snapshots_come_from_the_rpc(self):
        out = self.ask(self.url, "get_attested_snapshots")
        self.assertEqual((out["mode"], out["rpc"], out["block_height"]), ("live", self.url, 4242))
        self.assertEqual([x["date"] for x in out["snapshots"]], sorted(x["date"] for x in self.cache["attested"]))
        first = next(x for x in self.cache["attested"] if x["date"] == out["snapshots"][0]["date"])
        sup = next(int(m["value"]) for m in first["metrics"] if m["key"] == "supply_total_near")
        self.assertAlmostEqual(out["snapshots"][0]["metrics"]["supply_total_near"], sup / 1e6)   # micro-units decoded
        self.assertTrue(out["snapshots"][0]["attestation_url"].startswith("https://testnet-api.outlayer.ai/attestations/by-request/"))

    def test_unreachable_rpc_falls_back_to_the_cache_and_says_so(self):
        out = self.ask(DEAD, "get_attested_snapshots")
        self.assertEqual(out["mode"], "offline")
        self.assertEqual(out["cache_fetched_at"], self.cache["fetched_at"])
        self.assertIn("127.0.0.1:9", out["reason"])
        self.assertEqual(len(out["snapshots"]), len(self.cache["attested"]))
        self.assertIn("transaction_url", out["snapshots"][0], "the cache carries the attestation's tx hash")

    def test_second_rpc_is_tried_when_the_first_fails(self):
        out = self.ask(f"{DEAD},{self.url}", "get_predictions")
        self.assertEqual((out["mode"], out["rpc"]), ("live", self.url))

    def test_predictions_are_decoded_and_filterable(self):
        out = self.ask(self.url, "get_predictions")
        self.assertEqual([p["id"] for p in out["predictions"]], [p["id"] for p in self.cache["predictions"]])
        p0 = out["predictions"][0]
        self.assertEqual(p0["metric"], "supply_total_near")
        self.assertAlmostEqual(p0["threshold"], int(self.cache["predictions"][0]["threshold"]) / 1e6)
        for status in ("pending", "confirmed", "refuted", "void"):
            got = self.ask(self.url, "get_predictions", {"status": status})["predictions"]
            self.assertTrue(all(p["status"] == status for p in got))
            self.assertEqual(len(got), sum(p["status"] == status for p in self.cache["predictions"]))

    def test_contract_config_shows_the_timelock(self):
        out = self.ask(self.url, "get_contract_config")
        self.assertEqual(out["pin_delay_secs"], self.cache["pin_delay_secs"])
        self.assertEqual(out["pins"][0]["from_ns"], "0", "the first pin covers every earlier date")

    def test_offline_flag_never_touches_the_rpc(self):
        FakeRpc.calls = []
        out = self.ask(self.url, "get_predictions", {"offline": True})
        self.assertEqual((out["mode"], FakeRpc.calls), ("offline", []))


class TestChain(unittest.TestCase):
    def test_attestation_checks_compare_all_three_hashes(self):
        with open(CACHE) as f:
            snap = json.load(f)["attested"][0]
        a = snap["source"]["attested"]
        good = {"request_id": a["request_id"], "wasm_hash": a["wasm_hash"], "executed_wasm_sha256": a["wasm_hash"], "output_hash": a["output_sha256"],
                "input_hash": hashlib.sha256(chain.worker_input(snap).encode()).hexdigest(), "transaction_hash": "TX"}
        orig = chain._post_get
        try:
            chain._post_get = lambda url, timeout: dict(good)
            self.assertTrue(all(v for k, v in chain.attestation_checks(snap).items() if k != "transaction_hash"))
            for k in ("wasm_hash", "input_hash", "output_hash"):
                chain._post_get = lambda url, timeout, k=k: dict(good, **{k: "0" * 64})
                self.assertFalse(chain.attestation_checks(snap)[f"{k}_matches"], k)
        finally:
            chain._post_get = orig

    def test_worker_input_matches_the_contract_format(self):
        with open(CACHE) as f:
            snap = json.load(f)["attested"][0]
        self.assertRegex(chain.worker_input(snap), r'^\{"n":"\d+","d":"\d{4}-\d\d-\d\d","b":"\d+","b28":"\d+"\}$')


if __name__ == "__main__":
    unittest.main()
