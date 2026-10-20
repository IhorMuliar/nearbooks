"""Starts mcp/server.py as a subprocess and speaks MCP to it over stdio. Run: python3 -m unittest tests.test_mcp -v"""
import json, os, subprocess, sys, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
METRICS = os.environ.get("NEARBOOKS_METRICS", os.path.join(ROOT, "data", "snapshot-2026-10-20", "metrics.json"))


class McpSession:
    def __init__(self):
        self.p = subprocess.Popen([sys.executable, "-I", os.path.join(ROOT, "mcp", "server.py"), METRICS],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
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
        self.assertEqual(names, {"get_metrics", "get_reconciliation", "list_predictions"})

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


if __name__ == "__main__":
    unittest.main()
