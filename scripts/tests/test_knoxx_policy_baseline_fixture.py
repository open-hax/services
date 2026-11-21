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

    def test_original_v1_archive_digest_cannot_be_replaced(self):
        self.refuse(lambda c: c["original_v1"].update(artifact_sha256="0" * 64))

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

    def test_mcp_continuity_refuses_coordinated_digest_substitution(self):
        for identity in ("knoxx_site", "mcp_route"):
            with self.subTest(identity=identity):
                self.refuse(lambda c: c["mcp_continuity"].update({
                    f"{moment}_{identity}_sha256": "0" * 64 for moment in ("historical", "current")
                }))

    def test_current_config_identity_refuses_coordinated_digest_substitution(self):
        self.refuse(lambda c: c["observed_current"]["config_sha256"].update(
            native="a" * 64, active="b" * 64, current="b" * 64, startup="b" * 64, bridge="c" * 64))

    def test_artifact_must_match_native_host_receipt(self):
        self.refuse(lambda c: c["native_actions_artifacts"][0].update(canonicalReceiptSha256="a" * 64))

    def test_one_owner_receipt_cannot_satisfy_another_run(self):
        def change(c):
            c["native_actions_artifacts"][0]["canonicalReceiptSha256"] = c["native_actions_artifacts"][1]["canonicalReceiptSha256"]
        self.refuse(change)

    def test_artifact_id_is_bound_to_its_exact_run(self):
        def change(c):
            c["native_actions_artifacts"][0]["artifactId"] = c["native_actions_artifacts"][1]["artifactId"]
        self.refuse(change)

    def test_archive_digest_is_bound_to_its_exact_artifact(self):
        def change(c):
            c["native_actions_artifacts"][0]["nativeArtifactSha256"] = c["native_actions_artifacts"][1]["nativeArtifactSha256"]
        self.refuse(change)

    def test_native_receipt_digests_remain_pinned_if_both_copies_change(self):
        def change(c):
            artifact = c["native_actions_artifacts"][0]
            host = next(row for row in c["host_canonical_receipts"]
                        if row["canonicalReceiptSha256"] == artifact["canonicalReceiptSha256"])
            artifact["canonicalReceiptSha256"] = host["canonicalReceiptSha256"] = "a" * 64
        self.refuse(change)

    def test_artifact_receipt_byte_digest_cannot_be_substituted(self):
        self.refuse(lambda c: c["native_actions_artifacts"][0].update(receiptFileSha256="a" * 64))

    def test_canonical_host_hash_is_bound_to_same_file_path(self):
        self.refuse(lambda c: c["host_canonical_receipts"][0].update(fileSha256="a" * 64))

    def test_host_receipt_byte_digest_remains_pinned_if_both_copies_change(self):
        def change(c):
            artifact = c["native_actions_artifacts"][0]
            canonical_host = next(row for row in c["host_canonical_receipts"]
                                  if row["canonicalReceiptSha256"] == artifact["canonicalReceiptSha256"])
            host = next(row for row in c["host_receipts"] if row["path"] == canonical_host["path"])
            canonical_host["fileSha256"] = host["sha256"] = "a" * 64
        self.refuse(change)

    def test_same_path_metadata_must_match_native_artifact(self):
        self.refuse(lambda c: c["native_actions_artifacts"][0]["receiptMetadata"].update(status="healthy-dark"))

    def test_both_metadata_copies_cannot_substitute_other_status_or_run(self):
        for substitute in ("other-status", "other-run"):
            with self.subTest(substitute=substitute):
                def change(c):
                    activation, repair = c["native_actions_artifacts"][:2]
                    host = next(row for row in c["host_receipts"] if row["path"].endswith("/live/active.json"))
                    replacement = (dict(activation["receiptMetadata"], status="healthy-dark")
                                   if substitute == "other-status" else repair["receiptMetadata"])
                    activation["receiptMetadata"] = replacement
                    host["metadata"] = copy.deepcopy(replacement)
                self.refuse(change)

    def test_protected_run_source_cannot_be_substituted(self):
        self.refuse(lambda c: c["native_actions_artifacts"][0].update(head_sha="a" * 40))

    def test_fixture_never_accepts_an_activation_or_hold_clear(self):
        self.refuse(lambda c: c.update(live_authorization=True))
        self.refuse(lambda c: c.update(production_hold_cleared=True))


if __name__ == "__main__":
    unittest.main()
