"""Synthetic custody fixtures exercise refusal laws; they are not release evidence."""
import base64
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import axxium_production_qualification as qualification

SOURCE = "a" * 40
CONTROLLER = "b" * 40
STAGE_CONTROLLER = "c" * 40
HEAD = "d" * 40
NOW = datetime(2026, 10, 29, 18, 0, tzinfo=timezone.utc)


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def native_file(path, raw):
    return {"type": "file", "encoding": "base64", "path": path, "size": len(raw),
            "sha": hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest(),
            "content": base64.encodebytes(raw).decode()}


def proof():
    receipt = {"source_sha": SOURCE, "status": "passed", "assertions": 1}
    batches = [{"source_sha": SOURCE, "status": "passed", "baseline": "passed", "batch_index": index,
                "mutants": [{"fingerprint": f"fixture-{index}-{n}", "status": "killed", "phase": "test"}
                            for n in range(250)]} for index in range(4)]
    return {"repository": qualification.REPOSITORY, "source_sha": SOURCE,
            "integration": receipt, "e2e": dict(receipt), "mutation_batches": batches}


def create_archive(path, legacy_tag=SOURCE):
    """Construct a tiny OCI save-format fixture without a Docker daemon."""
    layer = b"synthetic unexecuted layer bytes"
    layer_digest = hashlib.sha256(layer).hexdigest()
    config = encoded({"architecture": "amd64", "os": "linux", "config": {"User": "node"},
                      "rootfs": {"type": "layers", "diff_ids": ["sha256:" + layer_digest]}})
    config_digest = hashlib.sha256(config).hexdigest()
    manifest = encoded({"schemaVersion": 2, "mediaType": "application/vnd.oci.image.manifest.v1+json",
                        "config": {"mediaType": "application/vnd.oci.image.config.v1+json",
                                   "digest": "sha256:" + config_digest, "size": len(config)},
                        "layers": [{"mediaType": "application/vnd.oci.image.layer.v1.tar",
                                    "digest": "sha256:" + layer_digest, "size": len(layer)}]})
    image = hashlib.sha256(manifest).hexdigest()
    index = encoded({"schemaVersion": 2, "mediaType": "application/vnd.oci.image.index.v1+json",
                     "manifests": [{"mediaType": "application/vnd.oci.image.manifest.v1+json",
                                    "digest": "sha256:" + image, "size": len(manifest),
                                    "annotations": {"io.containerd.image.name": "docker.io/library/promethean-axxium:" + SOURCE,
                                                    "org.opencontainers.image.ref.name": SOURCE}}]})
    entries = {"blobs/sha256/" + layer_digest: layer, "blobs/sha256/" + config_digest: config,
               "blobs/sha256/" + image: manifest, "index.json": index,
               "oci-layout": encoded({"imageLayoutVersion": "1.0.0"}),
               "repositories": encoded({"promethean-axxium": {legacy_tag: layer_digest}}),
               "manifest.json": encoded([{"Config": "blobs/sha256/" + config_digest,
                                         "RepoTags": ["promethean-axxium:" + SOURCE],
                                         "Layers": ["blobs/sha256/" + layer_digest]}])}
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as archive:
        for name, raw in entries.items():
            member = tarfile.TarInfo(name)
            member.size = len(raw)
            archive.addfile(member, io.BytesIO(raw))
    tar = stream.getvalue()
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("images.tar", tar)
        archive.writestr("images.sha256", hashlib.sha256(tar).hexdigest() + "\n")
    return "sha256:" + image


class ProductionQualificationTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.archive = Path(self.directory.name) / "artifact.zip"
        image = create_archive(self.archive)
        self.plan = {"schema": "axxium-minimal-production/v1", "host": "knoxx.promethean.rest",
                     "source_sha": SOURCE, "source_pr": 37, "image_id": image,
                     "stage_controller_sha": STAGE_CONTROLLER, "stage_run_id": 17,
                     "stage_workflow_path": qualification.STAGE_WORKFLOW, "artifact_id": 18,
                     "artifact_sha256": hashlib.sha256(self.archive.read_bytes()).hexdigest()}
        self.plan_bytes = encoded(self.plan)
        self.proof = proof()
        self.calls = []
        self.pr = {"number": 37, "merged": True, "state": "closed",
                   "merge_commit_sha": SOURCE, "head": {"sha": HEAD},
                   "base": {"ref": "main", "repo": {"full_name": qualification.REPOSITORY}},
                   "merged_at": (NOW - timedelta(minutes=10)).isoformat()}
        self.run = {"id": 17, "event": "pull_request", "status": "completed", "conclusion": "success",
                    "path": qualification.STAGE_WORKFLOW, "head_sha": HEAD,
                    "repository": {"id": 22, "full_name": qualification.REPOSITORY},
                    "referenced_workflows": [{"path": qualification.REUSABLE + STAGE_CONTROLLER, "sha": STAGE_CONTROLLER}],
                    "created_at": (NOW - timedelta(minutes=9)).isoformat(),
                    "updated_at": (NOW - timedelta(minutes=8)).isoformat()}
        self.artifact = {"id": 18, "name": "service-environment-images", "expired": False,
                         "digest": "sha256:" + self.plan["artifact_sha256"],
                         "size_in_bytes": self.archive.stat().st_size,
                         "created_at": (NOW - timedelta(minutes=8, seconds=30)).isoformat(),
                         "expires_at": (NOW + timedelta(days=1)).isoformat(),
                         "workflow_run": {"id": 17, "head_sha": HEAD, "repository_id": 22, "head_repository_id": 22}}

    def api(self, path):
        self.calls.append(path)
        if "/compare/" in path:
            sha = path.split("/compare/", 1)[1].split("...")[0]
            return {"status": "ahead", "base_commit": {"sha": sha}, "merge_base_commit": {"sha": sha}}
        if path.endswith("/pulls/37"):
            return copy.deepcopy(self.pr)
        if path.endswith("/actions/runs/17"):
            return copy.deepcopy(self.run)
        if path.endswith("/actions/artifacts/18"):
            return copy.deepcopy(self.artifact)
        if "/contents/" in path:
            selected = path.split("/contents/", 1)[1].split("?")[0]
            if selected == qualification.PLAN_PATH:
                return native_file(selected, self.plan_bytes)
            if selected == f"digitalocean/qualifications/axxium/{SOURCE}.json" and self.proof is not None:
                return native_file(selected, encoded(self.proof))
            raise HTTPError(path, 404, "missing fixture", {}, None)
        raise AssertionError("Unexpected fixture request")

    def admit(self):
        with patch.object(qualification, "api", side_effect=self.api):
            return qualification.admit(self.plan_bytes, CONTROLLER, self.archive, now=NOW)

    def test_complete_native_custody_reaches_existing_policy_and_accepts_main_ancestors(self):
        result = self.admit()
        self.assertEqual(result, {"admitted": True, "source_sha": SOURCE, "image_id": self.plan["image_id"],
                                  "plan_sha256": hashlib.sha256(self.plan_bytes).hexdigest()})
        self.assertGreaterEqual(sum("/actions/runs/17" in path for path in self.calls), 2)

    def test_absent_real_qualification_refuses_even_with_healthy_stage_and_archive(self):
        self.proof = None
        with self.assertRaisesRegex(qualification.Refusal, "immutable source qualification unavailable"):
            self.admit()

    def test_pilot_counts_and_survivors_cannot_supply_four_qualified_batches(self):
        for change in ("missing", "short", "survivor", "duplicate", "stale", "boolean"):
            with self.subTest(change=change):
                self.proof = proof()
                rows = self.proof["mutation_batches"]
                if change == "missing": rows.pop()
                elif change == "short": rows[0]["mutants"].pop()
                elif change == "survivor": rows[0]["mutants"][0]["status"] = "survived"
                elif change == "duplicate": rows[1]["mutants"][0]["fingerprint"] = rows[0]["mutants"][0]["fingerprint"]
                elif change == "stale": self.proof["integration"]["source_sha"] = "e" * 40
                elif change == "boolean": self.proof["e2e"]["assertions"] = True
                with self.assertRaises(qualification.Refusal):
                    self.admit()

    def test_plan_bytes_are_bound_to_accepted_controller(self):
        self.plan_bytes += b"\n"
        raw = self.plan_bytes
        with patch.object(qualification, "api", side_effect=lambda path: native_file(qualification.PLAN_PATH, raw + b"\n")
                          if "/contents/" in path else self.api(path)):
            with self.assertRaises(qualification.Refusal):
                qualification.admit(raw, CONTROLLER, self.archive, now=NOW)

    def test_unmerged_or_wrong_native_source_and_run_refuse(self):
        for target, field, value in ((self.pr, "merged", False), (self.pr, "merge_commit_sha", "e" * 40),
                                     (self.run, "head_sha", "e" * 40), (self.run, "conclusion", "failure"),
                                     (self.run, "path", ".github/workflows/arbitrary.yml")):
            with self.subTest(field=field):
                old = target[field]; target[field] = value
                with self.assertRaises(qualification.Refusal): self.admit()
                target[field] = old

    def test_expired_or_foreign_artifact_and_mutable_controller_refuse(self):
        for target, field, value in ((self.artifact, "expired", True),
                                     (self.artifact, "digest", "sha256:" + "f" * 64),
                                     (self.artifact["workflow_run"], "head_repository_id", 99)):
            with self.subTest(field=field):
                old = target[field]; target[field] = value
                with self.assertRaises(qualification.Refusal): self.admit()
                target[field] = old
        self.run["referenced_workflows"][0]["path"] = qualification.REUSABLE + "main"
        with self.assertRaises(qualification.Refusal): self.admit()

    def test_native_main_ancestry_is_required_but_tip_equality_is_not(self):
        def diverged(path):
            if "/compare/" in path:
                return {"status": "diverged", "base_commit": {"sha": CONTROLLER}, "merge_base_commit": {"sha": HEAD}}
            return self.api(path)
        with patch.object(qualification, "api", side_effect=diverged):
            with self.assertRaises(qualification.Refusal):
                qualification.admit(self.plan_bytes, CONTROLLER, self.archive, now=NOW)

    def test_archive_image_and_source_are_cryptographically_bound(self):
        self.assertEqual(qualification.verify_archive(self.plan, self.archive), self.plan["image_id"])
        for field, value in (("image_id", "sha256:" + "f" * 64), ("source_sha", "f" * 40)):
            with self.subTest(field=field):
                changed = {**self.plan, field: value}
                with self.assertRaises(qualification.Refusal):
                    qualification.verify_archive(changed, self.archive)
        with self.archive.open("ab") as stream:
            stream.write(b"tamper")
        with self.assertRaises(qualification.Refusal):
            qualification.verify_archive(self.plan, self.archive)

    def test_legacy_tag_must_agree_with_oci_reference_and_last_layer(self):
        changed = Path(self.directory.name) / "wrong-legacy-tag.zip"
        create_archive(changed, legacy_tag="e" * 40)
        plan = {**self.plan, "artifact_sha256": hashlib.sha256(changed.read_bytes()).hexdigest()}
        with self.assertRaisesRegex(qualification.Refusal, "legacy tag"):
            qualification.verify_archive(plan, changed)

    def test_duplicate_json_and_boolean_native_plan_identities_refuse(self):
        with self.assertRaises(qualification.Refusal):
            qualification.strict_json(b'{"source_sha":"a","source_sha":"b"}')
        for field in ("source_pr", "stage_run_id", "artifact_id"):
            with self.assertRaises(qualification.Refusal):
                qualification.validate_plan({**self.plan, field: True})


if __name__ == "__main__":
    unittest.main()
