"""No daemon, network, privileged host path, or production writes."""
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('stealth_rebind_test', HERE / 'stealth_rebind.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
RUN = 'a' * 32


class RebindingTests(unittest.TestCase):
    def test_closed_targets_rebound_and_project_local_alias_preserved(self):
        source = """\
ROOT = '/srv/open-hax/state/axxium'
APP = 'axxium-axxium-1'
DB = 'axxium-axxium-db-1'
NETWORK = 'axxium_database'
INGRESS = 'open-hax'
KNOXX = 'knoxx-knoxx-backend-1'
KNOXX_ID = '92346c9e74b47fc816eefaae4294b0e9ed1a20545bf3e9a3f40263561095d7b5'
LABEL = 'label=open-hax.axxium-operation='
OWNED = 'codex.axxium-recovery-fixture=synthetic'
SERVICE = {'labels': {'codex.axxium-recovery-fixture': 'synthetic'}}
DB_ALIAS = 'axxium-db'
CMD = ['docker', 'compose', '--project-name', 'axxium', 'up']
import os
def context():
    return os.environ.get('GITHUB_ACTIONS') == 'true' and os.environ.get('RUNNER_ENVIRONMENT') == 'github-hosted'
IMAGE = 'promethean-axxium:' + 'candidate'
def check(x):
    if not x:
        raise ValueError('preserve refusal')
"""
        output, counts = module.transform(source, RUN, Path('/tmp/synthetic-fixture/open-hax'))
        result = {}
        exec(compile(output, '<test>', 'exec'), result)
        prefix = 'axxium-fixture-' + RUN
        self.assertEqual(result['ROOT'], '/tmp/synthetic-fixture/open-hax/state/axxium')
        self.assertEqual(result['APP'], prefix + '-axxium-1')
        self.assertEqual(result['DB'], prefix + '-axxium-db-1')
        self.assertEqual(result['NETWORK'], prefix + '-database')
        self.assertEqual(result['INGRESS'], prefix + '-ingress')
        self.assertEqual(result['KNOXX'], prefix + '-not-a-live-knoxx')
        self.assertNotEqual(result['KNOXX_ID'], module.OLD_KNOXX_ID)
        self.assertEqual(result['DB_ALIAS'], 'axxium-db')
        self.assertEqual(result['CMD'][3], prefix)
        self.assertEqual(result['IMAGE'], prefix + ':fixture-' + RUN)
        with patch.dict(os.environ, {'AXXIUM_FIXTURE_MODE': 'local-isolated-v1',
                                     'AXXIUM_FIXTURE_HOST': 'stealth'}, clear=True):
            self.assertTrue(result['context']())
        with patch.dict(os.environ, {'GITHUB_ACTIONS': 'true',
                                     'RUNNER_ENVIRONMENT': 'github-hosted'}, clear=True):
            self.assertFalse(result['context']())
        self.assertEqual(result['LABEL'], 'label=open-hax.fixture-' + RUN + '=')
        self.assertEqual(result['OWNED'], 'codex.axxium-recovery-fixture=' + RUN)
        self.assertEqual(result['SERVICE']['labels']['codex.axxium-recovery-fixture'], RUN)
        self.assertIn('compose-project', counts)
        with self.assertRaises(ValueError):
            result['check'](False)

    def test_crypto_and_rehearsal_are_named_labeled_and_retained(self):
        source = """\
import secrets
def crypto():
    return ['docker', 'run', '--rm', '-i', '--network', 'none', 'image']
def rehearsal(name):
    return ['docker', 'run', '-d', '--name', name, '--network', 'axxium_database', 'image']
"""
        output, counts = module.transform(source, RUN, Path('/tmp/synthetic-fixture/open-hax'))
        result = {}
        exec(compile(output, '<test>', 'exec'), result)
        crypto = result['crypto']()
        rehearsal = result['rehearsal']('known-name')
        for command in (crypto, rehearsal):
            self.assertIn('codex.axxium-recovery-fixture=' + RUN, command)
            self.assertIn('--name', command)
        self.assertNotIn('--rm', crypto)
        self.assertEqual(crypto[crypto.index('--network') + 1], 'none')
        self.assertTrue(crypto[crypto.index('--name') + 1].startswith('axxium-fixture-' + RUN + '-crypto-'))
        self.assertEqual(rehearsal[rehearsal.index('--name') + 1], 'known-name')
        self.assertEqual(counts['owned-container'], 2)

    def test_caddy_declared_volume_mounts_are_temporary(self):
        source = """\
CADDY = 'caddy-caddy-1'
def command(*args): return args
def start(): return command('docker', 'run', '-d', '--name', CADDY, 'image')
"""
        output, counts = module.transform(source, RUN, Path('/tmp/synthetic-fixture/open-hax'))
        result = {}
        exec(compile(output, '<test>', 'exec'), result)
        args = result['start']()
        self.assertIn('/data:rw,nosuid,nodev,size=32m', args)
        self.assertIn('/config:rw,nosuid,nodev,size=16m', args)
        self.assertEqual(counts['caddy-tmpfs'], 1)

    def test_unknown_embedded_original_targets_fail_closed(self):
        for text in ("X='--network=axxium_database'", "X='prefix-axxium-axxium-1-suffix'"):
            with self.subTest(text=text), self.assertRaises(module.Refused):
                module.transform(text, RUN, Path('/tmp/synthetic-fixture/open-hax'))

    def test_invalid_run_and_nonprivate_root_refuse(self):
        with self.assertRaises(module.Refused):
            module.transform('X=1', 'bad', Path('/tmp/synthetic-fixture/open-hax'))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'existing').write_text('x')
            with self.assertRaises(module.Refused):
                module.checked_root(root)

    def test_preparation_pins_inputs_and_keeps_source_unmodified(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'src'
            source.mkdir()
            run = root / 'run'
            run.mkdir(mode=0o700)
            inputs = {}
            for relative in module.FILES:
                target = source / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("VALUE = '/srv/open-hax/state'\n")
                inputs[relative] = hashlib.sha256(target.read_bytes()).hexdigest()
            plan = source / module.PLAN
            plan.parent.mkdir(parents=True, exist_ok=True)
            plan.write_text('{}\n')
            inputs[module.PLAN] = hashlib.sha256(plan.read_bytes()).hexdigest()
            with patch.object(module, 'SOURCE_DIGESTS', inputs):
                manifest = module.prepare(source, run, RUN)
            copied = run / 'source' / module.FILES[0]
            self.assertIn(str(run / 'open-hax'), copied.read_text())
            self.assertEqual((source / module.FILES[0]).read_text(), "VALUE = '/srv/open-hax/state'\n")
            self.assertEqual(manifest['files'][module.FILES[0]]['input_sha256'], inputs[module.FILES[0]])
            self.assertEqual(copied.stat().st_mode & 0o777, 0o600)
            self.assertEqual(json.loads((run / 'rebind-manifest.json').read_text()), manifest)

    def test_changed_source_refuses_before_creating_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, run = root / 'src', root / 'run'
            source.mkdir()
            run.mkdir(mode=0o700)
            for relative in (*module.FILES, module.PLAN):
                target = source / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text('changed')
            with self.assertRaises(module.Refused):
                module.prepare(source, run, RUN)
            self.assertEqual(list(run.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
