import hashlib
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from axxium_oidc_ingress import IngressRefused, NEW, OLD, SITE, UPSTREAM, MAINTENANCE, prepare, maintenance


class ExistingIngressTests(unittest.TestCase):
    def setUp(self):
        self.raw=(b'# unrelated incumbent bytes\nother.example { respond "ok" }\n'
                  +SITE+b'\timport common\n'+OLD+
                  b'\thandle @axxium_api { reverse_proxy axxium:8787 }\n'
                  b'\thandle { reverse_proxy axxium-pds:3000 }\n}\n'
                  b'import /etc/caddy/managed/*.caddy\n')

    def apply(self, raw):
        return prepare(raw,hashlib.sha256(raw).hexdigest())

    def test_only_one_existing_matcher_line_changes(self):
        candidate=self.apply(self.raw)
        self.assertEqual(candidate.replace(NEW,OLD,1),self.raw)
        before,after=self.raw.splitlines(),candidate.splitlines()
        self.assertEqual(sum(a!=b for a,b in zip(before,after)),1)
        self.assertEqual(len(before),len(after))
        self.assertIn(b'reverse_proxy axxium-pds:3000',candidate)
        self.assertTrue(candidate.endswith(b'import /etc/caddy/managed/*.caddy\n'))

    def test_changed_or_unbound_bytes_refuse(self):
        for expected in ('0'*64,'missing',None):
            with self.assertRaises(IngressRefused):prepare(self.raw,expected)
        with self.assertRaises(IngressRefused):
            prepare(self.raw+b'# drift\n',hashlib.sha256(self.raw).hexdigest())

    def test_missing_duplicate_changed_and_already_selected_matchers_refuse(self):
        for raw in (self.raw.replace(OLD,b''),self.raw+OLD,
                    self.raw.replace(OLD,OLD.replace(b'/api/auth/*',b'/api/changed/*')),
                    self.raw.replace(OLD,NEW),self.raw+SITE):
            with self.subTest(raw=raw):
                with self.assertRaises(IngressRefused):self.apply(raw)

    def test_invalid_or_unbounded_input_refuses_without_echoing_source(self):
        for raw in (None,'source text',b'x'*(2*1024*1024+1)):
            with self.assertRaisesRegex(IngressRefused,'reviewed plan'):
                prepare(raw,'0'*64)

    def test_maintenance_fences_only_authority_api_and_leaves_pds(self):
        raw=self.raw.replace(b'\thandle @axxium_api { reverse_proxy axxium:8787 }\n',UPSTREAM)
        fenced=maintenance(raw,hashlib.sha256(raw).hexdigest())
        self.assertEqual(fenced.replace(MAINTENANCE,UPSTREAM,1),raw)
        self.assertIn(b'reverse_proxy axxium-pds:3000',fenced)
        self.assertNotIn(NEW,fenced)
        with self.assertRaises(IngressRefused):maintenance(self.raw,hashlib.sha256(self.raw).hexdigest())


if __name__=='__main__':unittest.main()
