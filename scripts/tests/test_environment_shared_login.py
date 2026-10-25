"""Source/controller contracts and synthetic private files; no runtime or credentials."""
import importlib.util
import json
import os
import subprocess
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import yaml

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import service_environment_config as c
import environment_policy as policy
spec = importlib.util.spec_from_file_location('receiver_shared', SCRIPTS/'receive-service-environment.py')
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)
ROOT = SCRIPTS.parent


class SharedLoginTests(unittest.TestCase):
 def test_real_compose_relay_overlays_emit_complete_separate_runtime_roles(self):
  templates=ROOT/'digitalocean/environments'
  values={'PATH':os.environ['PATH'],'PROXX_IMAGE':'synthetic-proxx:fixture',
          'PROXX_LISTEN_PORT':'18891','PROXX_PUBLIC_HOST':'testing.proxx.promethean.rest',
          'PROXY_AUTH_TOKEN':'synthetic-token','PROXY_TOKEN_PEPPER':'synthetic-pepper',
          'PROXX_SESSION_SECRET':'synthetic-session','PROXX_POSTGRES_PASSWORD':'synthetic-password'}
  def rendered(extra=None, role=None):
   command=['docker','compose','--project-name','synthetic-relay-roles','-f',str(templates/'proxx.compose.yaml')]
   if role:command+=['-f',str(templates/f'proxx-relay-{role}.compose.yaml')]
   result=subprocess.run(command+['config','--format','json'],env={**values,**(extra or {})},
                         text=True,capture_output=True,timeout=15)
   self.assertEqual(0,result.returncode,result.stderr)
   return json.loads(result.stdout)['services']['proxx']['environment']
  self.assertIsNone(c.validate_relay(rendered()))
  broker={'AXXIUM_RELAY_ISSUER':c.ISSUER,'AXXIUM_RELAY_AUDIENCE':'https://proxx.promethean.rest',
          'AXXIUM_RELAY_NODES':'[{"node_id":"synthetic-node","subject":"synthetic-owner"}]'}
  node={'AXXIUM_RELAY_NODE_ISSUER':c.ISSUER,'AXXIUM_RELAY_NODE_AUDIENCE':'https://proxx.promethean.rest',
        'AXXIUM_RELAY_NODE_ID':'synthetic-node','AXXIUM_RELAY_NODE_SECRET':'x'*32,
        'AXXIUM_RELAY_NODE_SUBJECT':'synthetic-owner'}
  for role,extra in [('broker',broker),('node',node)]:
   with self.subTest(role=role):
    runtime=rendered(extra,role)
    self.assertEqual(role,c.validate_relay(runtime))
    opposite=node if role=='broker' else broker
    self.assertTrue(all(not runtime.get(key) for key in opposite))

 def test_exact_roster_and_revision_tags(self):
  self.assertEqual(('knoxx', 'axxium', 'proxx'), c.SERVICES)
  for service in c.SERVICES:
   self.assertEqual(len(r.image_names(service)), len(r.expected_tags(service, 'a'*40)))
   self.assertTrue(all(tag.endswith(':'+'a'*40) for tag in r.expected_tags(service, 'a'*40)))
  for service in ('unknown', '../proxx', 'website'):
   with self.assertRaises(ValueError): r.expected_tags(service, 'a'*40)
   with self.assertRaises(ValueError): policy.hostname('stealth', service)
  with self.assertRaises(ValueError): r.expected_tags('proxx', 'latest')
  with self.assertRaises(ValueError): policy.hostname('arbitrary', 'proxx')

 def test_missing_proxx_slots_are_optional_only_before_receiver_owns_proxx(self):
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory)
   for phase in ('testing','staging'):
    for service in ('knoxx','axxium'):(root/phase/service).mkdir(parents=True)
   tag='promethean-proxx:'+('a'*40)
   catalog={'version':1,'owned_tags':{tag:'sha256:'+'a'*64},'refused_tags':{}}
   (root/'receiver-images.json').write_text(json.dumps(catalog))
   with patch.object(r,'ROOT',root),patch.object(r,'docker_output') as docker:
    with self.assertRaisesRegex(r.RetentionBlocked,'Owned Proxx slot'):r.reference_inventory()
    docker.assert_not_called()

 def test_disabled_consumers_remain_valid(self):
  for service in c.SERVICES:
   self.assertFalse(c.validate(service, {}))
  self.assertFalse(c.validate('knoxx', {'KNOXX_AXXIUM_OIDC_ENABLED':'false'}))
  self.assertFalse(c.validate('proxx', {'AXXIUM_OIDC_ISSUER':c.ISSUER, 'AXXIUM_OIDC_ACCOUNT_LINKS':'[]'}))

 def test_partial_consumers_and_relay_activation_refused(self):
  for values in ({'AXXIUM_OIDC_CLIENT_ID':'fixture'}, {'AXXIUM_OIDC_CLIENT_SECRET':'fixture'},
                 {'AXXIUM_OIDC_ACCOUNT_LINKS':'[{}]'}, {'AXXIUM_RELAY_ISSUER':c.ISSUER}):
   with self.assertRaises(ValueError): c.validate('proxx', values)
  for values in ({'KNOXX_AXXIUM_OIDC_ENABLED':'yes'}, {'KNOXX_AXXIUM_OIDC_ENABLED':'true'},
                 {'KNOXX_AXXIUM_OIDC_CLIENT_SECRET':'fixture'}):
   with self.assertRaises(ValueError): c.validate('knoxx', values)

 def test_exact_shared_issuer_and_consumer_origin(self):
  values = {'AXXIUM_OIDC_ISSUER': c.ISSUER, 'AXXIUM_OIDC_PUBLIC_ORIGIN':'https://stealth.proxx.promethean.rest',
            'AXXIUM_OIDC_CLIENT_ID':'fixture', 'AXXIUM_OIDC_CLIENT_SECRET':'fixture', 'AXXIUM_OIDC_ACCOUNT_LINKS':'[]'}
  self.assertFalse(c.validate('proxx', values))
  for key, bad in [('AXXIUM_OIDC_ISSUER','https://yoga.axxium.promethean.rest/oidc'),
                   ('AXXIUM_OIDC_PUBLIC_ORIGIN','https://host.invalid/path'),
                   ('AXXIUM_OIDC_PUBLIC_ORIGIN','http://host.invalid'),
                   ('AXXIUM_OIDC_PUBLIC_ORIGIN','https://unknown.proxx.promethean.rest'),
                   ('AXXIUM_OIDC_PUBLIC_ORIGIN','https://host.invalid?x'),
                   ('AXXIUM_OIDC_ACCOUNT_LINKS','{}')]:
   with self.assertRaises(ValueError): c.validate('proxx', {**values,key:bad})

 def test_authority_path_and_actual_proxy_hops_required(self):
  values = {'AXXIUM_OIDC_CONFIG_FILE':c.AUTHORITY_FILE, 'AXXIUM_PUBLIC_BASE_URL':'https://axxium.promethean.rest',
            'AXXIUM_TRUST_PROXY':'172.31.255.2/32,127.0.0.1'}
  self.assertTrue(c.validate('axxium',values))
  for proxy in ('true','1','0.0.0.0/0','::/0','loopback','172.31.255.2,',''):
   with self.assertRaises(ValueError): c.validate('axxium',{**values,'AXXIUM_TRUST_PROXY':proxy})
  for key, value in [('AXXIUM_OIDC_CONFIG_FILE','/tmp/key.json'),
                     ('AXXIUM_PUBLIC_BASE_URL','https://stealth.axxium.promethean.rest')]:
   with self.assertRaises(ValueError): c.validate('axxium',{**values,key:value})

 def test_scoped_relay_roles_are_complete_separate_and_disabled_by_default(self):
  self.assertIsNone(c.validate_relay({}))
  broker={'PROXX_SCOPED_RELAY_ROLE':'broker','AXXIUM_RELAY_ISSUER':c.ISSUER,
          'AXXIUM_RELAY_AUDIENCE':'https://proxx.promethean.rest',
          'AXXIUM_RELAY_NODES':'[{"nodeId":"synthetic-node","subject":"synthetic-verified-subject"}]'}
  self.assertEqual('broker',c.validate_relay(broker))
  for key in broker:
   with self.subTest(key=key):
    with self.assertRaises(ValueError):c.validate_relay({**broker,key:''})
  for nodes in ('[]','{}'):
   with self.assertRaises(ValueError):c.validate_relay({**broker,'AXXIUM_RELAY_NODES':nodes})
  node={'PROXX_SCOPED_RELAY_ROLE':'node','AXXIUM_RELAY_NODE_ISSUER':c.ISSUER,
        'AXXIUM_RELAY_NODE_AUDIENCE':'https://proxx.promethean.rest',
        'AXXIUM_RELAY_NODE_ID':'synthetic-node','AXXIUM_RELAY_NODE_SECRET':'x'*32,
        'AXXIUM_RELAY_NODE_SUBJECT':'synthetic-verified-subject',
        'FEDERATION_BRIDGE_RELAY_URL':'wss://proxx.promethean.rest/api/ui/federation/bridge/ws'}
  self.assertEqual('node',c.validate_relay(node))
  for key,bad in [('AXXIUM_RELAY_NODE_SECRET','short'),('AXXIUM_RELAY_NODE_ISSUER','https://other/oidc'),
                  ('AXXIUM_RELAY_NODE_AUDIENCE','https://other'),('FEDERATION_BRIDGE_RELAY_URL','wss://proxx.promethean.rest/admin')]:
   with self.assertRaises(ValueError):c.validate_relay({**node,key:bad})
  with self.assertRaises(ValueError):c.validate_relay({**node,'AXXIUM_RELAY_ISSUER':c.ISSUER})
  base=yaml.safe_load((ROOT/'digitalocean/environments/proxx.compose.yaml').read_text())
  self.assertFalse(any(key.startswith('AXXIUM_RELAY_') for key in base['services']['proxx']['environment']))

 def test_private_file_metadata_and_missing_mount_are_fail_closed(self):
  with tempfile.TemporaryDirectory() as directory:
   path = Path(directory)/'private.json'
   path.write_text('{}');path.chmod(0o600)
   c.private_file(path,os.getuid())
   for mode in (0o644,0o400,0o1600):
    path.chmod(mode)
    with self.assertRaises(ValueError): c.private_file(path,os.getuid())
   path.chmod(0o600)
   with self.assertRaises(ValueError): c.private_file(path,os.getuid()+1)
   link=path.with_name('link');link.symlink_to(path)
   with self.assertRaises(ValueError): c.private_file(link,os.getuid())
   for content in ('', 'x'*1048577):
    path.write_text(content)
    with self.assertRaises(ValueError): c.private_file(path,os.getuid())
   path.unlink()
   with self.assertRaises(ValueError): c.private_file(path,os.getuid())
   with self.assertRaises(ValueError): c.private_file(Path(directory),os.getuid())

 def test_optional_mount_cannot_create_host_path(self):
  overlay=yaml.safe_load((ROOT/'digitalocean/environments/axxium-oidc.compose.yaml').read_text())
  mount=overlay['services']['axxium']['volumes'][0]
  self.assertEqual({'type':'bind','source':'./oidc','target':'/run/axxium/oidc',
                    'read_only':True,'bind':{'create_host_path':False}},mount)
  base=yaml.safe_load((ROOT/'digitalocean/environments/axxium.compose.yaml').read_text())
  self.assertNotIn('AXXIUM_OIDC_CONFIG_FILE',base['services']['axxium']['environment'])
  self.assertIn('/health',str(base['services']['axxium']['healthcheck']))
  self.assertNotIn('https',str(base['services']['axxium']['healthcheck']))

 def test_proxx_topology_is_fixed_and_slots_are_declared(self):
  model=yaml.safe_load((ROOT/'digitalocean/environments/proxx.compose.yaml').read_text())
  self.assertEqual({'proxx','database'},set(model['services']))
  self.assertIn('PROXX_LISTEN_PORT',str(model['services']['proxx']['ports']))
  self.assertNotIn('networks',model)
  for host,port in [('testing',18891),('staging',18892),('stealth',8789),('yoga',8789)]:
   self.assertEqual(str(port),c.slot_values(host,'proxx')['PROXX_LISTEN_PORT'])
  with self.assertRaises(ValueError):c.slot_values('unknown','proxx')

 def test_receiver_slot_names_and_ports_are_fixed(self):
  for phase,index in [('testing',0),('staging',1)]:
   self.assertEqual(str(18871+index),c.slot_values(phase,'axxium')['AXXIUM_LISTEN_PORT'])
   self.assertEqual(str(18881+index),c.slot_values(phase,'knoxx')['KNOXX_LISTEN_PORT'])
  for phase,service in [('unknown','knoxx'),('stealth','axxium'),('testing','unknown')]:
   with self.assertRaises(ValueError):c.slot_values(phase,service)

 def test_preflight_rejects_drift_and_redacts_private_parser_errors(self):
  with tempfile.TemporaryDirectory() as directory:
   slot=Path(directory)/'testing'/'knoxx';slot.mkdir(parents=True)
   env=slot/'.env';env.write_text('# synthetic');env.chmod(0o600)
   valid={**{key:'' for key in c.KEYS['knoxx']},**c.slot_values('testing','knoxx')}
   for key in ('KNOXX_PUBLIC_BASE_URL','KNOXX_LISTEN_PORT','KNOXX_AXXIUM_ORIGIN'):
    result=type('Result',(),{'stdout':json.dumps({'services':{'preflight':{'environment':{**valid,key:'wrong'}}}})})()
    with patch.object(c.subprocess,'run',return_value=result):
     with self.assertRaisesRegex(ValueError,'preflight refused'):c.preflight(slot,'knoxx')
   result=type('Result',(),{'stdout':json.dumps({'services':{'preflight':{'environment':valid}}})})()
   with patch.object(c.subprocess,'run',return_value=result) as run:
    self.assertEqual([],c.preflight(slot,'knoxx'))
    self.assertEqual({'PATH'},set(run.call_args.kwargs['env']))
    self.assertEqual(10,run.call_args.kwargs['timeout'])

 def test_receiver_forced_command_cannot_change_service_slot_or_revision(self):
  commands = ['receive testing proxx '+'a'*40, 'receive staging axxium '+'a'*40,
              'receive testing axxium latest', 'receive testing axxium '+'a'*40+'; id',
              'bash', 'receive testing axxium '+'a'*40+' extra']
  for command in commands:
   with patch.object(sys, 'argv', ['receive.py','testing','axxium']), patch.dict(os.environ, {'SSH_ORIGINAL_COMMAND':command}):
    with self.assertRaisesRegex(ValueError,'fixed deployment slot'): r.main()
  for service in ('../axxium','unknown'):
   with patch.object(sys,'argv',['receive.py','testing',service]):
    with self.assertRaisesRegex(ValueError,'Unsupported slot'):r.main()

 def test_receiver_preflight_precedes_image_intake(self):
  text=(SCRIPTS/'receive-service-environment.py').read_text()
  main=text[text.index('def main():'):]
  self.assertLess(main.index('preflight('),main.index('validate_archive('))
  self.assertIn("for overlay in overlays:compose+=['-f',overlay]",main)


if __name__ == '__main__': unittest.main()
