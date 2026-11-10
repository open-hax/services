"""Exercise image-store compatibility and fail-closed host boundaries."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import axxium_work_dark_host as host


class WorkDarkHostTest(unittest.TestCase):
    def test_verified_image_accepts_both_store_representations(self):
        classic = [{"Id": "config", "Os": "linux", "Architecture": "amd64"}]
        index = [{"Id": "index"}]
        manifest = [{"Id": "manifest", "Descriptor": {"digest": "manifest"},
                     "Os": "linux", "Architecture": "amd64"}]
        for selected, platform, expected in ((classic, classic, "config"), (index, manifest, "index")):
            with self.subTest(expected=expected), patch.object(
                host, "docker", side_effect=[json.dumps(selected), json.dumps(platform)],
            ):
                self.assertEqual(host.verified_image("tag", "config", "index", "manifest"), expected)
        manifest[0]["Descriptor"]["digest"] = "different"
        with patch.object(host, "docker", side_effect=[json.dumps(index), json.dumps(manifest)]):
            with self.assertRaisesRegex(host.Refused, "index or manifest"):
                host.verified_image("tag", "config", "index", "manifest")

    def test_existing_unlabeled_volume_name_refuses_pilot(self):
        with tempfile.TemporaryDirectory() as scratch, patch.object(host, "SLOT", Path(scratch)):
            outputs = [b"", b"", b"", b"", (host.PROJECT + "_database\n").encode()]
            with patch.object(host, "project_containers", return_value=[]), patch.object(
                host, "docker", side_effect=outputs,
            ):
                with self.assertRaisesRegex(host.Refused, "name already exists"):
                    host.ensure_new_project()

    def test_command_timeout_kills_the_plugin_process_group(self):
        with patch.object(host.subprocess, "Popen") as spawn, patch.object(host.os, "killpg") as kill:
            process = spawn.return_value
            process.pid = 1234
            process.communicate.side_effect = subprocess.TimeoutExpired(["docker"], 1)
            with self.assertRaises(host.CommandTimeout):
                host.command(["docker"], "test timeout", timeout=1)
            self.assertTrue(spawn.call_args.kwargs["start_new_session"])
            kill.assert_called_once_with(process.pid, host.signal.SIGKILL)
            process.wait.assert_called_once()
