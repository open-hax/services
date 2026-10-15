#!/usr/bin/env python3
"""Exercise exact-candidate promotion without granting real approval."""
import copy
import importlib.util
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.dont_write_bytecode = True
SPEC = importlib.util.spec_from_file_location("candidate", ROOT / "scripts/website-owner-decision.py")
candidate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(candidate)


class CandidateDecisionTests(unittest.TestCase):
    """Synthetic approvals test admission without changing the actual owner record."""

    def setUp(self):
        self.record = candidate.read_record("6" * 40, directory=ROOT / "test/fixtures/website-decisions")
        self.record["deploymentInputs"] = candidate.deployment_inputs()
        self.receipt = {"service": "website", "phase": "staging", "health": "success",
                        "sourceSha": "6" * 40, "image": self.record["image"],
                        "servicesSha": "7" * 40, "nginxSha": "8" * 64,
                        "composeProject": "website-staging", "publicHost": "staging.website.promethean.rest"}

    def test_exact_approved_candidate_and_fresh_stage_are_admitted(self):
        result = candidate.qualify(self.record, "6" * 40, self.record["image"], "7" * 40,
                                   "8" * 64, self.receipt)
        self.assertEqual("owner-decision", result["qualification"])
        self.assertFalse(result["mutationCampaignPassed"])
        self.assertEqual(self.record["deploymentInputs"], result["deploymentInputs"])
        self.assertEqual(self.record["ownerDecision"], result["ownerDecision"])

    def test_pending_or_missing_owner_decision_is_denied(self):
        for state in ("pending", "rejected", "", None):
            with self.subTest(state=state):
                record = copy.deepcopy(self.record)
                record["ownerDecision"]["status"] = state
                with self.assertRaises(ValueError):
                    candidate.qualify(record, "6" * 40, record["image"], "7" * 40, "8" * 64, self.receipt)

    def test_source_digest_or_serving_configuration_drift_is_denied(self):
        for field, value in (("sourceSha", "9" * 40), ("image", self.record["image"].replace("a" * 64, "b" * 64)),
                             ("nginxSha", "9" * 64)):
            with self.subTest(field=field):
                receipt = dict(self.receipt, **{field: value})
                with self.assertRaises(ValueError):
                    candidate.qualify(self.record, "6" * 40, self.record["image"], "7" * 40, "8" * 64, receipt)

    def test_wrong_phase_health_services_source_alias_or_hostname_is_denied(self):
        for field, value in (("phase", "production"), ("health", "starting"), ("servicesSha", "9" * 40),
                             ("composeProject", "website"), ("publicHost", "open-hax.promethean.rest")):
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    candidate.qualify(self.record, "6" * 40, self.record["image"], "7" * 40, "8" * 64,
                                      dict(self.receipt, **{field: value}))

    def test_unapproved_image_rebuild_or_mutable_source_is_denied(self):
        for source, image in (("main", self.record["image"]),
                              ("6" * 40, self.record["image"].replace("a" * 64, "b" * 64))):
            with self.subTest(source=source, image=image), self.assertRaises(ValueError):
                candidate.select(self.record, source, image)

    def test_copied_approval_cannot_authorize_another_candidate(self):
        """A fresh valid receipt and manifest cannot extend an unchanged approval."""
        self.assertEqual(self.record["image"], candidate.qualify(
            self.record, "6" * 40, self.record["image"], "7" * 40, "8" * 64, self.receipt)["image"])
        for source, image in (("9" * 40, "ghcr.io/open-hax/website:" + "9" * 40 + "@sha256:" + "b" * 64),
                              ("6" * 40, self.record["image"].replace("a" * 64, "b" * 64))):
            copied = copy.deepcopy(self.record)
            copied.update(sourceSha=source, image=image)
            receipt = {**self.receipt, "sourceSha": source, "image": image}
            self.assertEqual(self.record["ownerDecision"], copied["ownerDecision"])
            self.assertEqual(candidate.deployment_inputs(), copied["deploymentInputs"])
            for operation in ("select", "qualify"):
                with self.subTest(source=source, image=image, operation=operation):
                    with self.assertRaisesRegex(ValueError, "Owner decision.*exact source and image"):
                        if operation == "select":
                            candidate.select(copied, source, image)
                        else:
                            candidate.qualify(copied, source, image, "7" * 40, "8" * 64, receipt)

    def test_missing_or_malformed_owner_candidate_coordinates_are_denied(self):
        for field in ("sourceSha", "image"):
            for mode, value in (("missing", None), ("null", None), ("empty", ""),
                                ("number", 6), ("object", {}), ("mutable", "main")):
                record = copy.deepcopy(self.record)
                if mode == "missing":
                    del record["ownerDecision"][field]
                else:
                    record["ownerDecision"][field] = value
                for operation in ("select", "qualify"):
                    with self.subTest(field=field, mode=mode, operation=operation):
                        with self.assertRaisesRegex(ValueError, "Owner decision.*exact source and image"):
                            if operation == "select":
                                candidate.select(record, "6" * 40, record["image"])
                            else:
                                candidate.qualify(record, "6" * 40, record["image"], "7" * 40,
                                                  "8" * 64, self.receipt)
        for decision in (None, [], "approved", 1):
            record = copy.deepcopy(self.record)
            record["ownerDecision"] = decision
            with self.subTest(decision=decision), self.assertRaisesRegex(ValueError, "owner decision"):
                candidate.select(record, "6" * 40, record["image"])

    def test_matching_coordinates_still_require_immutable_source_and_image(self):
        for source in ("main", "6" * 39, "6" * 41, "A" * 40, None, 6):
            record = copy.deepcopy(self.record)
            image = "ghcr.io/open-hax/website:" + str(source) + "@sha256:" + "a" * 64
            record.update(sourceSha=source, image=image)
            record["ownerDecision"].update(sourceSha=source, image=image)
            with self.subTest(source=source), self.assertRaisesRegex(ValueError, "immutable Website source"):
                candidate.select(record, source, image)
        for image in ("ghcr.io/open-hax/website:main", "ghcr.io/open-hax/website:" + "6" * 40,
                      self.record["image"].replace("a" * 64, "A" * 64), None, 6):
            record = copy.deepcopy(self.record)
            record["image"] = image
            record["ownerDecision"]["image"] = image
            with self.subTest(image=image), self.assertRaisesRegex(ValueError, "immutable Website image"):
                candidate.select(record, "6" * 40, image)

    def test_committed_real_candidate_preserves_exact_scope_and_decision_evidence(self):
        record = candidate.read_record("6fd48ac5df9a16e414efbbf2b6abe98ad331be78")
        self.assertEqual("6fd48ac5df9a16e414efbbf2b6abe98ad331be78", record["sourceSha"])
        self.assertEqual("ghcr.io/open-hax/website:" + record["sourceSha"] +
                         "@sha256:ef3f2daa46b212cfcd82c2a72492946b0c44c23f1fab9b0fffde35d7199c836f", record["image"])
        self.assertIn(record["ownerDecision"]["status"], ("pending", "approved", "rejected"))
        if record["ownerDecision"]["status"] != "approved":
            with self.assertRaises(ValueError):
                candidate.select(record, record["sourceSha"], record["image"])
        elif candidate.deployment_inputs() == record["deploymentInputs"]:
            self.assertEqual(record["image"], candidate.select(record, record["sourceSha"], record["image"])["image"])
        else:
            # Historical approval remains evidence, not permission for new inputs.
            with self.assertRaisesRegex(ValueError, "^Public deployment inputs differ from the exact owner-approved configuration$"):
                candidate.select(record, record["sourceSha"], record["image"])

    def test_reviewed_real_candidate_refuses_public_deployment_input_drift(self):
        """Fresh receipt and unchanged Nginx cannot approve different Services inputs."""
        prefixes = (".github/", "scripts/", "digitalocean/hosts/", "digitalocean/known_hosts/",
                    "digitalocean/lib/", "digitalocean/scripts/", "digitalocean/services/")
        paths = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT).decode().split("\0")
        with tempfile.TemporaryDirectory() as temporary:
            checkout = Path(temporary)
            for path in filter(lambda p: p.startswith(prefixes), paths):
                target = checkout / path
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(ROOT / path, target)
            subprocess.run(["git", "init", "-q", str(checkout)], check=True)
            subprocess.run(["git", "add", "."], cwd=checkout, check=True)
            entries = []
            for item in subprocess.check_output(["git", "ls-files", "--stage", "-z"], cwd=checkout).decode().split("\0"):
                if item:
                    metadata, path = item.split("\t")
                    entries.append({"path": path, "mode": metadata.split()[0],
                                    "sha256": hashlib.sha256((checkout / path).read_bytes()).hexdigest()})
            entries.sort(key=lambda entry: entry["path"])
            binding = {"schemaVersion": 1, "files": entries,
                       "sha256": hashlib.sha256(json.dumps(entries, sort_keys=True, separators=(",", ":")).encode()).hexdigest()}
            record = copy.deepcopy(candidate.read_record("6fd48ac5df9a16e414efbbf2b6abe98ad331be78"))
            record["deploymentInputs"] = binding
            record["ownerDecision"].update(status="approved", approvedAt="2026-10-11T19:00:00Z",
                question="Synthetic repair test; no actual approval.", answer="Synthetic fixture only.",
                sourceSha=record["sourceSha"], image=record["image"])
            source, image, nginx = record["sourceSha"], record["image"], record["nginxSha"]
            receipt = {**self.receipt, "sourceSha": source, "image": image, "nginxSha": nginx}
            previous_root = candidate.ROOT
            try:
                candidate.ROOT = checkout
                self.assertEqual(image, candidate.qualify(record, source, image, "7" * 40, nginx, receipt)["image"])
                for path in ("digitalocean/services/caddy/compose.yaml",
                             "digitalocean/services/caddy/env.template",
                             "digitalocean/services/website/compose.yaml",
                             "digitalocean/services/website/compose.staging.yaml",
                             "digitalocean/services/website/env.template",
                             "digitalocean/services/website/verify.sh",
                             ".github/workflows/deploy-website-phases.yml",
                             ".github/workflows/deploy-digitalocean.yml",
                             "digitalocean/lib/host-contract.sh",
                             "digitalocean/lib/caddy-dev-auth.sh",
                             "scripts/website-owner-decision.py"):
                    with self.subTest(path=path):
                        target = checkout / path
                        original = target.read_bytes()
                        try:
                            target.write_bytes(original + b"\n# changed public deployment input\n")
                            for operation in (lambda: candidate.select(record, source, image),
                                              lambda: candidate.qualify(record, source, image, "7" * 40, nginx, receipt)):
                                with self.assertRaisesRegex(ValueError, "deployment inputs differ"):
                                    operation()
                        finally:
                            target.write_bytes(original)
                added = checkout / "scripts/new-deployment-helper.py"
                added.write_text("# newly selected authored helper\n")
                subprocess.run(["git", "add", str(added)], cwd=checkout, check=True)
                with self.subTest(membership="added tracked helper"):
                    for operation in (lambda: candidate.select(record, source, image),
                                      lambda: candidate.qualify(record, source, image, "7" * 40, nginx, receipt)):
                        with self.assertRaisesRegex(ValueError, "deployment inputs differ"):
                            operation()
                subprocess.run(["git", "update-index", "--force-remove", "scripts/new-deployment-helper.py"], cwd=checkout, check=True)
                added.unlink()
                subprocess.run(["git", "update-index", "--force-remove", "digitalocean/lib/host-contract.sh"], cwd=checkout, check=True)
                with self.subTest(membership="removed tracked helper"):
                    for operation in (lambda: candidate.select(record, source, image),
                                      lambda: candidate.qualify(record, source, image, "7" * 40, nginx, receipt)):
                        with self.assertRaisesRegex(ValueError, "deployment inputs differ"):
                            operation()
                subprocess.run(["git", "add", "digitalocean/lib/host-contract.sh"], cwd=checkout, check=True)
                subprocess.run(["git", "update-index", "--chmod=+x", "digitalocean/hosts/production.yaml"], cwd=checkout, check=True)
                with self.subTest(membership="changed regular Git file mode"):
                    for operation in (lambda: candidate.select(record, source, image),
                                      lambda: candidate.qualify(record, source, image, "7" * 40, nginx, receipt)):
                        with self.assertRaisesRegex(ValueError, "deployment inputs differ"):
                            operation()
            finally:
                candidate.ROOT = previous_root

    def test_owner_record_and_untracked_private_state_are_outside_public_hash(self):
        """Updating approval evidence cannot create a recursive configuration hash."""
        with tempfile.TemporaryDirectory() as temporary:
            checkout = Path(temporary)
            paths = ("scripts/helper.py", "digitalocean/qualifications/website/decision.json",
                     "test/fixtures/website-decisions/synthetic.json", "docs/guide.md", "receipts.edn")
            for path in paths:
                target = checkout / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("synthetic test bytes\n")
            subprocess.run(["git", "init", "-q", str(checkout)], check=True)
            subprocess.run(["git", "add", "."], cwd=checkout, check=True)
            (checkout / "scripts/.env").write_text("untracked synthetic private sentinel\n")
            previous_root = candidate.ROOT
            try:
                candidate.ROOT = checkout
                expected = candidate.deployment_inputs()
                self.assertEqual(["scripts/helper.py"], [entry["path"] for entry in expected["files"]])
                for path in (*paths[1:], "scripts/.env"):
                    with self.subTest(excluded=path):
                        (checkout / path).write_text("changed excluded synthetic bytes\n")
                        self.assertEqual(expected, candidate.deployment_inputs())
            finally:
                candidate.ROOT = previous_root

    def test_malformed_or_incomplete_input_binding_is_denied(self):
        for mode in ("absent", "bad-hash", "unsorted", "duplicate", "bad-mode", "bad-entry", "omitted-file"):
            with self.subTest(mode=mode):
                record = copy.deepcopy(self.record)
                binding = record["deploymentInputs"]
                if mode == "absent":
                    del record["deploymentInputs"]
                elif mode == "bad-hash":
                    binding["sha256"] = "0" * 64
                elif mode == "unsorted":
                    binding["files"].reverse()
                elif mode == "duplicate":
                    binding["files"].append(binding["files"][-1])
                elif mode == "bad-mode":
                    binding["files"][0]["mode"] = "120000"
                elif mode == "bad-entry":
                    binding["files"][0]["sha256"] = "bad"
                elif mode == "omitted-file":
                    binding["files"].pop()
                if mode not in ("absent", "bad-hash"):
                    binding["sha256"] = candidate.inputs_sha256(binding["files"])
                with self.assertRaises(ValueError):
                    candidate.select(record, "6" * 40, record["image"])

    def test_links_nonregular_inputs_and_tracked_private_files_are_denied_before_reads(self):
        with tempfile.TemporaryDirectory() as temporary:
            checkout = Path(temporary)
            scripts = checkout / "scripts"
            scripts.mkdir()
            ordinary = scripts / "ordinary.py"
            ordinary.write_text("# public synthetic input\n")
            subprocess.run(["git", "init", "-q", str(checkout)], check=True)
            subprocess.run(["git", "add", "."], cwd=checkout, check=True)
            previous_root = candidate.ROOT
            try:
                candidate.ROOT = checkout
                self.assertEqual(["scripts/ordinary.py"], [e["path"] for e in candidate.deployment_inputs()["files"]])
                ordinary.unlink()
                ordinary.symlink_to(checkout / "missing-private-target")
                with self.subTest(mode="worktree symlink"), self.assertRaisesRegex(ValueError, "symlinks"):
                    candidate.deployment_inputs()
                subprocess.run(["git", "add", "."], cwd=checkout, check=True)
                with self.subTest(mode="tracked symlink"), self.assertRaisesRegex(ValueError, "regular tracked"):
                    candidate.deployment_inputs()
                ordinary.unlink()
                ordinary.mkdir()
                subprocess.run(["git", "update-index", "--cacheinfo", "100644," + "a" * 40 + ",scripts/ordinary.py"],
                               cwd=checkout, check=True)
                with self.subTest(mode="worktree directory"), self.assertRaisesRegex(ValueError, "regular files"):
                    candidate.deployment_inputs()
                ordinary.rmdir()
                ordinary.write_text("# public synthetic input\n")
                subprocess.run(["git", "update-index", "--cacheinfo", "160000," + "a" * 40 + ",scripts/ordinary.py"],
                               cwd=checkout, check=True)
                with self.subTest(mode="gitlink"), self.assertRaisesRegex(ValueError, "regular tracked"):
                    candidate.deployment_inputs()
                subprocess.run(["git", "add", "."], cwd=checkout, check=True)
                private = scripts / ".env"
                private.write_text("synthetic test sentinel; no credentials\n")
                subprocess.run(["git", "add", "."], cwd=checkout, check=True)
                with self.subTest(mode="tracked private env"), self.assertRaisesRegex(ValueError, "Private environment"):
                    candidate.deployment_inputs()
            finally:
                candidate.ROOT = previous_root

    def test_private_filename_variants_are_denied_without_reading_their_bytes(self):
        """Force-tracked synthetic secrets still refuse before file-byte access."""
        with tempfile.TemporaryDirectory() as temporary:
            checkout = Path(temporary)
            scripts = checkout / "scripts"
            scripts.mkdir()
            subprocess.run(["git", "init", "-q", str(checkout)], check=True)
            previous_root = candidate.ROOT
            try:
                candidate.ROOT = checkout
                for name in (".env", ".env.production", ".env.staging", ".env.local",
                             "credentials.env", "certificate.pem", "private.key", "certificate.crt",
                             "private.p12", "private.pfx", "id_rsa", "id_ed25519", "cloudflare.ini",
                             "secrets/private.json", "runtime/session.json", "state/session.json", "capture.log"):
                    with self.subTest(private=name):
                        target = scripts / name
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_text("synthetic sentinel only; no credentials\n")
                        subprocess.run(["git", "add", "--force", str(target)], cwd=checkout, check=True)
                        try:
                            with patch.object(Path, "read_bytes", side_effect=AssertionError("Private bytes were read")):
                                with self.assertRaisesRegex(ValueError, "Private environment"):
                                    candidate.deployment_inputs()
                        finally:
                            subprocess.run(["git", "update-index", "--force-remove", str(target)],
                                           cwd=checkout, check=True)
                            target.unlink()
                public = (".env.example", ".env.production.example", "env.template")
                for name in public:
                    target = scripts / name
                    target.write_text("public synthetic template\n")
                    subprocess.run(["git", "add", str(target)], cwd=checkout, check=True)
                self.assertEqual(sorted("scripts/" + name for name in public),
                                 [entry["path"] for entry in candidate.deployment_inputs()["files"]])
            finally:
                candidate.ROOT = previous_root


if __name__ == "__main__":
    unittest.main()
