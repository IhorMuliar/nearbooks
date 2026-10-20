"""Pipeline tests. Run: python3 -m unittest tests.test_pipeline -v"""
import copy, json, os, shutil, subprocess, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pipeline"))
import compute, verify  # noqa: E402
from common import load_json  # noqa: E402

SNAP = os.path.join(ROOT, "data", "snapshot-2026-10-20")
SNAP_M2 = os.path.join(ROOT, "data", "snapshot-2026-10-20-m2")


def rehash(d):
    """Rewrite a snapshot manifest after an edit, as a careful forger would."""
    p = os.path.join(d, "manifest.json")
    man = load_json(p)
    man["sha256"] = {f: compute.sha256_file(os.path.join(d, f)) for f in man["sha256"]}
    with open(p, "w") as f:
        json.dump(man, f, indent=1)


class TestPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = compute.compute(SNAP, SNAP_M2)

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
        rec = lambda x: x["buyback"]["reconciliation"]
        self.assertIn("F2a", failing(lambda x: rec(x).update(unexplained_yocto=1)))
        self.assertIn("F2a", failing(lambda x: rec(x).update(nontransfer_near=-50000.0)))
        self.assertIn("F2e", failing(lambda x: rec(x)["dashboard"]["wallets"]["buybacks.multisignature.near"].update(all_time_diff_near=0.2)))
        self.assertIn("F2e", failing(lambda x: rec(x)["dashboard"]["wallets"]["fefundsadmin.sputnik-dao.near"].update(all_time_ledger_unexplained_yocto=-5)))
        self.assertIn("F2e", failing(lambda x: rec(x)["dashboard"].update(full_history=False)))
        self.assertIn("F2f", failing(lambda x: rec(x)["altcoinbuzz"].update(gap_closure_near=1.0)))
        self.assertIn("F2f", failing(lambda x: rec(x)["altcoinbuzz"].update(dashboard_matching_day="2026-10-19")))
        self.assertIn("F2f", failing(lambda x: rec(x)["altcoinbuzz"].update(nearblocks_diff_near=0.01)))
        self.assertIn("F2b", failing(lambda x: x["supply"].update(increasing_intervals=x["supply"]["intervals"] - 1)))
        self.assertIn("F2c", failing(lambda x: x["buyback"].update(share_reached_pct=100.0)))
        smp = lambda x: x["sample"]
        self.assertIn("F5a", failing(lambda x: smp(x).update(blocks_outside_their_month=1)))
        self.assertIn("F5a", failing(lambda x: smp(x).update(blocks=smp(x)["blocks"] - 1)))
        def majority_channel(x):
            P = smp(x)["channel_mix_pooled"]
            named = sorted((c for c in smp(x)["channels"] if c != "other"), key=lambda c: -(P[c]["p"] or 0))
            P[named[0]]["hi"] = 0.55
        self.assertIn("F5b", failing(majority_channel))
        def flat_meta(x):
            mt, fm = smp(x)["meta_tx_by_month"], smp(x)["full_months"]
            mt[fm[-1]].update(lo=mt[fm[0]]["lo"], hi=mt[fm[0]]["hi"], p=mt[fm[0]]["p"])
        self.assertIn("F6a", failing(flat_meta))
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

    def _tampered_m2(self, edit):
        """Copy the M2 snapshot, apply `edit(dir)`, re-hash its manifest, recompute; return metrics or the SystemExit."""
        tmp = tempfile.mkdtemp(prefix="nbtamper2")
        try:
            d = os.path.join(tmp, "m2"); shutil.copytree(SNAP_M2, d)
            edit(d); rehash(d)
            try:
                return compute.compute(SNAP, d)
            except SystemExit as e:
                return e
        finally:
            shutil.rmtree(tmp)

    def test_m2_snapshot_tampering_is_detected(self):
        tmp = tempfile.mkdtemp(prefix="nbtamper2")
        try:
            d = os.path.join(tmp, "m2"); shutil.copytree(SNAP_M2, d)
            with open(os.path.join(d, "neardata_sample.jsonl"), "a") as f:
                f.write("\n")
            with self.assertRaises(SystemExit) as cm:
                compute.compute(SNAP, d)
            self.assertIn("integrity failure", str(cm.exception))
        finally:
            shutil.rmtree(tmp)

    def test_m2_snapshot_is_bound_to_its_main_snapshot(self):
        r = self._tampered_m2(lambda d: self._edit_json(os.path.join(d, "manifest.json"), lambda m: m.update(main_manifest_sha256="0" * 64)))
        self.assertIsInstance(r, SystemExit)
        self.assertIn("different main snapshot", str(r))

    def test_forged_pre2026_history_breaks_the_dashboard_reconciliation(self):
        """Inflate one pre-2026 inflow and re-hash the manifest: the yocto ledger no longer closes, so F2e fails."""
        def edit(d):
            p = os.path.join(d, "transfers_pre_fefundsadmin.sputnik-dao.near.jsonl")
            with open(p) as f:
                rows = [json.loads(l) for l in f]
            i = next(i for i, r in enumerate(rows) if r["asset_id"] == "native:near" and r["other_account_id"] == "auroraliqsupport.near")
            rows[i]["amount"] = str(int(rows[i]["amount"]) + 10 ** 24)
            with open(p, "w") as f:
                f.writelines(json.dumps(r, sort_keys=True) + "\n" for r in rows)
        m = self._tampered_m2(edit)
        self.assertNotIsInstance(m, SystemExit)
        self.assertIn("F2e", {f["id"] for f in verify.findings(m) if not f["ok"]})

    def test_sample_must_be_the_seeded_plan(self):
        """A re-seeded plan, or a dropped block (cherry-picking), is refused even with a consistent manifest."""
        r = self._tampered_m2(lambda d: self._edit_json(os.path.join(d, "neardata_plan.json"), lambda p: p.update(seed=p["seed"] + 1)))
        self.assertIsInstance(r, SystemExit)
        self.assertIn("seeded plan", str(r))
        def drop(d):
            p = os.path.join(d, "neardata_sample.jsonl")
            with open(p) as f:
                lines = f.readlines()
            with open(p, "w") as f:
                f.writelines(lines[:-1])
        r = self._tampered_m2(drop)
        self.assertIsInstance(r, SystemExit)
        self.assertIn("seeded plan", str(r))

    def test_wilson_and_ratio_estimators(self):
        import stats
        lo, hi = stats.wilson(0.5, 100)
        self.assertAlmostEqual(lo, 0.4038, places=3); self.assertAlmostEqual(hi, 0.5962, places=3)
        self.assertEqual(stats.wilson(0.0, 0), (0.0, 1.0))
        # one block holding all units: the cluster is one draw, so the interval is wider than for independent units
        clustered = stats.ratio_share([10, 0, 0, 0], [10, 1, 1, 1])
        spread = stats.ratio_share([3, 3, 2, 2], [4, 3, 3, 3])
        self.assertLess(clustered["n_eff"], clustered["units"])
        self.assertLessEqual(spread["n_eff"], spread["units"])

    def test_wrong_pinned_dashboard_figure_fails_f2e(self):
        pub = compute.CONFIG["published_figures"]["revenue_dashboard"]["wallet_total_near"]
        old = pub["buybacks.multisignature.near"]
        try:
            pub["buybacks.multisignature.near"] = old + 0.2
            m = compute.compute(SNAP, SNAP_M2)
        finally:
            pub["buybacks.multisignature.near"] = old
        self.assertIn("F2e", {f["id"] for f in verify.findings(m) if not f["ok"]})

    @staticmethod
    def _edit_json(p, fn):
        x = load_json(p); fn(x)
        with open(p, "w") as f:
            json.dump(x, f, indent=1)

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
