"""Exercise the actual reusable workflow's backend-output checks with fixtures."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / '.github/workflows/build-images.yml'


class KnoxxWorkflowRuntimeTest(unittest.TestCase):
    def run_checks(self, kind):
        text = WORKFLOW.read_text()
        step = text.split('- name: Build Knoxx backend bundle', 1)[1].split('\n      # The workflow repository', 1)[0]
        self.assertIn('set -euo pipefail', step)
        checks = [line.strip() for line in step.splitlines() if line.strip().startswith(('test -f backend/dist/server.js', 'test -d backend/dist/cljs-runtime', 'runtime_js=$(find backend/dist/cljs-runtime', 'test -n "$runtime_js"'))]
        self.assertEqual(len(checks), 4, 'workflow must check the server, directory, and regular JS content')
        self.assertLess(text.index('runtime_js=$(find backend/dist/cljs-runtime'), text.index('docker build -t knoxx-backend-base:local'))
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            runtime = root / 'backend/dist/cljs-runtime'
            runtime.mkdir(parents=True)
            (root / 'backend/dist/server.js').write_text('// server\n')
            if kind == 'js':
                (runtime / 'nested').mkdir()
                (runtime / 'nested/module.js').write_text('// runtime\n')
            elif kind == 'non-js':
                (runtime / 'module.txt').write_text('not JS\n')
            elif kind == 'symlink':
                target = root / 'outside.js'
                target.write_text('// target\n')
                os.symlink(target, runtime / 'module.js')
            env = os.environ.copy()
            if kind == 'find-error':
                bindir = root / 'bin'
                bindir.mkdir()
                fake_find = bindir / 'find'
                fake_find.write_text('#!/bin/sh\nprintf "%s\\n" backend/dist/cljs-runtime/module.js\nexit 42\n')
                fake_find.chmod(0o755)
                env['PATH'] = str(bindir) + os.pathsep + env.get('PATH', '')
            return subprocess.run(['bash', '-e', '-c', '\n'.join(checks)], cwd=root, env=env, capture_output=True).returncode

    def test_regular_javascript_passes(self):
        self.assertEqual(self.run_checks('js'), 0)

    def test_empty_tree_fails(self):
        self.assertNotEqual(self.run_checks('empty'), 0)

    def test_non_javascript_tree_fails(self):
        self.assertNotEqual(self.run_checks('non-js'), 0)

    def test_symlink_only_tree_fails(self):
        self.assertNotEqual(self.run_checks('symlink'), 0)

    def test_find_error_with_output_fails(self):
        self.assertEqual(self.run_checks('find-error'), 42)


if __name__ == '__main__':
    unittest.main()
