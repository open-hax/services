import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import tempfile
from types import SimpleNamespace

import yaml

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("embedding_worker", ROOT / "scripts/verify_embeddinggemma_worker.py")
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)
PROFILE = json.loads((ROOT / "workstations/embeddinggemma2/profile.json").read_text())


def runtime():
    return {
        "Image": PROFILE["vulkan_image_id"], "RestartCount": 0,
        "State": {"Running": True, "OOMKilled": False, "Pid": 20000,
                  "StartedAt": "2026-11-20T00:00:00Z"},
        "Config": {"Image": PROFILE["vulkan_image"], "User": "1002:1002",
                   "Entrypoint": ["/app/llama-server"], "Cmd": PROFILE["yoga_command"]},
        "HostConfig": {"Privileged": False, "GroupAdd": ["992"], "NanoCpus": 2000000000,
                       "Memory": 4294967296, "MemorySwap": 4294967296, "PidsLimit": 128,
                       "ReadonlyRootfs": True, "CapDrop": ["ALL"],
                       "SecurityOpt": ["no-new-privileges:true"],
                       "Tmpfs": {"/tmp": "rw,noexec,nosuid,size=128m,uid=1002,gid=1002"},
                       "Devices": [{"PathOnHost": "/dev/dri/renderD128", "PathInContainer": "/dev/dri/renderD128", "CgroupPermissions": "rwm"}],
                       "NetworkMode": "owned_worker", "PortBindings": {}},
        "Mounts": [{"Type": "bind", "Source": "/verified/model.gguf", "Destination": "/models/model.gguf", "RW": False}],
        "NetworkSettings": {"Networks": {"owned_worker": {"Aliases": ["embeddinggemma"]}}}}


def network():
    return [{"Name": "owned_worker", "Internal": True, "Attachable": False, "Containers": {"owned": {}}}]


class WorkerBoundaryTests(unittest.TestCase):
    def verify_observations(self, *, stale_gpu_only=False, attach_after_probe=False):
        value = runtime()
        value["Id"] = "b"*64
        inspected = 0
        log_calls = []
        def docker(*args):
            nonlocal inspected
            if args[0] == "inspect":
                inspected += 1
                current = copy.deepcopy(value)
                if attach_after_probe and inspected >= 4:
                    current["NetworkSettings"]["Networks"]["second_network"] = {}
                return json.dumps([current]).encode()
            if args[:2] == ("network", "inspect"):
                return json.dumps(network() + (network() if "second_network" in args else [])).encode()
            if args[0] == "exec":
                return (PROFILE["model_sha256"] + "  /models/model.gguf\n").encode()
            raise AssertionError("Unexpected Docker readback")
        def logs(args, **kwargs):
            log_calls.append(args)
            data = b"current process CPU-only\n" if stale_gpu_only and "--since" in args else b"RADV REMBRANDT offloaded 25/25 layers to GPU\n"
            return SimpleNamespace(returncode=0, stdout=data, stderr=b"")
        answer = {"model": "embeddinggemma-2", "data": [{"index": 0, "embedding": [1.0] + [0.0]*767}],
                  "usage": {"prompt_tokens": 10}}
        with patch.object(worker, "docker", docker), patch.object(worker.subprocess, "run", logs), \
             patch.object(worker, "infer_in_namespace", return_value=(answer, 1)):
            result = worker.verify("owned", "yoga")
        return result, log_calls

    def test_old_startup_logs_cannot_attest_current_cpu_only_process(self):
        with self.assertRaises(worker.Refused):
            self.verify_observations(stale_gpu_only=True)

    def test_network_added_after_initial_validation_is_refused(self):
        with self.assertRaises(worker.Refused):
            self.verify_observations(attach_after_probe=True)

    def test_unchanged_native_observations_still_pass(self):
        result, log_calls = self.verify_observations()
        self.assertEqual(result["status"], "passed")
        self.assertTrue(log_calls)

    def test_replaced_host_path_does_not_attest_the_old_running_bind(self):
        with tempfile.TemporaryDirectory() as directory:
            model = Path(directory) / "model.gguf"
            # A running single-file bind can retain the old inode after an
            # atomic replacement. This path now contains the expected file,
            # while the actual container namespace still exposes stale bytes.
            model.write_bytes(b"replacement expected model")
            expected = hashlib.sha256(model.read_bytes()).hexdigest()
            stale = hashlib.sha256(b"old container-bound model").hexdigest()
            profile = {**PROFILE, "model_sha256": expected}
            profile_file = Path(directory) / "profile.json"
            profile_file.write_text(json.dumps(profile))
            value = runtime()
            value["Id"] = "b"*64
            value["Mounts"][0]["Source"] = str(model)
            value["Name"] = "/owned"
            def docker(*args):
                if args[0] == "inspect": return json.dumps([value]).encode()
                if args[:2] == ("network", "inspect"): return json.dumps(network()).encode()
                if args[0] == "exec": return (stale + "  /models/model.gguf\n").encode()
                raise AssertionError("Unexpected Docker call")
            answer = {"model": "embeddinggemma-2", "data": [{"index": 0, "embedding": [1.0] + [0.0]*767}],
                      "usage": {"prompt_tokens": 10}}
            logs = SimpleNamespace(returncode=0, stdout=b"RADV REMBRANDT offloaded 25/25 layers to GPU\n", stderr=b"")
            with patch.object(worker, "PROFILE", profile_file), patch.object(worker, "docker", docker), \
                 patch.object(worker.subprocess, "run", return_value=logs), \
                 patch.object(worker, "infer_in_namespace", return_value=(answer, 1)):
                with self.assertRaises(worker.Refused):
                    worker.verify("owned", "yoga")

    def test_stealth_effective_port_must_match_requested_loopback_binding(self):
        value = runtime()
        value["NetworkSettings"]["Networks"] = {"ai-infra": {"Aliases": ["llamacpp-embed"]}}
        value["HostConfig"]["PortBindings"] = {"8081/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8081"}]}
        for effective in ({"8081/tcp": None}, {"8081/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8081"}]},
                          {"8081/tcp": [{"HostIp": "127.0.0.1", "HostPort": "9090"}]}):
            with self.subTest(effective=effective):
                value["NetworkSettings"]["Ports"] = effective
                with self.assertRaises(worker.Refused):
                    worker.validate_runtime(value, PROFILE, "stealth", [])

    def test_reviewed_compose_matches_pinned_profile_and_has_no_public_listener(self):
        compose = yaml.safe_load((ROOT / "workstations/embeddinggemma2/yoga.compose.yaml").read_text())
        service = compose["services"]["embeddinggemma"]
        self.assertEqual(service["command"], PROFILE["yoga_command"])
        self.assertEqual(service["image"], PROFILE["vulkan_image"])
        self.assertEqual(service["pull_policy"], "never")
        self.assertNotIn("ports", service)
        self.assertEqual(service["cpus"], "2.0")
        self.assertEqual(service["mem_limit"], service["memswap_limit"])
        self.assertTrue(service["volumes"][0]["read_only"])
        self.assertFalse(service["volumes"][0]["bind"]["create_host_path"])
        self.assertEqual(compose["networks"], {"worker": {"internal": True, "attachable": False}})
        self.assertNotIn("external_links", service)
        self.assertNotIn("build", service)

    def test_expected_private_yoga_runtime_is_accepted(self):
        model, port = worker.validate_runtime(runtime(), PROFILE, "yoga", network())
        self.assertEqual(str(model), "/models/model.gguf")
        self.assertIsNone(port)

    def test_broad_ipv4_ipv6_and_extra_host_exposure_are_rejected(self):
        for ip in ("", "0.0.0.0", "::", "100.105.116.31", "192.168.4.21"):
            with self.subTest(ip=ip):
                value = runtime()
                value["HostConfig"]["PortBindings"] = {
                    "8081/tcp": [{"HostIp": ip, "HostPort": "18081"}]}
                with self.assertRaises(worker.Refused):
                    worker.validate_runtime(value, PROFILE, "yoga", network())
        value = runtime()
        value["HostConfig"]["PortBindings"]["22/tcp"] = [{"HostIp": "127.0.0.1", "HostPort": "2222"}]
        with self.assertRaises(worker.Refused):
            worker.validate_runtime(value, PROFILE, "yoga", network())

    def test_resource_and_privilege_drift_is_rejected(self):
        changes = {"NanoCpus": 3000000000, "Memory": 0, "MemorySwap": -1, "PidsLimit": 0,
                   "Privileged": True, "ReadonlyRootfs": False, "CapDrop": [],
                   "CapAdd": ["SYS_ADMIN"], "PidMode": "host", "IpcMode": "host", "UTSMode": "host",
                   "SecurityOpt": [], "GroupAdd": [], "Devices": [], "NetworkMode": "host",
                   "Tmpfs": {"/tmp": "rw,size=4g"}}
        for key, changed in changes.items():
            with self.subTest(key=key):
                value = runtime()
                value["HostConfig"][key] = changed
                with self.assertRaises(worker.Refused):
                    worker.validate_runtime(value, PROFILE, "yoga", network())

    def test_wrong_image_command_user_and_host_mount_are_rejected(self):
        values = []
        value = runtime(); value["Image"] = "sha256:" + "0"*64; values.append(value)
        value = runtime(); value["Config"]["Image"] = "mutable:latest"; values.append(value)
        value = runtime(); value["Config"]["User"] = "0:0"; values.append(value)
        value = runtime(); value["Config"]["Cmd"] = ["--threads", "16"]; values.append(value)
        value = runtime(); value["Mounts"][0]["RW"] = True; values.append(value)
        value = runtime(); value["Mounts"].append({"Type": "bind", "Source": "/", "Destination": "/host", "RW": False}); values.append(value)
        for value in values:
            with self.assertRaises(worker.Refused):
                worker.validate_runtime(value, PROFILE, "yoga", network())
        value = runtime()
        value["NetworkSettings"]["Ports"] = {"8081/tcp": [{"HostIp": "127.0.0.1", "HostPort": "18081"}]}
        with self.assertRaises(worker.Refused):
            worker.validate_runtime(value, PROFILE, "yoga", network())

    def test_unisolated_or_shared_network_is_rejected(self):
        values = []
        value = network(); value[0]["Internal"] = False; values.append(value)
        value = network(); value[0]["Attachable"] = True; values.append(value)
        value = network(); value[0]["Containers"]["other"] = {}; values.append(value)
        values += [[], network() + network()]
        for value in values:
            with self.assertRaises(worker.Refused):
                worker.validate_runtime(runtime(), PROFILE, "yoga", value)

    def test_existing_stealth_fallback_can_be_verified_without_changing_its_limits(self):
        value = runtime()
        value["Image"] = "existing-cuda-image"
        value["Config"]["Cmd"] = ["--model", "/models/model.gguf"]
        value["NetworkSettings"]["Networks"] = {"ai-infra": {"Aliases": ["llamacpp-embed"]}}
        value["HostConfig"].update({"NanoCpus": 0, "Memory": 0, "MemorySwap": 0})
        value["HostConfig"]["PortBindings"] = {
            "8081/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8081"}]}
        value["NetworkSettings"]["Ports"] = copy.deepcopy(value["HostConfig"]["PortBindings"])
        worker.validate_runtime(value, PROFILE, "stealth", [])
        value["NetworkSettings"]["Networks"]["ai-infra"]["Aliases"] = ["other"]
        with self.assertRaises(worker.Refused):
            worker.validate_runtime(value, PROFILE, "stealth", [])

    def test_vector_requires_exact_model_finite_768_unit_vector_and_positive_usage(self):
        valid = {"model": "embeddinggemma-2", "data": [{"index": 0, "embedding": [1.0] + [0.0]*767}],
                 "usage": {"prompt_tokens": 10}}
        self.assertEqual(worker.validate_embedding(valid, PROFILE)["dimensions"], 768)
        variants = []
        for bad in (True, float("nan"), float("inf"), "1"):
            value = copy.deepcopy(valid); value["data"][0]["embedding"][0] = bad; variants.append(value)
        value = copy.deepcopy(valid); value["model"] = "embeddinggemma"; variants.append(value)
        value = copy.deepcopy(valid); value["data"][0]["embedding"].pop(); variants.append(value)
        value = copy.deepcopy(valid); value["data"][0]["embedding"] = [0.0]*768; variants.append(value)
        value = copy.deepcopy(valid); value["data"][0]["embedding"][0] = 100.0; variants.append(value)
        value = copy.deepcopy(valid); value["usage"]["prompt_tokens"] = True; variants.append(value)
        value = copy.deepcopy(valid); value["usage"]["prompt_tokens"] = 0; variants.append(value)
        for value in variants:
            with self.assertRaises(worker.Refused):
                worker.validate_embedding(value, PROFILE)


if __name__ == "__main__":
    unittest.main()
