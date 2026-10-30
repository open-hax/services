"""Tests for isolated image metadata rewriting; no Docker daemon is used."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('derive_fixture_image_test', HERE / 'derive_fixture_image.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class DeriveTests(unittest.TestCase):
    def fixture(self, root, corrupt=False):
        manifest_data, config_data, layer_data = b'manifest', b'config', b'layer'
        manifest = hashlib.sha256(manifest_data).hexdigest()
        config = hashlib.sha256(config_data).hexdigest()
        layer = hashlib.sha256(layer_data).hexdigest()
        index = {'manifests': [{'digest': 'sha256:' + manifest, 'annotations': {
            'io.containerd.image.name': 'docker.io/library/' + module.ORIGINAL_TAG,
            'org.opencontainers.image.ref.name': module.SOURCE}}]}
        docker = [{'Config': 'blobs/sha256/' + config, 'RepoTags': [module.ORIGINAL_TAG],
                   'Layers': ['blobs/sha256/' + layer]}]
        repositories = {'promethean-axxium': {module.SOURCE: layer}}
        files = {'index.json': json.dumps(index).encode(), 'manifest.json': json.dumps(docker).encode(),
                 'repositories': json.dumps(repositories).encode(), 'oci-layout': b'{}',
                 'blobs/sha256/' + manifest: manifest_data, 'blobs/sha256/' + config: config_data,
                 'blobs/sha256/' + layer: b'bad' if corrupt else layer_data}
        inner = io.BytesIO()
        with tarfile.open(fileobj=inner, mode='w') as archive:
            for name, data in files.items():
                info = tarfile.TarInfo(name); info.size = len(data)
                archive.addfile(info, io.BytesIO(data))
        zip_path = root / 'input.zip'
        with zipfile.ZipFile(zip_path, 'w') as archive:
            archive.writestr('images.tar', inner.getvalue())
        plan_raw = json.dumps({'artifact_sha256': hashlib.sha256(zip_path.read_bytes()).hexdigest()}).encode()
        plan_path = root / 'plan.json'; plan_path.write_bytes(plan_raw)
        return plan_path, zip_path, manifest, config, files

    def call(self, root, corrupt=False, run_id='a' * 32):
        plan, archive, manifest, config, files = self.fixture(root, corrupt=corrupt)
        output = root / 'output.tar'
        with patch.object(module, 'MANIFEST', manifest), patch.object(module, 'CONFIG', config), \
             patch.object(module.qualification, 'WAIVER_PLAN_SHA256', hashlib.sha256(plan.read_bytes()).hexdigest()), \
             patch.object(module.qualification, 'validate_plan') as validate, \
             patch.object(module.qualification, 'verify_archive') as verified:
            receipt = module.derive(plan, archive, output, run_id)
        validate.assert_called_once()
        verified.assert_called_once()
        return receipt, output, files

    def test_rewrites_only_references_and_preserves_blobs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            receipt, output, original = self.call(root)
            self.assertEqual(receipt['tag'], 'axxium-fixture-' + 'a' * 32 + ':fixture-' + 'a' * 32)
            with tarfile.open(output, 'r:') as archive:
                for name, data in original.items():
                    actual = archive.extractfile(name).read()
                    if name not in module.CHANGED:
                        self.assertEqual(actual, data)
                self.assertEqual(json.load(archive.extractfile('manifest.json'))[0]['RepoTags'], [receipt['tag']])
                index = json.load(archive.extractfile('index.json'))
                self.assertEqual(index['manifests'][0]['annotations']['org.opencontainers.image.ref.name'], 'fixture-' + 'a' * 32)
                annotations = index['manifests'][0]['annotations']
                self.assertEqual(annotations['io.containerd.image.name'], 'docker.io/library/' + receipt['tag'])
                self.assertEqual(json.load(archive.extractfile('repositories')),
                                 {'axxium-fixture-' + 'a' * 32: {'fixture-' + 'a' * 32:
                                  hashlib.sha256(b'layer').hexdigest()}})

    def test_corrupt_blob_refuses_and_removes_partial(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaises(module.Refused):
                self.call(root, corrupt=True)
            self.assertFalse((root / 'output.tar').exists())

    def test_unexpected_run_id_and_existing_output_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaises(module.Refused):
                self.call(root, run_id='bad')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan, archive, manifest, config, _ = self.fixture(root)
            output = root / 'output.tar'; output.write_bytes(b'existing')
            with patch.object(module, 'MANIFEST', manifest), patch.object(module, 'CONFIG', config), \
                 patch.object(module.qualification, 'WAIVER_PLAN_SHA256', hashlib.sha256(plan.read_bytes()).hexdigest()), \
                 patch.object(module.qualification, 'validate_plan'), patch.object(module.qualification, 'verify_archive'):
                with self.assertRaises(FileExistsError):
                    module.derive(plan, archive, output, 'a' * 32)
            self.assertEqual(output.read_bytes(), b'existing')


if __name__ == '__main__':
    unittest.main()
