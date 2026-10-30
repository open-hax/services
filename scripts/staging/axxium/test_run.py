#!/usr/bin/env python3
"""Offline orchestration tests: no Docker daemon or network is contacted."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parent
CANDIDATE = 'sha256:6aed20a3439dc29c6bcb43cf3f1e0bf41f65a72734975eb2db922898c45e4652'
POSTGRES = 'sha256:cf78e76683b9ca8c5733cbbdce6c9262b45b6767934dd0a95e671f9a0fc20685'
RUN_ID = 'f' * 32
NETWORK_ID, DB_ID, APP_ID = 'a' * 64, 'b' * 64, 'c' * 64

DOCKER = r'''#!__PYTHON__
import json, os, pathlib, sys
a = sys.argv[1:]
root = pathlib.Path(os.environ['FAKE_DOCKER_DIR'])
with (root/'calls.jsonl').open('a') as f: f.write(json.dumps(a)+'\n')
scenario = os.environ.get('FAKE_SCENARIO', '')
state_path = root/'state.json'
state = json.loads(state_path.read_text()) if state_path.exists() else {}
def save(): state_path.write_text(json.dumps(state))
def arg(name): return a[a.index(name)+1]
if a[:2] == ['image','inspect']:
    print(a[-1])
elif a[:2] == ['network','create']:
    if scenario == 'unknown_network': sys.exit(124)
    state['network_name'] = a[-1]; state['label'] = arg('--label').split('=',1)[1]; save(); print('__NETWORK__')
elif a[0] == 'run':
    name = arg('--name'); is_app = name.startswith('axxium-oidc-app-')
    if is_app and scenario == 'unknown_app': sys.exit(124)
    ident = '__APP__' if is_app else '__DB__'
    state[ident] = {'name':name, 'label':arg('--label').split('=',1)[1]}; save()
    pathlib.Path(arg('--cidfile')).write_text(ident)
    print(ident)
elif a[0] == 'exec':
    if 'node' in a: print('synthetic-only-client-secret',end='')
elif a[0] == 'inspect':
    ident = a[-1]; item=state[ident]; label=item['label']; fmt=arg('--format')
    if scenario == 'metadata_timeout' and '.Image' in fmt: sys.exit(124)
    if '.State.Status' in fmt: print('"running" true 0 {"8787/tcp":null} null')
    elif '.HostConfig.PortBindings' in fmt: print('{"8787/tcp":[{"HostIp":"0.0.0.0"}]}' if scenario == 'public_binding' else 'null')
    elif '.HostConfig.PublishAllPorts' in fmt: print('true' if scenario == 'publish_all' else 'false')
    elif '.NetworkSettings.Ports' in fmt: print('{"8787/tcp":[{"HostIp":"127.0.0.1","HostPort":"1234"}]}' if scenario == 'network_port_map' else '{"8787/tcp":null}')
    elif '.Image' in fmt: print(ident, '/' + item['name'], label, 'sha256:6aed20a3439dc29c6bcb43cf3f1e0bf41f65a72734975eb2db922898c45e4652', '1')
    elif 'with index .NetworkSettings.Networks' in fmt: print('__NETWORK__','172.29.0.3' if scenario != 'bad_ip' else '127.0.0.1')
    else:
        if scenario == 'foreign_app_label' and ident == '__APP__': label='unowned'
        print(ident, '/' + item['name'], label)
elif a[:2] == ['network','inspect']:
    fmt=arg('--format')
    if '.IPAM.Config' in fmt: print(json.dumps([{'Subnet':'172.29.0.0/24','Gateway':'172.29.0.1'}]))
    elif '.Containers' in fmt:
        peers={'__APP__':{'IPv4Address':'172.29.0.3/24'},'__DB__':{'IPv4Address':'172.29.0.2/24'}}
        if scenario == 'foreign_peer': peers['d'*64]={'IPv4Address':'172.29.0.4/24'}
        print(json.dumps(peers))
    else:
        label=state['label']
        if scenario == 'foreign_network_label' and '.Internal' not in fmt: label='unowned'
        print('__NETWORK__',state['network_name'],label,*(['true'] if '.Internal' in fmt else []))
elif a[0] == 'rm' or a[:2] == ['network','rm']:
    if scenario == 'uncertain_remove' and a[-1] == '__APP__': sys.exit(124)
else:
    print('unexpected docker call',file=sys.stderr); sys.exit(2)
'''.replace('__PYTHON__', sys.executable).replace('__NETWORK__', NETWORK_ID).replace('__DB__', DB_ID).replace('__APP__', APP_ID)

PYTHON = r'''#!__PYTHON__
import os, pathlib, sys
a=sys.argv[1:]
if a and a[0]=='-c':
    program=a[1]
    if 'secrets.token_hex' in program: print('__RUN_ID__')
    elif 'cryptography' in program or 'http.client' in program: pass
    else: sys.exit(3)
elif a and a[0]=='-':
    os.execv('__PYTHON__', ['__PYTHON__', *a])
elif a and a[0].endswith('/probe.py'):
    p=pathlib.Path(a[a.index('--client-secret-file')+1])
    if p.read_text()!='synthetic-only-client-secret' or p.stat().st_mode & 0o777 != 0o600: sys.exit(4)
    if a[a.index('--base')+1]!='http://127.0.0.1:8787' or a[a.index('--transport-ip')+1]!='172.29.0.3': sys.exit(6)
    print('synthetic probe passed')
elif a and a[0].endswith('/handoff_probe.py'):
    if a[1:] != ['--db-id', '__DB__', '--app-id', '__APP__', '--network-id', '__NETWORK__', '--run-id', '__RUN_ID__']: sys.exit(7)
    print('synthetic handoff passed')
else: sys.exit(5)
'''.replace('__PYTHON__', sys.executable).replace('__RUN_ID__', RUN_ID).replace('__DB__', DB_ID).replace('__APP__', APP_ID).replace('__NETWORK__', NETWORK_ID)


class OrchestrationTests(unittest.TestCase):
    def run_harness(self, scenario='', candidate=CANDIDATE, optimized=False):
        with tempfile.TemporaryDirectory(prefix='axxium-harness-unit-') as temporary:
            root = Path(temporary)
            binary = root / 'bin'
            binary.mkdir()
            for name, text in [('docker', DOCKER), ('python3', PYTHON)]:
                p = binary / name
                p.write_text(text)
                p.chmod(0o755)
            env = {'PATH': str(binary) + ':/usr/bin:/bin', 'FAKE_DOCKER_DIR': str(root),
                   'GITHUB_ACTIONS': 'true', 'RUNNER_ENVIRONMENT': 'github-hosted',
                   'FAKE_SCENARIO': scenario, 'LANG': 'C.UTF-8'}
            if optimized:
                env['PYTHONOPTIMIZE'] = '1'
            result = subprocess.run(['bash', str(ROOT/'run.sh'), candidate, POSTGRES],
                                    env=env, capture_output=True, text=True, timeout=15)
            calls_path = root/'calls.jsonl'
            calls = [json.loads(line) for line in calls_path.read_text().splitlines()] if calls_path.exists() else []
            # These failure cases deliberately retain the fresh directory; only
            # the fake-Docker test removes its exact reported synthetic scratch.
            match = re.search(r'Private synthetic state retained in (/tmp/axxium-oidc-test\.[A-Za-z0-9]+)\.', result.stderr)
            if match:
                scratch = Path(match.group(1))
                self.assertEqual(scratch.parent, Path('/tmp'))
                self.assertTrue(scratch.is_dir())
                shutil.rmtree(scratch)
            return result, calls

    def test_owned_resources_use_exact_ids_and_loopback_only(self):
        result, calls = self.run_harness()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('synthetic-only-client-secret', result.stdout + result.stderr)
        network = next(c for c in calls if c[:2] == ['network', 'create'])
        self.assertIn('--internal', network)
        runs = [c for c in calls if c[0] == 'run']
        self.assertEqual(len(runs), 2)
        self.assertTrue(all('--pull' in c and c[c.index('--pull')+1] == 'never' for c in runs))
        self.assertTrue(all('--read-only' in c and not any(x in c for x in ('--mount', '-v', '--volume')) for c in runs))
        self.assertTrue(all('--publish' not in c and '-p' not in c for c in runs))
        app = next(c for c in runs if c[c.index('--name')+1].startswith('axxium-oidc-app-'))
        self.assertIn('AXXIUM_PUBLIC_BASE_URL=http://127.0.0.1:8787', app)
        self.assertEqual(app[app.index('--network')+1], NETWORK_ID)
        self.assertTrue(any(c[0]=='run' and '--user' in c and c[c.index('--user')+1]=='70:70' for c in calls))
        removals = [c for c in calls if c[0] == 'rm']
        self.assertEqual(removals, [['rm', '--force', APP_ID], ['rm', '--force', DB_ID]])
        self.assertIn(['network', 'rm', NETWORK_ID], calls)
        self.assertLess(calls.index(['network', 'rm', NETWORK_ID]), len(calls))

    def test_foreign_container_label_is_never_removed(self):
        result, calls = self.run_harness('foreign_app_label')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Refused cleanup: container identity or ownership label changed', result.stderr)
        self.assertNotIn(['rm', '--force', APP_ID], calls)
        self.assertIn(['rm', '--force', DB_ID], calls)

    def test_foreign_network_label_is_never_removed(self):
        result, calls = self.run_harness('foreign_network_label')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Refused cleanup: network identity or ownership label changed', result.stderr)
        self.assertNotIn(['network', 'rm', NETWORK_ID], calls)

    def test_unknown_candidate_creation_is_reported_not_guessed(self):
        result, calls = self.run_harness('unknown_app')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Automatic cleanup refused after an uncertain Docker outcome', result.stderr)
        self.assertNotIn(['rm', '--force', APP_ID], calls)
        self.assertNotIn(['rm', '--force', DB_ID], calls)
        self.assertNotIn(['network', 'rm', NETWORK_ID], calls)

    def test_unknown_network_creation_never_guesses_cleanup(self):
        result, calls = self.run_harness('unknown_network')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Automatic cleanup refused after an uncertain Docker outcome', result.stderr)
        self.assertNotIn(['network', 'rm', NETWORK_ID], calls)
        self.assertIn(RUN_ID, result.stdout)

    def test_unexpected_published_port_refused_before_probe(self):
        result, calls = self.run_harness('public_binding')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('unexpectedly publishes host ports', result.stderr)
        self.assertIn('Candidate network diagnostics', result.stderr)
        self.assertNotIn('synthetic probe passed', result.stdout)

    def test_publish_all_refused_even_without_explicit_binding(self):
        result, calls = self.run_harness('publish_all')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('unexpectedly publishes host ports', result.stderr)
        self.assertIn('Candidate network diagnostics', result.stderr)
        self.assertNotIn('synthetic probe passed', result.stdout)

    def test_effective_network_port_mapping_refused_even_without_host_binding(self):
        result, calls = self.run_harness('network_port_map')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('unexpectedly publishes host ports', result.stderr)
        self.assertIn('Candidate network diagnostics', result.stderr)
        self.assertNotIn('synthetic probe passed', result.stdout)

    def test_unexpected_network_peer_refused_before_probe(self):
        result, calls = self.run_harness('foreign_peer')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Candidate IP, subnet, or network peers refused', result.stderr)
        self.assertNotIn('synthetic probe passed', result.stdout)

    def test_unsafe_candidate_ip_refused_before_probe(self):
        result, calls = self.run_harness('bad_ip')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Candidate IP, subnet, or network peers refused', result.stderr)
        self.assertNotIn('synthetic probe passed', result.stdout)

    def test_metadata_timeout_is_unknown_and_skips_cleanup(self):
        result, calls = self.run_harness('metadata_timeout')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Automatic cleanup refused after an uncertain Docker outcome', result.stderr)
        self.assertNotIn(['rm', '--force', APP_ID], calls)
        self.assertNotIn(['rm', '--force', DB_ID], calls)
        self.assertNotIn(['network', 'rm', NETWORK_ID], calls)

    def test_optimized_python_still_refuses_unsafe_network_and_port_states(self):
        valid, _ = self.run_harness(optimized=True)
        self.assertEqual(valid.returncode, 0, valid.stderr)
        for scenario, message in [('public_binding', 'unexpectedly publishes host ports'),
                                  ('publish_all', 'unexpectedly publishes host ports'),
                                  ('network_port_map', 'unexpectedly publishes host ports'),
                                  ('foreign_peer', 'Candidate IP, subnet, or network peers refused'),
                                  ('bad_ip', 'Candidate IP, subnet, or network peers refused')]:
            with self.subTest(scenario=scenario):
                result, _ = self.run_harness(scenario, optimized=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
                self.assertNotIn('synthetic probe passed', result.stdout)

    def test_optimized_health_check_refuses_non_200(self):
        source = (ROOT/'run.sh').read_text()
        match = re.search(r"python3 -c '([^']*http\.client[^']*)'", source)
        self.assertIsNotNone(match)
        program = match.group(1)
        shim = '''import http.client, os
class FakeResponse:
    status = 503
    def read(self): return b''
class FakeConnection:
    def __init__(self, *args, **kwargs): pass
    def request(self, *args, **kwargs): pass
    def getresponse(self): return FakeResponse()
http.client.HTTPConnection = FakeConnection
os.environ['TRANSPORT_IP'] = '172.29.0.3'
'''
        result = subprocess.run([sys.executable, '-O', '-c', shim + program],
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 1, result.stderr)

    def test_uncertain_removal_never_retries_or_removes_other_targets(self):
        result, calls = self.run_harness('uncertain_remove')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Cleanup outcome uncertain', result.stderr)
        self.assertEqual(calls.count(['rm', '--force', APP_ID]), 1)
        self.assertNotIn(['rm', '--force', DB_ID], calls)
        self.assertNotIn(['network', 'rm', NETWORK_ID], calls)

    def test_wrong_candidate_refused_before_docker(self):
        result, calls = self.run_harness(candidate='sha256:' + '1' * 64)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(calls, [])


if __name__ == '__main__':
    unittest.main()
