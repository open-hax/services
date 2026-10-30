"""Local identity-fixture contract checks, with no Docker access."""
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import identity_fixture
import provision_axxium_colab_identity as provisioner


class IdentityFixtureTests(unittest.TestCase):
    def test_synthetic_binding_and_private_pair(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state = root / 'state'
            state.mkdir(mode=0o700)
            controller = root / 'controller'
            (controller / 'scripts').mkdir(parents=True)
            source = Path(provisioner.__file__)
            copied = controller / 'scripts' / source.name
            shutil.copyfile(source, copied)
            fields = ('kty', 'n', 'e', 'd', 'p', 'q', 'dp', 'dq', 'qi')
            key = {name: 'test' for name in fields}
            key['kty'] = 'RSA'
            actor = 'actor_00000000-0000-4000-8000-000000000001'
            original_lstat = Path.lstat
            def fixture_lstat(path):
                info = original_lstat(path)
                if path in (copied, state / 'axxium' / 'oidc', state / 'colab-mcp'):
                    return SimpleNamespace(st_mode=info.st_mode, st_uid=1000, st_nlink=info.st_nlink)
                return info
            with patch.object(identity_fixture, 'validate_root', return_value=(state, controller)), \
                 patch.object(Path, 'lstat', fixture_lstat), \
                 patch.object(identity_fixture, 'command', return_value=json.dumps(key).encode()):
                result = identity_fixture.create('a' * 64, state, controller, actor_id=actor)
            self.assertEqual(result, actor)
            oidc = state / 'axxium' / 'oidc'
            binding = json.loads((oidc / 'production-binding.json').read_bytes())
            authority = json.loads((oidc / 'authority.json').read_bytes())
            gateway = json.loads((state / 'colab-mcp' / 'axxium-client.json').read_bytes())
            with patch.object(provisioner, 'APPROVED_ACTOR', actor):
                provisioner.validate_binding(binding)
                provisioner.validate_pair(authority, gateway, binding, crypto=lambda *args: None)
            self.assertIn("APPROVED_ACTOR = '" + actor + "'", copied.read_text())
            self.assertEqual((oidc / 'authority.json').stat().st_mode & 0o777, 0o600)
            self.assertEqual((controller / 'production-binding.json').read_bytes(),
                             (oidc / 'production-binding.json').read_bytes())

    def test_provisioner_with_wrong_owner_is_refused(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            state = root / 'state'
            state.mkdir(mode=0o700)
            controller = root / 'controller'
            (controller / 'scripts').mkdir(parents=True)
            copied = controller / 'scripts' / 'provision_axxium_colab_identity.py'
            shutil.copyfile(provisioner.__file__, copied)
            original_lstat = Path.lstat
            def wrong_owner(path):
                info = original_lstat(path)
                if path == copied:
                    return SimpleNamespace(st_mode=info.st_mode, st_uid=1001, st_nlink=info.st_nlink)
                return info
            with patch.object(identity_fixture, 'validate_root', return_value=(state, controller)), \
                 patch.object(Path, 'lstat', wrong_owner), \
                 patch.object(identity_fixture, 'command') as command:
                with self.assertRaises(identity_fixture.FixtureRefused):
                    identity_fixture.create('a' * 64, state, controller, actor_id='actor_00000000-0000-4000-8000-000000000001')
                command.assert_not_called()


if __name__ == '__main__':
    unittest.main()
