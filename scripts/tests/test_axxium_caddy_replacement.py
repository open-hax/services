"""Exercise the generic Caddy preflight without a Docker daemon."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


GUARD = Path(__file__).resolve().parents[2] / "digitalocean/services/caddy/check-replacement.sh"


class AxxiumCaddyReplacementTest(unittest.TestCase):
    def test_only_unoccupied_ingress_can_be_replaced(self):
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            docker = directory / "docker"
            docker.write_text(
                '#!/bin/bash\n'
                '[[ "$*" == "container ls --all --filter label=com.docker.compose.project=axxium '
                '--format {{.Names}}" ]] || exit 2\n'
                '[[ "$DOCKER_RESULT" != error ]] || exit 3\n'
                'if [[ "$DOCKER_RESULT" == incumbent ]]; then printf "axxium-pds-1\\n"; fi\n'
            )
            docker.chmod(0o700)
            runtime = directory / "caddy"
            runtime.mkdir()
            asset = runtime / "axxium-did.json"
            for scenario, docker_result, has_asset, expected in (
                ("fresh host", "empty", False, 0),
                ("retained Axxium project", "incumbent", False, 1),
                ("recovery with a dangling DID mount", "empty", True, 1),
                ("cannot read Docker inventory", "error", False, 3),
            ):
                with self.subTest(scenario=scenario):
                    if has_asset:
                        asset.symlink_to(runtime / "missing.json")
                    result = subprocess.run(
                        ["bash", str(GUARD), str(runtime)],
                        env={**os.environ, "PATH": f"{temp}:{os.defpath}", "DOCKER_RESULT": docker_result},
                        capture_output=True, text=True, timeout=5,
                    )
                    self.assertEqual(expected, result.returncode, result.stderr)
                    asset.unlink(missing_ok=True)
