"""The CI canary uses the real rendered mounts and never starts application code."""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_colab_stage_create as m


class CanaryTests(unittest.TestCase):
    def test_only_create_inspect_owned_cleanup_with_empty_auth(self):
        calls = []
        project = None
        image = json.loads((m.ROOT / 'digitalocean/services/colab-mcp/transport-stage-plan.json').read_text())['image_reference']
        ids = ['a' * 64, 'b' * 64, 'c' * 64]
        def command(args, **kwargs):
            nonlocal project
            calls.append(args)
            self.assertEqual(set(kwargs['env']), {'PATH', 'HOME', 'DOCKER_CONFIG', 'COLAB_MCP_IMAGE'})
            if 'config' in args:
                project = args[args.index('--project-name') + 1]
                return json.dumps({'services': {role: {'image': image, 'tmpfs': ['/tmp:size=32m,mode=1777'],
                    'volumes': [{'type': 'bind', 'source': '/srv/open-hax/state/colab-transport-stage',
                                 'target': '/run/colab-transport-stage'}]}
                    for role in ('bridge-staging', 'compute-staging', 'gateway-staging')}}).encode()
            if 'create' in args:
                model = json.loads(Path(args[args.index('--file') + 1]).read_text())
                for service in model['services'].values():
                    self.assertNotIn('/srv/', service['volumes'][0]['source'])
                return b''
            if 'ps' in args:
                if args[:2] == ['docker', 'ps']:
                    self.assertIn('--no-trunc', args)
                    self.assertEqual(args[-1], 'label=com.docker.compose.project=' + project)
                return b'' if any('rm' in c for c in calls) else ('\n'.join(ids) + '\n').encode()
            if args[:2] == ['docker', 'inspect']:
                return json.dumps([{'Id': identifier, 'State': {'Running': False, 'Status': 'created'},
                    'Config': {'Image': image, 'Labels': {'com.docker.compose.project': project}},
                    'HostConfig': {'Tmpfs': {'/tmp': 'size=32m,mode=1777'}}} for identifier in ids]).encode()
            return b''
        result = m.check(command)
        self.assertTrue(result['passed'])
        self.assertTrue(result['full_container_ids_verified'])
        self.assertFalse(result['application_started'])
        self.assertTrue(any('create' in c for c in calls))
        self.assertFalse(any(set(c) & {'start', 'up', 'build', 'push', 'login'} for c in calls))


if __name__ == '__main__':
    unittest.main()
