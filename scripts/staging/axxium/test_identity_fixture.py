"""Local identity-fixture contract checks, with no Docker access."""
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

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
            with patch.object(identity_fixture, 'validate_root', return_value=(state, controller)), \
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


if __name__ == '__main__':
    unittest.main()
