#!/usr/bin/env python3
"""Exercise actual deploy-render and verifier boundaries with synthetic secrets."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github/workflows/deploy-digitalocean.yml'
INVALID = {
    'missing': None,
    'empty': '',
    'short hex': 'a' * 63,
    'long hex': 'a' * 65,
    'URL-safe text': 'A' * 63 + '_',
    'nonhex letter': 'a' * 63 + 'g',
    'leading space': ' ' + 'a' * 63,
    'trailing space': 'a' * 63 + ' ',
    'trailing newline after valid hex': 'a' * 64 + '\n',
    'CRLF after valid hex': 'a' * 64 + '\r\n',
    'newline with another assignment': 'a' * 64 + '\nSYNTHETIC_OTHER=value',
    'non-ASCII hex lookalike': 'a' * 63 + 'Ａ',
    'prefixed hex': '0x' + 'a' * 62,
}


class SessionSecretDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='knoxx-session-secret-test-')
        self.addCleanup(self.directory.cleanup)
        self.fixture = Path(self.directory.name)
        self.bin = self.fixture / 'bin'
        self.bin.mkdir()
        (self.fixture/'scripts').symlink_to(ROOT/'scripts',target_is_directory=True)
        # Rendering and Docker are effects, not the decision under test. These
        # sentinels record whether the actual authored shell admitted an input;
        # they never parse or validate a key and never invoke the Docker daemon.
        for name in ['envsubst', 'docker']:
            script = self.bin / name
            script.write_text(
                '#!/usr/bin/env python3\n'
                'import json, os, pathlib, sys\n'
                f'pathlib.Path(os.environ["TEST_{name.upper()}_MARKER"]).write_text('
                'json.dumps({"session":os.environ.get("KNOXX_SESSION_SECRET")}))\n'
                + ('print("SYNTHETIC_RENDER_REACHED=\\\'yes\\\'")\n'
                   if name == 'envsubst' else 'sys.exit(73)\n')
            )
            script.chmod(0o755)
        self.env = {
            'PATH': str(self.bin) + os.pathsep + os.environ['PATH'],
            'TEST_ENVSUBST_MARKER': str(self.fixture / 'render-called.json'),
            'TEST_DOCKER_MARKER': str(self.fixture / 'docker-called.json'),
            'KNOXX_API_KEY': 'synthetic-api-key',
        }

    def run_render(self, base, overlay=None, service='knoxx', raw_overlay=None):
        document = yaml.load(WORKFLOW.read_text(), Loader=yaml.BaseLoader)
        step = next(step for step in document['jobs']['deploy']['steps']
                    if step.get('name') == 'Render runtime environment')
        template = ROOT / 'digitalocean/services' / service / 'env.template'
        env = dict(self.env)
        env.update({key: 'synthetic-nonsecret-value'
                    for key in re.findall(r'\$\{([A-Z][A-Z0-9_]*)\}', template.read_text())})
        service_fixture=self.fixture/service
        if not service_fixture.exists():shutil.copytree(template.parent,service_fixture)
        env.update({'SERVICE': service, 'SERVICE_DIR': str(service_fixture),
                    'EXTRA_ENV': raw_overlay if raw_overlay is not None else json.dumps(overlay or {}),
                    'KNOXX_MCP_LOOPBACK_TOKEN': 'synthetic-mcp-token'})
        if base is None:
            env.pop('KNOXX_SESSION_SECRET', None)
        else:
            env['KNOXX_SESSION_SECRET'] = base
        # The workflow resolves this existing library from checkout cwd. Source
        # the actual library, while confining rendered.env to the fixture cwd.
        script = step['run'].replace(
            '. digitalocean/lib/caddy-dev-auth.sh',
            '. "' + str(ROOT / 'digitalocean/lib/caddy-dev-auth.sh') + '"')
        return subprocess.run(['bash', '-c', script], cwd=self.fixture,
                              env=env, text=True, capture_output=True)

    def run_verify(self, key):
        env = dict(self.env)
        if key is not None:
            env['KNOXX_SESSION_SECRET'] = key
        return subprocess.run(['bash', str(ROOT / 'digitalocean/services/knoxx/verify.sh')],
                              cwd=self.fixture, env=env, text=True, capture_output=True)

    def reset_markers(self):
        for name in ['render-called.json', 'docker-called.json', 'rendered.env']:
            (self.fixture / name).unlink(missing_ok=True)

    def assert_refused_without_effect(self, result, marker, value):
        self.assertEqual(result.returncode, 2)
        self.assertFalse((self.fixture / marker).exists(), 'invalid key reached an external effect')
        self.assertIn('KNOXX_SESSION_SECRET', result.stderr)
        self.assertIn('64', result.stderr)
        if value:
            self.assertFalse(value in result.stdout + result.stderr, 'diagnostic exposed the synthetic key')

    def test_render_rejects_bad_base_before_substitution(self):
        for label, value in INVALID.items():
            with self.subTest(label=label):
                self.reset_markers()
                self.assert_refused_without_effect(self.run_render(value), 'render-called.json', value)
                self.assertFalse((self.fixture / 'rendered.env').exists())

    def test_bad_overlay_replaces_valid_base_and_is_rejected(self):
        for label, value in INVALID.items():
            if value is None:
                continue
            with self.subTest(label=label):
                self.reset_markers()
                self.assert_refused_without_effect(
                    self.run_render('a' * 64, {'KNOXX_SESSION_SECRET': value}),
                    'render-called.json', value)

    def test_valid_final_values_are_preserved_after_overlay(self):
        for label, base, overlay, expected in [
            ('lowercase', 'a' * 64, None, 'a' * 64),
            ('uppercase', 'ABCDEF01' * 8, None, 'ABCDEF01' * 8),
            ('mixed case', 'aBcDeF01' * 8, None, 'aBcDeF01' * 8),
            ('valid overlay replaces malformed base', 'malformed', {'KNOXX_SESSION_SECRET': 'B' * 64}, 'B' * 64),
            ('valid overlay supplies missing base', None, {'KNOXX_SESSION_SECRET': 'c' * 64}, 'c' * 64),
        ]:
            with self.subTest(label=label):
                self.reset_markers()
                result = self.run_render(base, overlay)
                self.assertEqual(result.returncode, 0, 'valid final key was refused')
                captured = json.loads((self.fixture / 'render-called.json').read_text())
                self.assertTrue(captured['session'] == expected, 'valid final key changed')
                self.assertFalse((self.fixture / 'docker-called.json').exists())

    def test_raw_overlay_refuses_unrepresentable_or_nonstring_keys(self):
        for label, value in [('embedded NUL', 'a' * 64 + '\0'),
                             ('null', None), ('boolean', True),
                             ('array', ['a' * 64])]:
            with self.subTest(label=label):
                self.reset_markers()
                self.assert_refused_without_effect(
                    self.run_render('b' * 64, {'KNOXX_SESSION_SECRET': value}),
                    'render-called.json', value if isinstance(value, str) else None)

    def test_raw_overlay_requires_one_json_object_before_rendering(self):
        for label, raw in [('malformed', '{'), ('array', '[]'), ('string', '"text"'),
                           ('number', '42'), ('boolean', 'true'), ('null', 'null'),
                           ('whitespace only', ' \n'), ('array then object', '[] {}'),
                           ('two objects', '{} {}')]:
            with self.subTest(label=label):
                self.reset_markers()
                result = self.run_render('a' * 64, raw_overlay=raw)
                self.assertEqual(2, result.returncode, 'invalid overlay reached rendering')
                self.assertIn('extra_env must be a valid JSON object', result.stderr)
                self.assertFalse((self.fixture / 'render-called.json').exists())
                self.assertFalse((self.fixture / 'docker-called.json').exists())
                self.assertFalse((self.fixture / 'rendered.env').exists())
                self.assertNotIn('a' * 64, result.stdout + result.stderr)
        for raw in ('{}', ' \n{}\n ', '{"SYNTHETIC_OTHER":"value"}'):
            with self.subTest(valid_object=raw):
                self.reset_markers()
                result = self.run_render('a' * 64, raw_overlay=raw)
                self.assertEqual(0, result.returncode, result.stderr)
                captured = json.loads((self.fixture / 'render-called.json').read_text())
                self.assertEqual('a' * 64, captured['session'], 'absent overlay key changed the base')

    def test_original_service_context_cannot_be_shadowed(self):
        for label, base, overlay in [
            ('missing candidate', None, {'SERVICE': 'smoke'}),
            ('malformed candidate', 'malformed', {'SERVICE': 'smoke'}),
            ('malformed overlaid candidate', 'a' * 64,
             {'SERVICE': 'smoke', 'KNOXX_SESSION_SECRET': 'malformed'}),
            ('injected later malformed value', 'a' * 64,
             {'SYNTHETIC_OTHER': 'value\nSERVICE=smoke\nKNOXX_SESSION_SECRET=malformed'}),
        ]:
            with self.subTest(label=label):
                self.reset_markers()
                self.assert_refused_without_effect(self.run_render(base, overlay),
                                                   'render-called.json', base)
        self.reset_markers()
        result = self.run_render('a' * 64, {'SERVICE': 'smoke'})
        self.assertEqual(result.returncode, 0)
        self.assertTrue((self.fixture / 'render-called.json').exists())

    def test_other_service_does_not_require_a_knoxx_secret(self):
        result = self.run_render(None, service='smoke')
        self.assertEqual(result.returncode, 0)
        self.assertTrue((self.fixture / 'render-called.json').exists())

    def test_verifier_rejects_bad_key_before_any_docker_probe(self):
        for label, value in INVALID.items():
            with self.subTest(label=label):
                self.reset_markers()
                self.assert_refused_without_effect(self.run_verify(value), 'docker-called.json', value)

    def test_verifier_admits_valid_key_unchanged(self):
        for label, value in [('lowercase', 'a' * 64), ('uppercase', 'ABCDEF01' * 8)]:
            with self.subTest(label=label):
                self.reset_markers()
                result = self.run_verify(value)
                self.assertEqual(result.returncode, 73, 'valid key did not reach the sentinel Docker probe')
                captured = json.loads((self.fixture / 'docker-called.json').read_text())
                self.assertTrue(captured['session'] == value, 'verifier changed the valid key')


class SessionSecretWiringTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            'session_secret_environment_contract', ROOT / 'scripts/check-env-contracts.py')
        self.checker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.checker)
        self.render = WORKFLOW.read_text()
        self.verify_path = ROOT / 'digitalocean/services/knoxx/verify.sh'
        self.verify = self.verify_path.read_text()

    def replace_statement(self, text, statement, replacement):
        lines = text.splitlines()
        matches = [i for i, line in enumerate(lines) if line.strip() == statement]
        self.assertEqual(len(matches), 1, 'mutation must target one authored statement')
        index = matches[0]
        indent = lines[index][:len(lines[index]) - len(lines[index].lstrip())]
        lines[index] = '\n'.join(indent + part for part in replacement.splitlines())
        return '\n'.join(lines) + '\n'

    def run_contract(self, path, text):
        # Execute the real checker against a substituted source read. Every
        # other repository input stays real; no shell, Docker, or provider runs.
        original_read = Path.read_text

        def read(candidate, *args, **kwargs):
            return text if candidate == path else original_read(candidate, *args, **kwargs)

        stderr = io.StringIO()
        with mock.patch.object(Path, 'read_text', read), \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(stderr):
            result = self.checker.main()
        return result, stderr.getvalue()

    def test_render_wiring_rejects_comments_renames_and_wrong_control_flow(self):
        source = '. "${SERVICE_DIR}/session-secret-policy.sh"'
        overlay = 'require_knoxx_session_secret_overlay "${EXTRA_ENV:-\\{\\}}"'
        validate = 'require_knoxx_session_secret'
        render = 'envsubst "${declared[*]}" < "${SERVICE_DIR}/env.template" > rendered.env'
        capture = 'readonly render_session_secret_service=$SERVICE'
        cases = [
            ('commented source', source, '# ' + source),
            ('commented validator', validate, '# ' + validate),
            ('commented overlay admission', overlay, '# ' + overlay),
            ('wrong policy path', source, source.replace('/session-', '/untrusted-session-')),
            ('renamed validator', validate, 'skip_' + validate),
            ('renamed overlay admission', overlay, 'skip_' + overlay),
            ('mutable original service', capture, capture.removeprefix('readonly ')),
            ('validator in unused function', validate, 'unused_session_guard() {\n' + validate + '\n}'),
            ('source after rendering', source, '# ' + source),
            ('validator after rendering', validate, '# ' + validate),
            ('guard in false branch', capture, 'if false; then\n' + capture),
            ('guard in unused function', capture, 'unused_render_guard() {\n' + capture),
        ]
        for label, before, after in cases:
            with self.subTest(label=label):
                mutated = self.replace_statement(self.render, before, after)
                if label == 'source after rendering':
                    mutated = self.replace_statement(mutated, render, render + '\n' + source)
                elif label == 'validator after rendering':
                    mutated = self.replace_statement(mutated, render, render + '\n' + validate)
                elif label == 'guard in false branch':
                    mutated = self.replace_statement(mutated, render, render + '\nfi')
                elif label == 'guard in unused function':
                    mutated = self.replace_statement(mutated, render, render + '\n}')
                result, error = self.run_contract(WORKFLOW, mutated)
                self.assertEqual(result, 1, 'inactive or misplaced wiring was accepted')
                self.assertIn('Knoxx render', error)

    def test_verifier_wiring_rejects_comments_renames_and_late_calls(self):
        source = '. "$verify_dir/session-secret-policy.sh"'
        validate = 'require_knoxx_session_secret'
        boundary = 'FRONTEND=http://127.0.0.1:8080'
        cases = [
            ('commented source', source, '# ' + source),
            ('commented validator', validate, '# ' + validate),
            ('wrong policy path', source, source.replace('/session-', '/untrusted-session-')),
            ('renamed validator', validate, 'skip_' + validate),
            ('source after probe setup', source, '# ' + source),
            ('validator after probe setup', validate, '# ' + validate),
            ('validator in false branch', validate, 'if false; then\n' + validate + '\nfi'),
            ('validator in unused function', validate, 'unused_session_guard() {\n' + validate + '\n}'),
        ]
        for label, before, after in cases:
            with self.subTest(label=label):
                mutated = self.replace_statement(self.verify, before, after)
                if label == 'source after probe setup':
                    mutated = self.replace_statement(mutated, boundary, boundary + '\n' + source)
                elif label == 'validator after probe setup':
                    mutated = self.replace_statement(mutated, boundary, boundary + '\n' + validate)
                result, error = self.run_contract(self.verify_path, mutated)
                self.assertEqual(result, 1, 'inactive or misplaced wiring was accepted')
                self.assertIn('Knoxx verification', error)

    def test_current_wiring_tolerates_comments_and_blank_lines(self):
        for path, text in [(WORKFLOW, self.render), (self.verify_path, self.verify)]:
            with self.subTest(path=path.name):
                source = next(line.strip() for line in text.splitlines()
                              if line.strip().startswith('. ') and 'session-secret-policy.sh' in line)
                annotated = self.replace_statement(text, source, '# explanatory comment\n\n' + source)
                result, error = self.run_contract(path, annotated)
                self.assertEqual(result, 0, error)


if __name__ == '__main__':
    unittest.main()
