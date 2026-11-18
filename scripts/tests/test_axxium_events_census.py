"""Verify callback projector artifact custody, execution and aggregate output."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import axxium_events_census as census
from axxium_minimal_release import Refused

RECIPIENT = "did:plc:aaaaaaaaaaaaaaaaaaaaaaaa"
PRIVATE_MARKER = "private fixture data"
ZERO = {
    "retained-subscriptions": 0,
    "active-subscriptions": 0,
    "eligible-pending": 0,
    "retryable-callbacks": 0,
    "unsettled-attempts": 0,
    "due-callbacks": 0,
}
DRIVER = b"""import fs from 'node:fs';
import { summarize } from './probe-dist/probe.js';
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify(summarize(input.text, input.owner, input.now)));
"""
MODULE = b"export function summarize() { return {}; }"


class CensusTests(unittest.TestCase):
    def setUp(self):
        self.scratch = TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.root = Path(self.scratch.name)
        self.module = self.write("module.js", MODULE)
        self.driver = self.write("driver.mjs", DRIVER)

    def write(self, name, raw):
        path = self.root / name
        path.write_bytes(raw)
        path.chmod(0o600)
        return path

    def projector(self, module_sha256=None, driver_sha256=None):
        return census.PinnedCensus(
            self.module, self.driver,
            module_sha256=module_sha256 or hashlib.sha256(self.module.read_bytes()).hexdigest(),
            driver_sha256=driver_sha256 or hashlib.sha256(self.driver.read_bytes()).hexdigest(),
        )

    def test_exact_digests_are_required_for_both_artifacts(self):
        for field in ("module_sha256", "driver_sha256"):
            for wrong in ("0" * 64, "malformed"):
                with self.subTest(field=field, wrong=wrong), self.assertRaises(Refused):
                    self.projector(**{field: wrong})

    def test_artifacts_require_private_regular_single_link_files(self):
        digest = hashlib.sha256(MODULE).hexdigest()
        self.module.chmod(0o644)
        with self.assertRaisesRegex(Refused, "artifact custody"):
            self.projector(module_sha256=digest)
        self.module.chmod(0o600)
        alternate = self.root / "alternate.js"
        alternate.symlink_to(self.module)
        with self.assertRaisesRegex(Refused, "artifact custody"):
            census.pinned_file(alternate, digest, 100)
        alternate.unlink()
        os.link(self.module, alternate)
        with self.assertRaisesRegex(Refused, "artifact custody"):
            self.projector(module_sha256=digest)

    def test_driver_uses_exactly_one_reviewed_import(self):
        for driver in (b"export {};", DRIVER + b"\n" + DRIVER):
            with self.subTest(driver=driver), self.assertRaisesRegex(Refused, "driver import"):
                self.write("driver.mjs", driver)
                self.projector()

    def test_rejects_out_of_bounds_or_invalid_text_without_spawning(self):
        projector = self.projector()
        inputs = [(b"\xff", RECIPIENT), (b"x" * (census.MAX_LEDGER_BYTES + 1), RECIPIENT),
                  (b"", ""), (b"", "\ud800"), (b"", "x" * 2049)]
        with patch.object(census.subprocess, "run") as run:
            for ledger, recipient in inputs:
                with self.subTest(size=len(ledger), recipient_length=len(recipient)), self.assertRaises(Refused):
                    projector(ledger, recipient)
        run.assert_not_called()

    def test_result_requires_unique_integer_counts_and_bounds(self):
        with_duplicate = b'{"due-callbacks":0,' + json.dumps(ZERO).encode()[1:]
        malformed = [b"{", with_duplicate, b"x" * (census.MAX_RESULT_BYTES + 1),
                     json.dumps({**ZERO, "extra": 0}).encode(),
                     json.dumps({**ZERO, "eligible-pending": True}).encode(),
                     json.dumps({**ZERO, "eligible-pending": -1}).encode(),
                     json.dumps({**ZERO, "active-subscriptions": 1}).encode()]
        for result in malformed:
            with self.subTest(result_length=len(result)), self.assertRaises(Refused):
                census.callback_counts(result)
        self.assertEqual(census.callback_counts(json.dumps(ZERO).encode()), ZERO)
        retained = {**ZERO, "retained-subscriptions": 1, "active-subscriptions": 1}
        self.assertEqual(census.callback_counts(json.dumps(retained).encode()), retained)

    @unittest.skipUnless(shutil.which("node", path=census.SYSTEM_PATH), "system Node required")
    def test_native_module_receives_private_input_only_on_stdin(self):
        module = f"""
          export function summarize(text, owner, now) {{
            if (text !== {json.dumps(PRIVATE_MARKER)} || owner !== {json.dumps(RECIPIENT)}
                || !Number.isSafeInteger(now) || Object.keys(process.env).join(',') !== 'AXXIUM_PROBE_FD'
                || process.argv.some(arg => arg.includes(text) || arg.includes(owner))) {{
              throw new Error('untrusted diagnostic');
            }}
            console.error('private child stderr');
            return {json.dumps(ZERO)};
          }}
        """
        self.write("module.js", module.encode())
        projector = self.projector()
        self.write("module.js", b"this file changes after the pinned read")
        with patch.dict(os.environ, {"SECRET_SENTINEL": PRIVATE_MARKER}):
            self.assertEqual(projector(PRIVATE_MARKER.encode(), RECIPIENT), ZERO)

    def test_production_error_messages_do_not_copy_child_output(self):
        with patch.object(census.shutil, "which", return_value="/bin/node"):
            with patch.object(census.subprocess, "run", side_effect=subprocess.TimeoutExpired(
                ["node"], timeout=1, output=PRIVATE_MARKER, stderr=PRIVATE_MARKER
            )):
                with self.assertRaisesRegex(Refused, "^Callback projection could not complete$"):
                    self.projector()(b"", RECIPIENT)
            process = subprocess.CompletedProcess(["node"], 1, PRIVATE_MARKER.encode(), PRIVATE_MARKER)
            with patch.object(census.subprocess, "run", return_value=process):
                with self.assertRaisesRegex(Refused, "^Callback projection refused the private ledger$"):
                    self.projector()(b"", RECIPIENT)
