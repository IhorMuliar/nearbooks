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
        if self.path == "/redirect":
            self.send_response(307); self.send_header("location", "http://169.254.169.254/latest/meta-data/"); self.send_header("content-length", "0"); self.end_headers()
            return
        if self.path == "/big":
            body = b'{"x":"' + b"a" * (300 * 1024) + b'"}'
            self.send_response(200); self.send_header("content-length", str(len(body))); self.end_headers(); self.wfile.write(body)
            return
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
        cls.httpd.server_close()

    def ask(self, rpc, name, args=None):
        s = McpSession({"NEARBOOKS_RPC": rpc, "NEARBOOKS_CACHE": CACHE, "NEARBOOKS_RPC_ALLOW_LOOPBACK": "1"})
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


    def test_rpc_outside_the_allowlist_is_refused_not_contacted(self):
        s = McpSession({"NEARBOOKS_RPC": "https://evil.example,http://rpc.testnet.near.org", "NEARBOOKS_CACHE": CACHE})
        try:
            out = json.loads(s.call("tools/call", {"name": "get_predictions", "arguments": {}})["result"]["content"][0]["text"])
        finally:
            s.close()
        self.assertEqual(out["mode"], "offline")
        self.assertIn("host not in the allowlist", out["reason"])
        self.assertIn("only https is allowed", out["reason"])

    def test_loopback_rpc_needs_the_explicit_switch(self):
        s = McpSession({"NEARBOOKS_RPC": self.url, "NEARBOOKS_CACHE": CACHE, "NEARBOOKS_RPC_ALLOW_LOOPBACK": ""})
        try:
            out = json.loads(s.call("tools/call", {"name": "get_predictions", "arguments": {}})["result"]["content"][0]["text"])
        finally:
            s.close()
        self.assertEqual(out["mode"], "offline")

    def test_redirects_and_oversized_responses_are_refused(self):
        old = chain.ALLOW_LOOPBACK_RPC
        chain.ALLOW_LOOPBACK_RPC = True
        try:
            with self.assertRaises(OSError) as e:
                chain._post(self.url + "/redirect", {}, 5)
            self.assertIn("redirect", str(e.exception))
            with self.assertRaisesRegex(ValueError, "larger than"):
                chain._post(self.url + "/big", {}, 5)
        finally:
            chain.ALLOW_LOOPBACK_RPC = old


class TestUrlAllowlist(unittest.TestCase):
    def test_check_url(self):
        ok = "https://rpc.testnet.near.org"
        self.assertEqual(chain.check_url(ok, chain.RPC_HOSTS), ok)
        for bad in ("http://rpc.testnet.near.org", "https://evil.example", "https://rpc.testnet.near.org:8443", "https://u:p@rpc.testnet.near.org",
                    "https://rpc.testnet.near.org.evil.example", "file:///etc/passwd", "http://127.0.0.1:8080", "https://169.254.169.254/"):
            with self.assertRaises(chain.RefusedUrl, msg=bad):
                chain.check_url(bad, chain.RPC_HOSTS)
        self.assertEqual(chain.check_url("http://127.0.0.1:8080", chain.RPC_HOSTS, loopback_ok=True), "http://127.0.0.1:8080")
        with self.assertRaises(chain.RefusedUrl):
            chain.check_url("http://10.0.0.1", chain.RPC_HOSTS, loopback_ok=True)

    def test_attestation_url_is_rebuilt_not_taken_from_state(self):
        with open(CACHE) as f:
            snap = json.load(f)["attested"][0]
        snap["source"]["attested"]["attestation"] = "http://169.254.169.254/latest/meta-data/"
        url = chain.attestation_url(snap)
        self.assertEqual(url, f"https://testnet-api.outlayer.ai/attestations/by-request/{snap['source']['attested']['request_id']}")
        fetched = []
        saved = (chain._read, chain.view)
        try:
            chain._read = lambda req, timeout: fetched.append(req.full_url) or {}
            chain.view = lambda method, args, contract=None: ({}, "rpc", 1)
            self.assertFalse(chain.attestation_checks(snap)["attestation_url_matches"], "a tampered stored link is reported")
        finally:
            chain._read, chain.view = saved
        self.assertEqual(fetched, [url])
        for rid in ("5168", -1, 2**63, True, 1.5):
            snap["source"]["attested"]["request_id"] = rid
            with self.assertRaises(chain.RefusedUrl, msg=repr(rid)):
                chain.attestation_url(snap)


class TestSiteStatic(unittest.TestCase):
    """site/index.html: no build step, no external scripts or styles, and it only talks to allowlisted origins."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(ROOT, "site", "index.html")) as f:
            cls.html = f.read()

    def test_no_external_scripts_styles_or_fonts(self):
        import re
        self.assertEqual(re.findall(r"<script[^>]+src=", self.html), [])
        self.assertEqual(re.findall(r"<link[^>]+href=", self.html), [])
        self.assertNotIn("@import", self.html)
        self.assertNotIn("url(", self.html)

    def test_rpc_and_attestation_origins_match_the_python_allowlist(self):
        import re
        rpcs = re.search(r"const RPC_ALLOWED = \[([^\]]+)\]", self.html).group(1)
        self.assertEqual({u.split("//")[1] for u in re.findall(r'"([^"]+)"', rpcs)}, chain.RPC_HOSTS)
        self.assertIn(re.search(r'const ATTESTATION_API = "([^"]+)"', self.html).group(1), chain.ATTESTATION_BASES)
        # fetch() is only ever called on an RPC from the allowlisted list, the built attestation URL or local files
        targets = re.findall(r"fetch\(([^,)]+)", self.html)
        self.assertEqual(sorted(set(t.strip() for t in targets)), ["path", "url"])
        self.assertIn('.filter((u) => RPC_ALLOWED.includes(u))', self.html, "?rpc= overrides are filtered")
        self.assertIn("redirect: \"error\"", self.html)

    def test_known_worker_list_is_identical_in_site_and_repo(self):
        import re
        with open(os.path.join(ROOT, "contract", "known_workers.json")) as f:
            known = set(json.load(f)["worker_sha256"])
        site = set(re.findall(r"[0-9a-f]{64}", re.search(r"const KNOWN_WORKERS = \[([^\]]*)\]", self.html).group(1)))
        self.assertEqual(site, known)
        self.assertEqual(chain.KNOWN_WORKERS, known)
        with open(os.path.join(ROOT, "contract", "deployment.json")) as f:
            self.assertIn(json.load(f)["worker_sha256"], known, "the pinned worker is vouched for")
        with open(os.path.join(ROOT, "contract", "scripts", "verify_attestation.py")) as f:
            self.assertIn("known_workers.json", f.read())

    def test_the_contract_cannot_be_overridden_from_the_url(self):
        self.assertNotIn('Q.get("contract")', self.html)
        self.assertIn(f'const CONTRACT = "{chain.OFFICIAL_CONTRACT}";', self.html)

    def test_a_snapshot_is_verified_only_with_every_check(self):
        for k in ("known_worker", "pin_for_date_matches", "tx_targets_contract", "attestation_url_matches", "wasm_hash_matches", "input_hash_matches", "output_hash_matches"):
            self.assertIn(k, self.html)
        self.assertIn('c.verified = Object.entries(c).every(([k, v]) => k === "transaction_hash" || v === true);', self.html)

    def test_cache_is_the_contract_the_site_reads(self):
        with open(CACHE) as f:
            self.assertIn(f'"{json.load(f)["contract"]}"', self.html)


class TestChain(unittest.TestCase):
    def test_attestation_checks_compare_all_three_hashes(self):
        with open(CACHE) as f:
            snap = json.load(f)["attested"][0]
        a = snap["source"]["attested"]
        good = {"request_id": a["request_id"], "wasm_hash": a["wasm_hash"], "executed_wasm_sha256": a["wasm_hash"], "output_hash": a["output_sha256"],
                "input_hash": hashlib.sha256(chain.worker_input(snap).encode()).hexdigest(), "transaction_hash": "TX"}
        good["caller_account_id"] = snap["submitted_by"]
        saved = (chain._post_get, chain.view, chain.tx_targets)
        try:
            chain._post_get = lambda url, timeout: dict(good)
            chain.view = lambda method, args, contract=None: ({"wasm_hash": a["wasm_hash"]}, "rpc", 1)
            chain.tx_targets = lambda tx, sender, contract: tx == "TX" and sender == snap["submitted_by"] and contract == chain.OFFICIAL_CONTRACT
            c = chain.attestation_checks(snap, chain.OFFICIAL_CONTRACT)
            self.assertTrue(c["verified"], c)
            for k in ("wasm_hash", "input_hash", "output_hash"):
                chain._post_get = lambda url, timeout, k=k: dict(good, **{k: "0" * 64})
                c = chain.attestation_checks(snap, chain.OFFICIAL_CONTRACT)
                self.assertFalse(c[f"{k}_matches"] or c["verified"], k)
            chain._post_get = lambda url, timeout: dict(good)
            # another contract replaying the same OutLayer run: tx check and official flag fail
            self.assertFalse(chain.attestation_checks(snap, "attacker.testnet")["verified"])
            # the contract's pin for that date is another hash
            chain.view = lambda method, args, contract=None: ({"wasm_hash": "1" * 64}, "rpc", 1)
            self.assertFalse(chain.attestation_checks(snap, chain.OFFICIAL_CONTRACT)["verified"])
            # a worker this repo does not vouch for, even if pinned and attested consistently
            chain.view = lambda method, args, contract=None: ({"wasm_hash": a["wasm_hash"]}, "rpc", 1)
            known = chain.KNOWN_WORKERS
            chain.KNOWN_WORKERS = set()
            try:
                self.assertFalse(chain.attestation_checks(snap, chain.OFFICIAL_CONTRACT)["verified"])
            finally:
                chain.KNOWN_WORKERS = known
        finally:
            chain._post_get, chain.view, chain.tx_targets = saved

    def test_worker_input_matches_the_contract_format(self):
        with open(CACHE) as f:
            snap = json.load(f)["attested"][0]
        self.assertRegex(chain.worker_input(snap), r'^\{"n":"\d+","d":"\d{4}-\d\d-\d\d","b":"\d+","b28":"\d+"\}$')


if __name__ == "__main__":
    unittest.main()
