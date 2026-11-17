"""Synthetic laws for preparing an active-mailbox image upgrade."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import axxium_active_mailbox_release as upgrade
import axxium_active_mailbox_admission as admission
import axxium_production_qualification as custody
import axxium_unified_mailbox_admission as shared_admission
import axxium_minimal_release as release
from axxium_oidc_ingress import IngressRefused, MAILBOX, SITE, UPSTREAM, prepare_active_mailbox
from test_axxium_minimal_release import FakeHost
from test_axxium_production_qualification import NOW, create_archive


class ActiveMailboxProjectionTests(unittest.TestCase):
    def setUp(self):
        self.current = {"name": "axxium", "services": {
            "axxium": {"image": upgrade.PLAN["incumbent"]["image_id"],
                       "environment": {"JWT_AUDIENCE": "promethean+production",
                                       "AXXIUM_MAILBOX_STREAM_ENABLED": "true",
                                       "AXXIUM_MAILBOX_SUBSCRIPTIONS_ENABLED": "false",
                                       "AXXIUM_IDENTITY_ROOT_DID": "did:web:fixture.example:actors:owner"},
                       "volumes": [{"source": "/retained/mailbox.edn", "target": "/var/lib/axxium/mailbox/ledger.edn",
                                    "type": "bind", "read_only": False, "bind": {"create_host_path": False}}],
                       "depends_on": {"database": {"condition": "service_healthy"}}},
            "database": {"image": "postgres:retained"}, "pds": {"image": "pds:retained"}}}
        self.candidate = deepcopy(self.current)
        self.candidate["services"]["axxium"]["image"] = upgrade.PLAN["image_id"]

    def test_image_only_upgrade_retains_active_mailbox_and_inputs(self):
        before, candidate = deepcopy(self.current), deepcopy(self.candidate)
        self.assertEqual(upgrade.validate_projection(self.current, self.candidate),
                         self.candidate["services"]["axxium"])
        self.assertEqual(self.current, before)
        self.assertEqual(self.candidate, candidate)

    def test_transport_binding_mount_dependency_and_image_changes_refuse(self):
        variants = []
        for key, value in (("AXXIUM_MAILBOX_SUBSCRIPTIONS_ENABLED", "true"),
                           ("AXXIUM_MAILBOX_STREAM_ENABLED", "false"),
                           ("AXXIUM_MAILBOX_TRANSPORT_COEXISTENCE", "true"),
                           ("AXXIUM_IDENTITY_ROOT_DID", "did:web:other.example:actors:owner")):
            changed = deepcopy(self.candidate)
            changed["services"]["axxium"]["environment"][key] = value
            variants.append(changed)
        changed = deepcopy(self.candidate)
        changed["services"]["axxium"]["volumes"] = []
        variants.append(changed)
        changed = deepcopy(self.candidate)
        changed["services"]["database"]["image"] = "postgres:other"
        variants.append(changed)
        changed = deepcopy(self.candidate)
        changed["services"]["axxium"]["image"] = upgrade.PLAN["image_manifest_digest"]
        variants.append(changed)
        for candidate in variants:
            with self.subTest(candidate=candidate), self.assertRaises((upgrade.Refusal, RuntimeError)):
                upgrade.validate_projection(self.current, candidate)

    def test_rollback_ledger_copies_retain_acknowledgements_and_refuse_rewrites(self):
        accepted = b'{:event/type :axxium.mailbox/accepted :event/body {:message/id "fixture"}}\n'
        ack = b'{:event/type :axxium.mailbox/acknowledged :event/body {:message/id "fixture"}}\n'
        before = {"mailbox": accepted, "identity": b"identity-before\n"}
        current = {"mailbox": accepted + ack, "identity": b"identity-before\nidentity-after\n"}
        self.assertEqual(upgrade.preserve_ledgers(before, current), current)
        self.assertTrue(upgrade.preserve_ledgers(before, current)["mailbox"].endswith(ack))
        for changed in ({"mailbox": accepted[:-1], "identity": current["identity"]},
                        {"mailbox": b"rewritten\n", "identity": current["identity"]},
                        {"mailbox": current["mailbox"]}):
            with self.subTest(changed=changed), self.assertRaises(upgrade.Refusal):
                upgrade.preserve_ledgers(before, changed)


class ActiveMailboxCustodyTests(unittest.TestCase):
    def test_complete_fixed_plan_pins_candidate_and_already_active_incumbent(self):
        self.assertEqual(upgrade.validate_plan(deepcopy(upgrade.PLAN)), upgrade.PLAN)
        self.assertNotEqual(upgrade.PLAN["image_id"], upgrade.PLAN["image_manifest_digest"])
        self.assertNotEqual(upgrade.PLAN["image_manifest_digest"], upgrade.PLAN["image_config_digest"])
        for key in upgrade.PLAN:
            changed = deepcopy(upgrade.PLAN)
            changed[key] = None
            with self.subTest(key=key), self.assertRaises(upgrade.Refusal):
                upgrade.validate_plan(changed)
        for field in upgrade.PLAN["expected"]:
            changed = deepcopy(upgrade.PLAN)
            changed["expected"][field] = "other"
            with self.subTest(field=field), self.assertRaises(upgrade.Refusal):
                upgrade.validate_plan(changed)

    def test_missing_new_qualification_refuses_before_stage_or_archive_admission(self):
        with tempfile.NamedTemporaryFile() as archive:
            with (mock.patch.object(shared_admission, "main_ancestor"),
                  mock.patch.object(shared_admission, "native_file", side_effect=[upgrade.PLAN_BYTES, OSError()]) as files,
                  mock.patch.object(shared_admission, "verify_native_stage") as stage):
                with self.assertRaisesRegex(upgrade.Refusal, "immutable unified mailbox source qualification unavailable"):
                    admission.admit_active_mailbox(upgrade.PLAN_BYTES, "f" * 40, Path(archive.name), now=NOW)
            self.assertEqual(files.call_args_list[0].args[1], upgrade.PLAN_PATH)
            self.assertEqual(files.call_args_list[1].args[1],
                             f'digitalocean/qualifications/axxium/{upgrade.SOURCE}-active-upgrade.json')
            stage.assert_not_called()

    def test_old_qualification_cannot_be_relabelled_for_new_source_or_plan(self):
        old_path = upgrade.ROOT / "digitalocean/qualifications/axxium" / (upgrade.INCUMBENT_SOURCE + ".json")
        old = json.loads(old_path.read_text())
        with self.assertRaisesRegex(upgrade.Refusal, "does not bind"):
            shared_admission.verify_qualification(old, upgrade.PLAN, upgrade.PLAN_SHA256, NOW)
        modified = deepcopy(old)
        modified.update(source_sha=upgrade.SOURCE, image_id=upgrade.IMAGE, plan_sha256=upgrade.PLAN_SHA256)
        with self.assertRaisesRegex(upgrade.Refusal, "integration"):
            shared_admission.verify_qualification(modified, upgrade.PLAN, upgrade.PLAN_SHA256, NOW)

    def test_exact_retained_inputs_refuse_missing_changed_and_size_drift(self):
        data = {"/fixture/mailbox.env": b"synthetic config"}
        rows = [{"path": path, "bytes": len(value), "sha256": sha256(value).hexdigest()} for path, value in data.items()]
        upgrade.verify_inputs(rows, data)
        for altered in ({}, {"/fixture/mailbox.env": b"changed"}, {**data, "/other": b"unexpected"}):
            with self.subTest(altered=altered), self.assertRaises(upgrade.Refusal):
                upgrade.verify_inputs(rows, altered)

    def test_incumbent_receipt_requires_exact_bytes_and_healthy_mailbox_lineage(self):
        prior = b'{"schema":"axxium-unified-mailbox-production/v1"}'
        expected = deepcopy(upgrade.PLAN["incumbent"])
        expected["plan_sha256"] = sha256(prior).hexdigest()
        receipt = {"schema": "axxium-unified-mailbox-deployment/v1", "status": "healthy",
                   **{key: expected[key] for key in ("source_sha", "image_id", "app_container_id", "plan_sha256")}}
        raw = json.dumps(receipt).encode()
        expected["receipt_sha256"] = sha256(raw).hexdigest()
        plan = {**upgrade.PLAN, "incumbent": expected}
        with mock.patch.object(upgrade, "PLAN", plan):
            upgrade.verify_incumbent_receipt(raw, prior)
            with self.assertRaisesRegex(upgrade.Refusal, "bytes differ"):
                upgrade.verify_incumbent_receipt(raw + b"\n", prior)
            receipt["status"] = "rolled_back"
            invalid = json.dumps(receipt).encode()
            expected["receipt_sha256"] = sha256(invalid).hexdigest()
            with self.assertRaisesRegex(upgrade.Refusal, "lineage"):
                upgrade.verify_incumbent_receipt(invalid, prior)

    def test_archive_gate_binds_inner_tar_digest_as_well_as_outer_zip(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "stage.zip"
            image = create_archive(archive, nested=True)
            with zipfile.ZipFile(archive) as package:
                checksum = package.read("images.sha256").decode().strip()
            plan = {"image_id": image, "source_sha": "a" * 40,
                    "artifact_sha256": sha256(archive.read_bytes()).hexdigest(), "images_tar_sha256": checksum}
            # The existing fixture carries the canonical source used by custody tests.
            from test_axxium_production_qualification import SOURCE
            plan["source_sha"] = SOURCE
            self.assertEqual(custody.verify_archive(plan, archive), image)
            plan["images_tar_sha256"] = "0" * 64
            with self.assertRaisesRegex(upgrade.Refusal, "reviewed archive digest"):
                custody.verify_archive(plan, archive)


class ActiveMailboxControllerTests(unittest.TestCase):
    def test_preflight_rejects_live_environment_and_mount_drift_before_effects(self):
        host = upgrade.Host(upgrade.PLAN_SHA256)
        image = upgrade.PLAN["incumbent"]["image_id"]
        service = {"environment": {**upgrade.PLAN["transport"], "MAILBOX_CONFIG": "retained", "UNSET": None},
                   "volumes": [{"type": "bind", "source": "/retained/mailbox.edn",
                                "target": "/var/lib/axxium/mailbox/ledger.edn", "read_only": False},
                               {"type": "bind", "source": "/retained/authority.json",
                                "target": "/run/axxium/authority.json", "read_only": True}]}
        environment = {"PATH": "/image/default", **service["environment"]}
        environment.pop("UNSET")
        current = {"Image": image, "Config": {"Env": [f"{k}={v}" for k, v in environment.items()]},
                   "Mounts": [{"Type": row["type"], "Source": row["source"], "Destination": row["target"],
                               "RW": not row["read_only"]} for row in service["volumes"]]}
        image_config = {"Config": {"Env": ["PATH=/image/default", "UNSET=from-image"]}}
        host.identity_check = mock.Mock()
        host.mailbox_projection = mock.Mock(return_value=service)
        selected = [current]
        host.inspect = mock.Mock(side_effect=lambda name: selected[0] if name == release.APP else image_config)
        host.command = mock.Mock()
        host.identity_preflight()
        self.assertEqual(host.inspect.call_args_list, [mock.call(release.APP), mock.call(image)])
        changed_environment = deepcopy(current)
        changed_environment["Config"]["Env"].append("MAILBOX_CONFIG=drifted")
        changed_mount = deepcopy(current)
        changed_mount["Mounts"][0]["Source"] = "/other/mailbox.edn"
        changed_permissions = deepcopy(current)
        changed_permissions["Mounts"][1]["RW"] = True
        for actual, reason in ((changed_environment, "environment"), (changed_mount, "mounts"),
                               (changed_permissions, "mounts")):
            selected[0] = actual
            with self.subTest(reason=reason), self.assertRaisesRegex(release.Uncertain, reason + " differ"):
                host.identity_preflight()
        host.command.assert_not_called()

    def test_host_requires_the_exact_successor_plan_digest_before_any_capability(self):
        with mock.patch.object(upgrade.IngressHost, "__init__") as constructor:
            for digest in (None, "f" * 64, upgrade.PLAN["incumbent"]["plan_sha256"]):
                with self.subTest(digest=digest), self.assertRaises(upgrade.Refusal):
                    upgrade.Host(digest)
            constructor.assert_not_called()

    def test_existing_hold_and_operation_refuse_retry_without_replaying_effects(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            hold = root / "pending"
            hold.write_bytes(b"retained original hold")
            host = upgrade.Host(upgrade.PLAN_SHA256)
            host._preflight_shared = mock.Mock()
            with mock.patch.object(release, "PENDING", hold), mock.patch.object(release.os, "getuid", return_value=1000):
                with self.assertRaisesRegex(release.Refused, "independent recovery"):
                    host.preflight(upgrade.PLAN)
            host._preflight_shared.assert_not_called()
            self.assertEqual(hold.read_bytes(), b"retained original hold")
            operations = root / "operations"
            operations.mkdir(mode=0o700)
            operation = operations / host.operation_name(upgrade.PLAN)
            operation.mkdir(mode=0o700)
            marker = operation / "original-journal.json"
            marker.write_bytes(b"retained operation")
            with mock.patch.object(release, "STATE", root):
                with self.assertRaisesRegex(release.Refused, "no replay"):
                    host.claim(upgrade.PLAN)
            self.assertEqual(marker.read_bytes(), b"retained operation")

    def test_active_ingress_fences_without_changing_live_topology(self):
        current = SITE + MAILBOX + UPSTREAM + b"}\n"
        fenced, live = prepare_active_mailbox(current, sha256(current).hexdigest())
        self.assertEqual(live, current)
        self.assertIn(b'respond "Axxium sign-in is updating" 503', fenced)
        for bad, digest in ((current + b"changed", sha256(current).hexdigest()),
                            (current.replace(MAILBOX, b"other\n"), None), (current + MAILBOX, None)):
            with self.subTest(bad=bad), self.assertRaises(IngressRefused):
                prepare_active_mailbox(bad, digest or sha256(bad).hexdigest())

    def test_identity_checkpoint_never_invokes_a_provisioner(self):
        host = upgrade.Host(upgrade.PLAN_SHA256)
        host.unchanged_dependencies = mock.Mock()
        host.command = mock.Mock()
        retained = {"mailbox": b"accepted\nack\n", "identity": b"owner\n"}
        host.ledger_snapshots["before"] = retained
        with mock.patch.object(upgrade, "ledger_bytes", return_value=retained):
            host.provision_identity(upgrade.PLAN)
        host.command.assert_not_called()

    def test_both_rehearsal_images_use_same_retained_copies_with_acknowledgments(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            paths = {key: base / (key + ".edn") for key in ("mailbox", "identity")}
            state = {"mailbox": b"accepted\nack\n", "identity": b"existing actor\n"}
            for key, path in paths.items():
                path.write_bytes(state[key])
                path.chmod(0o600)
            host = upgrade.Host(upgrade.PLAN_SHA256)
            host.operation = base
            host.backups["before"] = {"database": "axxium_verify_" + "a" * 24}
            host.ledger_snapshots["before"] = state
            host.private_write = lambda path, raw: (path.write_bytes(raw), path.chmod(0o600))
            host.command = mock.Mock()
            service = {"environment": {**upgrade.PLAN["transport"], "JWT_AUDIENCE": "promethean+production"},
                       "volumes": [
                           {"type": "bind", "source": str(paths["mailbox"]), "target": "/var/lib/axxium/mailbox/ledger.edn"},
                           {"type": "bind", "source": str(paths["identity"]), "target": "/var/lib/axxium/identity/ledger.edn"},
                           {"type": "bind", "source": "/retained/oidc", "target": "/run/axxium/oidc", "read_only": True}]}
            host.mailbox_projection = mock.Mock(return_value=service)
            seen = []

            def probe(plan, database, image, credentials, signup, *, candidate):
                values, mounts = host.probe_profile(plan, image, "fixture", {}, candidate=candidate)
                self.assertEqual(values["AXXIUM_MAILBOX_SUBSCRIPTIONS_ENABLED"], "false")
                self.assertEqual(upgrade.ledger_bytes(host.probe_ledgers)["mailbox"], state["mailbox"])
                self.assertNotIn("AXXIUM_MAILBOX_TRANSPORT_COEXISTENCE", values)
                self.assertNotIn(f'src={paths["mailbox"]}', " ".join(mounts))
                seen.append((image, dict(host.probe_ledgers)))

            host.probe = probe
            with mock.patch.object(upgrade, "LEDGERS", paths):
                host.rehearse(upgrade.PLAN, "before", candidate=True)
            self.assertEqual([row[0] for row in seen], [upgrade.IMAGE, upgrade.PLAN["incumbent"]["image_id"]])
            self.assertEqual(seen[0][1], seen[1][1])
            self.assertEqual(upgrade.ledger_bytes(paths), state)

    def test_candidate_and_rollback_compose_retain_all_four_active_layers(self):
        host = upgrade.Host(upgrade.PLAN_SHA256)
        host.operation = Path("/fixture/operation")
        args = host.compose_command(host.operation / "rollback.compose.json")
        layers = [args[i + 1] for i, item in enumerate(args) if item == "-f"]
        self.assertEqual(layers[:-1], [row["path"] for row in upgrade.PLAN["compose_chain"]])
        self.assertEqual(layers[-1], "/fixture/operation/rollback.compose.json")
        self.assertIn(str(upgrade.IDENTITY / "mailbox.env"), args)
        self.assertEqual(host.compose_environment()["AXXIUM_PRODUCTION_MAILBOX_IMAGE"],
                         upgrade.PLAN["incumbent"]["image_id"])

    def test_shared_transaction_order_and_uncertain_effect_never_replay_or_rollback(self):
        host = FakeHost()
        self.assertEqual(upgrade.execute_admitted(upgrade.PLAN, host), {"status": "healthy"})
        self.assertLess(host.calls.index("backup_before"), host.calls.index("provision_identity"))
        uncertain = FakeHost("start", uncertain=True)
        with self.assertRaises(release.Uncertain):
            upgrade.execute_admitted(upgrade.PLAN, uncertain)
        self.assertNotIn("rollback", uncertain.calls)
        self.assertNotIn("finish_healthy", uncertain.calls)
        active = upgrade.Host(upgrade.PLAN_SHA256)
        active.command = mock.Mock()
        for method in (active.recovery_preflight, active.recovery_claim):
            with self.assertRaises(release.Refused):
                method(upgrade.PLAN)
        active.command.assert_not_called()

    def test_active_rollback_checks_current_ledger_state_and_keeps_mailbox_available(self):
        host = upgrade.Host(upgrade.PLAN_SHA256)
        before = {"mailbox": b"accepted\n", "identity": b"owner\n"}
        current = {"mailbox": b"accepted\nack\n", "identity": b"owner\nchild\n"}
        host.ledger_snapshots["before"] = before
        host.identity_check = mock.Mock()
        host.verify_active_runtime = mock.Mock()
        host.public_health = mock.Mock()
        with mock.patch.object(upgrade, "ledger_bytes", return_value=current):
            host.verify_rollback(upgrade.PLAN, {"Image": upgrade.PLAN["incumbent"]["image_id"]})
        host.public_health.assert_called_once_with(upgrade.PLAN)

    def test_native_rollback_selects_old_image_and_retains_new_acknowledgments(self):
        with tempfile.TemporaryDirectory() as directory:
            host = upgrade.Host(upgrade.PLAN_SHA256)
            host.operation = Path(directory)
            host.incumbent_image_id = upgrade.PLAN["incumbent"]["image_id"]
            host.candidate_id = "candidate"
            host.original, host.maintenance = b"live", b"fenced"
            candidate = {"Id": "candidate", "Image": upgrade.IMAGE}
            old = {"Id": "restored", "Image": host.incumbent_image_id, "State": {"Health": {"Status": "healthy"}}}
            selected = [candidate]
            host.inspect = lambda _: selected[0]
            host.unchanged_dependencies = mock.Mock()
            host.replace_caddy = mock.Mock()
            host.stop_app = mock.Mock()
            host.backup = mock.Mock()
            host.rehearse = mock.Mock()
            host.compose = mock.Mock(side_effect=lambda *args, **kw: selected.__setitem__(0, old))
            host.wait_app = mock.Mock()
            host.environment_hash = lambda _: upgrade.PLAN["expected"]["app_environment_sha256"]
            host.http = lambda _: (200, b"")
            host.identity_check = mock.Mock()
            host.verify_active_runtime = mock.Mock()
            host.public_health = mock.Mock()
            host.private_write = mock.Mock()
            before = {"mailbox": b"accepted\n", "identity": b"owner\n"}
            current = {"mailbox": b"accepted\nack\n", "identity": b"owner\n"}
            host.ledger_snapshots["before"] = before
            with mock.patch.object(upgrade, "ledger_bytes", return_value=current):
                host.rollback(upgrade.PLAN, stopped=True, candidate_attempted=True)
            host.backup.assert_called_once_with(upgrade.PLAN, "failed")
            host.rehearse.assert_called_once_with(upgrade.PLAN, "failed", candidate=False)
            host.public_health.assert_called_once_with(upgrade.PLAN)
            paths = [call.args[0] for call in host.private_write.call_args_list]
            self.assertTrue(all(path.parent == host.operation for path in paths))
            overlay = json.loads(host.private_write.call_args_list[0].args[1])
            self.assertEqual(overlay, upgrade.image_overlay(host.incumbent_image_id))
            receipt = json.loads(host.private_write.call_args_list[-1].args[1])
            self.assertEqual(receipt["image_id"], host.incumbent_image_id)
            self.assertEqual(receipt["incumbent_source_sha"], upgrade.INCUMBENT_SOURCE)
            self.assertIsNone(receipt["source_sha"])

    def test_existing_work_binding_query_is_read_only_and_exact(self):
        host = upgrade.Host(upgrade.PLAN_SHA256)
        files = {row["path"]: b"synthetic" for row in upgrade.PLAN["retained_config"]}
        rows = [{"path": path, "bytes": len(raw), "sha256": sha256(raw).hexdigest()} for path, raw in files.items()]
        plan = {**upgrade.PLAN, "retained_config": rows,
                "identity": {**upgrade.PLAN["identity"], "actor_signing_sha256": sha256(b"synthetic").hexdigest()}}
        host.sql = mock.Mock(return_value=(upgrade.PLAN["identity"]["work_actor_id"] + "\n").encode())
        with mock.patch.object(upgrade, "PLAN", plan), mock.patch.object(release, "private_read", return_value=b"synthetic"):
            host.identity_check()
            query = host.sql.call_args.args[1]
            self.assertTrue(query.startswith("BEGIN READ ONLY;"))
            self.assertTrue(query.endswith("ROLLBACK;"))
            self.assertIn(upgrade.PLAN["identity"]["work_atproto_did"], query)
            host.sql.return_value = b"other-actor\n"
            with self.assertRaisesRegex(release.Uncertain, "Work binding differs"):
                host.identity_check()


if __name__ == "__main__":
    unittest.main()
