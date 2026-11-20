import copy
import unittest

from scripts import verify_knoxx_policy_baseline_fixture as fixture


class BaselineFixtureTest(unittest.TestCase):
    def setUp(self):
        self.certificate = fixture.read_json(fixture.FIXTURE)

    def refuse(self, change):
        candidate = copy.deepcopy(self.certificate)
        change(candidate)
        with self.assertRaises(fixture.FixtureRefused):
            fixture.verify(candidate)

    def test_exact_captured_fixture_verifies_without_mutating_inputs(self):
        original = copy.deepcopy(self.certificate)
        self.assertEqual(fixture.verify(self.certificate)["status"], "fixture-verified")
        self.assertEqual(original, self.certificate)
        self.assertFalse(fixture.verify(self.certificate)["live_authorization"])

    def test_v1_seal_cannot_be_replaced(self):
        self.refuse(lambda c: c["original_v1"].update(receipt_sha256="a" * 64))

    def test_both_original_hashes_must_be_reconstructed(self):
        self.refuse(lambda c: c["historical_reconstruction"].update(active_config_sha256="a" * 64))

    def test_extra_managed_fragment_fails_even_with_recomputed_digest(self):
        def change(c):
            rows = c["observed_current"]["managed"]
            rows.append({"name": "unknown.caddy", "sha256": "a" * 64})
            rows.sort(key=lambda row: row["name"])
            c["observed_current"]["managed_sha256"] = fixture.canonical(rows)
        self.refuse(change)

    def test_changed_fragment_fails_after_repaired_local_digest(self):
        def change(c):
            rows = c["observed_current"]["managed"]
            rows[0]["sha256"] = "a" * 64
            c["observed_current"]["managed_sha256"] = fixture.canonical(rows)
        self.refuse(change)

    def test_mcp_origin_and_handler_must_remain_exact(self):
        self.refuse(lambda c: c.update(public_mcp_origin="https://staging.knoxx.promethean.rest/mcp"))
        self.refuse(lambda c: c["mcp_continuity"].update(current_mcp_route_sha256="a" * 64))

    def test_artifact_must_match_native_host_receipt(self):
        self.refuse(lambda c: c["native_actions_artifacts"][0].update(canonicalReceiptSha256="a" * 64))

    def test_protected_run_source_cannot_be_substituted(self):
        self.refuse(lambda c: c["native_actions_artifacts"][0].update(head_sha="a" * 40))

    def test_fixture_never_accepts_an_activation_or_hold_clear(self):
        self.refuse(lambda c: c.update(live_authorization=True))
        self.refuse(lambda c: c.update(production_hold_cleared=True))


if __name__ == "__main__":
    unittest.main()
