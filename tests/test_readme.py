"""The README's generated numbers cannot drift: test counts, deployment hashes and local builds are checked."""
import json, os, shutil, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "pipeline"))
import readme_facts, verify  # noqa: E402
from common import load_json  # noqa: E402

METRICS = os.path.join(ROOT, "data", "snapshot-2026-10-20", "metrics.json")


class TestReadmeFacts(unittest.TestCase):
    def test_test_counts_match_the_suites(self):
        loader = unittest.TestLoader()
        n = sum(s.countTestCases() for s in loader.discover(os.path.join(ROOT, "tests"), top_level_dir=ROOT))
        self.assertEqual(readme_facts.test_counts()["python"], n)

    def test_a_changed_test_count_makes_the_readme_stale(self):
        m = load_json(METRICS)
        with open(os.path.join(ROOT, "README.md")) as f:
            txt = f.read()
        orig = readme_facts.test_counts
        try:
            readme_facts.test_counts = lambda: dict(orig(), contract=orig()["contract"] + 1)
            stale = [b for b, e, body in verify.generated_blocks(m) if b + "\n" + body + "\n" + e not in txt]
        finally:
            readme_facts.test_counts = orig
        self.assertIn(readme_facts.marker("test-output")[0], stale)
        self.assertIn(readme_facts.marker("summary")[0], stale)

    def test_a_local_build_that_differs_from_the_deployment_is_reported(self):
        tmp = tempfile.mkdtemp()
        try:
            os.makedirs(os.path.join(tmp, "contract", "res"))
            shutil.copy(os.path.join(ROOT, "contract", "deployment.json"), os.path.join(tmp, "contract"))
            with open(os.path.join(tmp, "contract", "res", "nearbooks.wasm"), "wb") as f:
                f.write(b"\0asm not the deployed code")
            orig = readme_facts.ROOT
            readme_facts.ROOT = tmp
            try:
                problems = readme_facts.check_builds()
            finally:
                readme_facts.ROOT = orig
            self.assertEqual(len(problems), 1)
            self.assertIn("contract/res/nearbooks.wasm", problems[0])
        finally:
            shutil.rmtree(tmp)

    def test_deployment_record_is_consistent(self):
        d = load_json(os.path.join(ROOT, "contract", "deployment.json"))
        self.assertTrue(d["worker_url"].endswith(d["worker_sha256"] + ".wasm"))
        self.assertEqual(load_json(os.path.join(ROOT, "site", "contract-cache.json"))["contract"], d["contract"])
        with open(os.path.join(ROOT, "contract", "testnet-attested.log")) as f:
            log = f.read()
        self.assertIn(f"contract wasm sha256: {d['code_sha256']}", log, "the logged deploy used this build")
        with open(os.path.join(ROOT, "contract", "testnet-resolve.log")) as f:
            log += f.read()
        for _, tx in d["transactions"]:
            self.assertIn(tx, log)


if __name__ == "__main__":
    unittest.main()
