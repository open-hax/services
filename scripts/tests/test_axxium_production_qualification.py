"""Synthetic custody fixtures exercise refusal laws; they are not release evidence."""
import base64
import copy
from datetime import datetime, timedelta, timezone
import gzip
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

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(SCRIPTS), str(SCRIPTS / 'staging/axxium')]
import axxium_production_qualification as qualification
import prepare_candidate as recovery_candidate

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


def create_archive(path, legacy_tag=SOURCE, *, nested=False, defect=None):
    """Construct a tiny OCI save-format fixture without a Docker daemon."""
    blobs = {}

    def blob(raw, media_type, **fields):
        digest = hashlib.sha256(raw).hexdigest()
        blobs["blobs/sha256/" + digest] = raw
        return {"mediaType": media_type, "digest": "sha256:" + digest, "size": len(raw), **fields}

    layer = b"synthetic unexecuted layer bytes"
    if defect == "expansion":
        layer *= 4096
    layer_media = "application/vnd.oci.image.layer.v1.tar"
    manifest_media = "application/vnd.oci.image.manifest.v1+json"
    index_media = "application/vnd.oci.image.index.v1+json"
    config_media = "application/vnd.oci.image.config.v1+json"
    actual_diff_id = "sha256:" + hashlib.sha256(layer).hexdigest()
    diff_id = "sha256:" + "f" * 64 if defect == "diff-id" else actual_diff_id
    if nested:
        layer_descriptor = blob(layer if defect == "invalid-gzip" else gzip.compress(layer, mtime=0),
                                layer_media + "+gzip")
    else:
        layer_descriptor = blob(layer, layer_media)
    layer_count = 2 if defect == "repeated-layer" else 1
    config = blob(encoded({"architecture": "amd64", "os": "linux", "config": {"User": "node"},
                           "rootfs": {"type": "layers", "diff_ids": [diff_id] * layer_count}}), config_media)
    selected = blob(encoded({"schemaVersion": 2, "mediaType": manifest_media, "config": config,
                             "layers": [layer_descriptor] * layer_count}), manifest_media)
    docker = {"Config": "blobs/sha256/" + config["digest"][7:],
              "RepoTags": ["promethean-axxium:" + SOURCE],
              "Layers": ["blobs/sha256/" + layer_descriptor["digest"][7:]] * layer_count}
    if nested:
        subject = selected["digest"][7:] if defect != "attestation-subject" else "f" * 64
        predicate_type = "https://slsa.dev/provenance/v1"
        statement = blob(encoded({"_type": "https://in-toto.io/Statement/v0.1",
                                  "subject": [{"name": f"pkg:docker/promethean-axxium@{SOURCE}?platform=linux%2Famd64",
                                               "digest": {"sha256": subject}}],
                                  "predicateType": predicate_type, "predicate": {}}),
                         "application/vnd.in-toto+json", annotations={"in-toto.io/predicate-type": predicate_type})
        attestation_config = blob(encoded({"architecture": "unknown", "os": "unknown", "config": {},
                                           "rootfs": {"type": "layers", "diff_ids": [statement["digest"]]}}), config_media)
        attestation = blob(encoded({"schemaVersion": 2, "mediaType": manifest_media, "config": attestation_config,
                                    "layers": [statement]}), manifest_media,
                           platform={"architecture": "unknown", "os": "unknown"},
                           annotations={"vnd.docker.reference.type": "attestation-manifest",
                                        "vnd.docker.reference.digest": selected["digest"] if defect != "attestation-target"
                                        else "sha256:" + "f" * 64})
        selected["platform"] = {"architecture": "arm64" if defect == "platform" else "amd64", "os": "linux"}
        if defect == "descriptor-size":
            selected["size"] += 1
        manifests = [selected, attestation]
        if defect == "extra-platform":
            manifests.append(selected)
        selected = blob(encoded({"schemaVersion": 2, "mediaType": index_media, "manifests": manifests}), index_media)
        if defect == "blob-tamper":
            blobs[docker["Layers"][0]] += b"tampered archive layer"
        if defect == "docker-alias":
            docker["config"] = attestation_config["digest"]
    if defect == "unbound-blob":
        blob(b"unused archive blob", config_media)
    selected["annotations"] = {"io.containerd.image.name": "docker.io/library/promethean-axxium:" + SOURCE,
                               "org.opencontainers.image.ref.name": SOURCE}
    entries = {**blobs,
               "index.json": encoded({"schemaVersion": 2, "mediaType": index_media, "manifests": [selected]}),
               "oci-layout": encoded({"imageLayoutVersion": "1.0.0"}), "manifest.json": encoded([docker])}
    if not nested:
        entries["repositories"] = encoded({"promethean-axxium": {legacy_tag: actual_diff_id[7:]}})
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
    return selected["digest"]


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

    def test_mutation_batches_do_not_block_when_receipts_and_custody_pass(self):
        for change in ("missing", "short", "survivor", "duplicate", "empty", "absent", "campaign_false"):
            with self.subTest(change=change):
                self.proof = proof()
                rows = self.proof["mutation_batches"]
                if change == "missing": rows.pop()
                elif change == "short": rows[0]["mutants"].pop()
                elif change == "survivor": rows[0]["mutants"][0]["status"] = "survived"
                elif change == "duplicate": rows[1]["mutants"][0]["fingerprint"] = rows[0]["mutants"][0]["fingerprint"]
                elif change == "empty": self.proof["mutation_batches"] = []
                elif change == "absent": del self.proof["mutation_batches"]
                elif change == "campaign_false":
                    self.proof["mutation_batches"] = []
                    self.proof["mutation_campaign_passed"] = False
                result = self.admit()
                self.assertTrue(result["admitted"])
                self.assertNotIn("mutationCampaignPassed", result)
                self.assertNotIn("qualification", result)
                self.assertNotIn("waivedMutationVerdict", result)

    def test_invalid_integration_or_e2e_still_refuses(self):
        for change in ("stale", "boolean", "failed", "missing", "warnings", "timeout"):
            with self.subTest(change=change):
                self.proof = proof()
                if change == "stale": self.proof["integration"]["source_sha"] = "e" * 40
                elif change == "boolean": self.proof["e2e"]["assertions"] = True
                elif change == "failed": self.proof["e2e"]["status"] = "failed"
                elif change == "missing": del self.proof["integration"]
                elif change == "warnings": self.proof["e2e"]["warnings"] = ["warning"]
                elif change == "timeout": self.proof["integration"]["timed_out"] = True
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

    def test_historical_run_check_is_independent_of_native_artifact_retention(self):
        self.artifact["expired"] = True
        self.artifact["expires_at"] = (NOW - timedelta(seconds=1)).isoformat()
        with patch.object(qualification, "api", side_effect=self.api):
            self.assertEqual(qualification.verify_native_stage_run(self.plan, NOW), self.run)
            self.assertFalse(any("/actions/artifacts/" in path for path in self.calls))
            with self.assertRaisesRegex(qualification.Refusal, "native staging artifact custody mismatch"):
                qualification.verify_native_stage(self.plan, NOW)
        self.assertTrue(any("/actions/artifacts/" in path for path in self.calls))

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

    def nested_plan(self, defect=None):
        image_id = create_archive(self.archive, nested=True, defect=defect)
        return {**self.plan, "image_id": image_id,
                "artifact_sha256": hashlib.sha256(self.archive.read_bytes()).hexdigest()}

    def test_staged_nested_index_binds_runnable_image_and_provenance(self):
        plan = self.nested_plan()
        self.assertEqual(qualification.verify_archive(plan, self.archive), plan["image_id"])

    def test_only_digest_named_legacy_metadata_may_be_unreferenced(self):
        create_archive(self.archive, defect="unbound-blob")
        plan = {**self.plan, "artifact_sha256": hashlib.sha256(self.archive.read_bytes()).hexdigest()}
        legacy = "blobs/sha256/" + hashlib.sha256(b"unused archive blob").hexdigest()
        with patch.object(qualification, "LEGACY_LAYER_METADATA", frozenset({legacy})):
            self.assertEqual(qualification.verify_archive(plan, self.archive), plan["image_id"])
        with self.assertRaisesRegex(qualification.Refusal, "unreferenced"):
            qualification.verify_archive(plan, self.archive)

    def test_direct_manifest_also_refuses_unreferenced_blob(self):
        create_archive(self.archive, defect="unbound-blob")
        plan = {**self.plan, "artifact_sha256": hashlib.sha256(self.archive.read_bytes()).hexdigest()}
        with self.assertRaisesRegex(qualification.Refusal, "unreferenced"):
            qualification.verify_archive(plan, self.archive)

    def test_recovery_projection_omits_only_pinned_metadata(self):
        create_archive(self.archive, defect="unbound-blob")
        plan = {**self.plan, "artifact_sha256": hashlib.sha256(self.archive.read_bytes()).hexdigest()}
        image = self.archive.with_name("projected.tar")
        projected = self.archive.with_name("projected.zip")
        legacy = "blobs/sha256/" + hashlib.sha256(b"unused archive blob").hexdigest()
        with patch.object(recovery_candidate, "LEGACY_LAYER_METADATA", {legacy}):
            wrong = {**plan, "artifact_sha256": "f" * 64}
            with self.assertRaisesRegex(qualification.Refusal, "differs from native digest"):
                recovery_candidate.project_fixture_tar(wrong, self.archive, image)
            self.assertFalse(image.exists())
            recovery_candidate.project_fixture_tar(plan, self.archive, image)
            recovery_candidate.verify_projection(plan, image, projected)
        with self.assertRaisesRegex(qualification.Refusal, "unreferenced"):
            qualification.verify_archive(plan, self.archive)

    def test_direct_manifest_retains_support_for_repeated_layers(self):
        image_id = create_archive(self.archive, defect="repeated-layer")
        plan = {**self.plan, "image_id": image_id,
                "artifact_sha256": hashlib.sha256(self.archive.read_bytes()).hexdigest()}
        self.assertEqual(qualification.verify_archive(plan, self.archive), image_id)

    def test_nested_index_refuses_resealed_archives_with_inconsistent_content(self):
        for defect, reason in (("platform", "platform"), ("extra-platform", "nested OCI index"),
                               ("descriptor-size", "descriptor"), ("attestation-target", "attestation"),
                               ("attestation-subject", "provenance"), ("unbound-blob", "unreferenced"),
                               ("blob-tamper", "blob digest"), ("docker-alias", "Docker manifest"),
                               ("diff-id", "root filesystem"), ("invalid-gzip", "gzip"),
                               ("repeated-layer", "repeated OCI runnable layer")):
            with self.subTest(defect=defect):
                plan = self.nested_plan(defect)
                with self.assertRaisesRegex(qualification.Refusal, reason):
                    qualification.verify_archive(plan, self.archive)

    def test_nested_index_refuses_excessive_gzip_expansion(self):
        plan = self.nested_plan("expansion")
        with zipfile.ZipFile(self.archive) as artifact:
            tar_size = artifact.getinfo("images.tar").file_size
        with patch.object(qualification, "MAX_TAR", tar_size):
            with self.assertRaisesRegex(qualification.Refusal, "size exceeds inspected bounds"):
                qualification.verify_archive(plan, self.archive)

    def test_duplicate_json_and_boolean_native_plan_identities_refuse(self):
        with self.assertRaises(qualification.Refusal):
            qualification.strict_json(b'{"source_sha":"a","source_sha":"b"}')
        for field in ("source_pr", "stage_run_id", "artifact_id"):
            with self.assertRaises(qualification.Refusal):
                qualification.validate_plan({**self.plan, field: True})


if __name__ == "__main__":
    unittest.main()
