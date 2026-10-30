"""Exercise the real embedded Node probe with a synthetic HTTP boundary."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import sys

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location('rehearsal_release', SCRIPTS / 'axxium_minimal_release.py')
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)
ACTOR = 'actor_00000000-0000-4000-8000-000000000001'
CLONE = 'axxium_verify_' + 'a' * 24


class FakeHost(release.NativeHost):
    def __init__(self, root, *, actor=ACTOR, me=ACTOR, cookie='session=ok', db_result=b't\n'):
        super().__init__()
        self.operation = root
        self.old_environment = {}
        self.proxy_ip = '127.0.0.1'
        self.actor, self.me, self.cookie, self.db_result = actor, me, cookie, db_result
        self.commands = []
        self.queries = []

    def private_write(self, path, data): pass
    def wait_app(self, name): pass
    def sql(self, database, query):
        self.queries.append((database, query))
        return self.db_result

    def command(self, args, *, data=None, **kwargs):
        self.commands.append(args)
        if 'node' not in args:
            return b''
        program = args[args.index('-e') + 1]
        fixture = json.dumps({'actor': self.actor, 'me': self.me, 'cookie': self.cookie})
        prelude = '''const fixture = FIXTURE;
            global.fetch = async (url, options = {}) => {
              if (url.endsWith('/api/auth/signup')) return {status:200};
              if (url.endsWith('/api/auth/login')) return {
                status:200, headers:{get:()=>fixture.cookie},
                json:async()=>({actor:{id:fixture.actor}})
              };
              if (url.endsWith('/api/actors/me')) return {
                status:options.headers?.cookie === 'session=ok' ? 200 : 401,
                json:async()=>({actor:{id:fixture.me}})
              };
              throw new Error('unexpected URL');
            };
        '''.replace('FIXTURE', fixture)
        result = subprocess.run(['node', '-e', prelude + program], input=data,
                                capture_output=True, timeout=10)
        if result.returncode:
            raise release.Uncertain('Synthetic probe failure')
        return result.stdout


class RehearsalProbeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.rehearsal_env = patch.object(release, 'rehearsal_environment', return_value={})
        self.rehearsal_env.start()
        self.addCleanup(self.rehearsal_env.stop)

    def run_probe(self, **options):
        host = FakeHost(self.root, **options)
        try:
            host.probe({}, CLONE, 'fixture-image', {'email': 'synthetic', 'password': 'synthetic'}, True)
        except Exception as error:
            return host, error
        return host, None

    def test_successful_login_without_kind_checks_human_in_clone(self):
        host, error = self.run_probe()
        self.assertIsNone(error)
        self.assertEqual(len(host.queries), 1)
        self.assertEqual(host.queries[0][0], CLONE)
        self.assertIn(ACTOR, host.queries[0][1])
        self.assertIn('human', host.queries[0][1])
        self.assertTrue(any(cmd[:2] == ['docker', 'stop'] for cmd in host.commands))
        self.assertTrue(any(cmd[:2] == ['docker', 'rm'] for cmd in host.commands))

    def test_missing_or_wrong_cookie_and_mismatched_me_fail_before_sql(self):
        for options in ({'cookie': None}, {'cookie': 'session=wrong'}, {'me': 'actor_other'}):
            with self.subTest(options=options):
                host, error = self.run_probe(**options)
                self.assertIsNotNone(error)
                self.assertEqual(host.queries, [])
                self.assertFalse(any(cmd[:2] in (['docker', 'stop'], ['docker', 'rm']) for cmd in host.commands))

    def test_nonhuman_actor_is_refused(self):
        host, error = self.run_probe(db_result=b'f\n')
        self.assertIsNotNone(error)
        self.assertEqual(len(host.queries), 1)
        self.assertFalse(any(cmd[:2] in (['docker', 'stop'], ['docker', 'rm']) for cmd in host.commands))

    def test_malformed_or_malicious_actor_refused_before_sql(self):
        for bad in ('actor_foo', "actor_00000000-0000-4000-8000-000000000001' OR TRUE--", '', None):
            with self.subTest(bad=bad):
                host, error = self.run_probe(actor=bad, me=bad)
                self.assertIsNotNone(error)
                self.assertEqual(host.queries, [])
                self.assertFalse(any(cmd[:2] in (['docker', 'stop'], ['docker', 'rm']) for cmd in host.commands))


if __name__ == '__main__': unittest.main()
