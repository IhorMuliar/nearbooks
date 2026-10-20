"""Pipeline tests. Run: python3 -m unittest tests.test_pipeline -v"""
import copy, json, os, shutil, subprocess, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pipeline"))
import compute, verify  # noqa: E402
from common import load_json  # noqa: E402

SNAP = os.path.join(ROOT, "data", "snapshot-2026-10-20")


class TestPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = compute.compute(SNAP)

    def test_compute_is_deterministic_and_matches_committed_metrics(self):
        self.assertEqual(json.loads(json.dumps(self.m)), load_json(os.path.join(SNAP, "metrics.json")))

    def test_all_findings_pass_on_snapshot(self):
        bad = [f["id"] for f in verify.findings(self.m) if not f["ok"]]
        self.assertEqual(bad, [])

    def test_site_copy_is_current(self):
        self.assertEqual(load_json(os.path.join(ROOT, "site", "metrics.json")), json.loads(json.dumps(self.m)))

    def test_snapshot_values_are_integers_in_micro_units(self):
        sn = self.m["snapshot"]["metrics"]
        self.assertTrue(all(isinstance(v, int) for v in sn.values()))
        self.assertEqual(sn["supply_total_near"], round(self.m["supply"]["last_near"] * 1e6))

    def test_findings_are_not_vacuous(self):
        """Corrupt the metrics in ways a wrong pipeline might and check that the matching finding fails."""
        def failing(mutate):
            x = copy.deepcopy(self.m); mutate(x)
            return {f["id"] for f in verify.findings(x) if not f["ok"]}
        self.assertIn("F1a", failing(lambda x: x["reconciliation"]["rows"][0].update(onchain=x["reconciliation"]["rows"][0]["onchain"] * 1.10, delta_pct=10.0)))
        self.assertIn("F1b", failing(lambda x: x["reconciliation"]["pearson_by_lag"].update({"1": 0.9995})))
        self.assertIn("F2a", failing(lambda x: x["buyback"]["all_wallets"][x["buyback"]["wallet"]].update(balance_residual_near=-50000.0)))
        self.assertIn("F2b", failing(lambda x: x["supply"].update(increasing_intervals=x["supply"]["intervals"] - 1)))
        self.assertIn("F2c", failing(lambda x: x["buyback"].update(share_reached_pct=100.0)))
        self.assertIn("F3a", failing(lambda x: x["hsp029"]["windows"]["92"].update(treasury_share_pct=12.0)))
        self.assertIn("F4a", failing(lambda x: x["deflation"].update(net_issuance_near_per_day=1.0, breakeven_over_current_volume=0.001)))

    def test_snapshot_tampering_is_detected(self):
        tmp = tempfile.mkdtemp(prefix="nbtamper")
        try:
            d = os.path.join(tmp, "s"); shutil.copytree(SNAP, d)
            with open(os.path.join(d, "price.json"), "a") as f:
                f.write(" ")
            with self.assertRaises(SystemExit) as cm:
                compute.compute(d)
            self.assertIn("integrity failure", str(cm.exception))
        finally:
            shutil.rmtree(tmp)

    def test_every_prediction_metric_exists_in_the_onchain_snapshot(self):
        keys = set(self.m["snapshot"]["metrics"])
        for p in self.m["predictions"]:
            self.assertIn(p["metric"], keys)
            self.assertIn(p["comparator"], {"gt", "gte", "lt", "lte"})
            self.assertGreater(p["resolve_after"], self.m["as_of"])

    def test_readme_block_is_current(self):
        r = subprocess.run([sys.executable, "-I", os.path.join(ROOT, "pipeline", "verify.py"), os.path.join(SNAP, "metrics.json"), "--check-readme", os.path.join(ROOT, "README.md")], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
