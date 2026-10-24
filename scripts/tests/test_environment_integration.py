"""Candidate source laws and real filesystem/Compose projections; no live runtime."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
import yaml

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import environment_policy as policy
import github_environment_admission as github
import preserve_service_login as preserve
import protected_receiver as protected
import service_environment_config as config
import service_slots as slots
from test_environment_policy import batches, SHA

NOW=datetime.now(timezone.utc)
CONTROLLER='b'*40
IMAGE='c'*64
ROOT=Path(__file__).resolve().parents[2]


class SlotTests(unittest.TestCase):
 def test_every_role_and_target_is_explicit(self):
  law=slots.load_slots()
  self.assertEqual({'testing':(18891,19091),'staging':(18892,19092),'stealth':(19789,19089),'yoga':(19790,19090)},
      {env:(row['droplet_target'],row['caddy_port']) for env,row in law.items() if env!='production'})
  self.assertEqual('proxx.promethean.rest',policy.hostname('production','proxx'))
  for env in ('unknown','big-ussy','../yoga'):
   with self.assertRaises(ValueError):slots.proxx_slot(env)
  for env,row in law.items():
   self.assertEqual(row['hostname'],config.slot_values(env,'proxx')['PROXX_PUBLIC_HOST'])
   self.assertEqual(str(row['host_port']),config.slot_values(env,'proxx')['PROXX_LISTEN_PORT'])

 def test_unknown_roles_hosts_missing_or_colliding_ports_refuse(self):
  source=json.loads(slots.LAW.read_text())
  variants=[]
  for key,value in [('host','unknown'),('role','inferred'),('caddy_port',19082),('droplet_target',18881),('hostname','big-ussy.proxx.promethean.rest'),('host_port',True)]:
   item=copy.deepcopy(source);item['proxx']['testing'][key]=value;variants.append(item)
  item=copy.deepcopy(source);item['proxx'].pop('yoga');variants.append(item)
  item=copy.deepcopy(source);item['proxx']['staging']['caddy_port']=19091;variants.append(item)
  with tempfile.TemporaryDirectory() as d:
   path=Path(d)/'law.json'
   for value in variants:
    path.write_text(json.dumps(value))
    with self.assertRaises(ValueError):slots.load_slots(path)

 def test_existing_stack_has_no_database_creation_or_inferred_bind(self):
  model=yaml.safe_load((ROOT/'digitalocean/environments/proxx-existing.compose.yaml').read_text())
  self.assertEqual({'proxx'},set(model['services']))
  self.assertNotIn('volumes',model)
  app=model['services']['proxx']
  self.assertEqual('1000:1000',app['user'])
  self.assertIn('PROXX_EXISTING_BIND_ADDRESS',str(app['ports']))
  self.assertIn('PROXX_DATABASE_URL',app['environment']['DATABASE_URL'])
  self.assertFalse(app['volumes'][0]['bind']['create_host_path'])


class PreparerLawTests(unittest.TestCase):
 def test_colliding_source_declaration_refuses_before_slot_creation(self):
  import subprocess
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);script=root/'prepare-service-environments.mjs'
   script.write_text((ROOT/'scripts/prepare-service-environments.mjs').read_text())
   law=json.loads(slots.LAW.read_text());law['proxx']['testing']['caddy_port']=19082
   (root/'service_slots.json').write_text(json.dumps(law))
   result=subprocess.run(['node',str(script),str(root/'slots'),str(ROOT/'digitalocean/environments')],
    env={'PATH':os.environ['PATH']},capture_output=True,text=True,timeout=15)
   self.assertNotEqual(0,result.returncode)
   self.assertIn('Colliding ingress port',result.stderr)
   self.assertFalse((root/'slots').exists())


class IngressProjectionTests(unittest.TestCase):
 def test_installer_uses_declared_loopback_forward_and_caddy_allowlist(self):
  import runpy
  with tempfile.TemporaryDirectory() as d:
   root=Path(d)
   def isolated(path):
    return root if path=='/etc/systemd/system' else Path(path)
   with patch('pathlib.Path',side_effect=isolated),patch('subprocess.run') as calls,patch('builtins.print'):
    runpy.run_path(str(ROOT/'scripts/install-environment-ingress.py'))
   for env,row in slots.load_slots().items():
    if env=='production':continue
    unit='promethean-'+env+'-proxx'
    sock=(root/(unit+'.socket')).read_text()
    service=(root/(unit+'.service')).read_text()
    self.assertIn('ListenStream=172.31.255.1:'+str(row['caddy_port']),sock)
    self.assertIn('FreeBind=true',sock)
    self.assertIn('IPAddressDeny=any',sock)
    self.assertIn('IPAddressAllow=172.31.255.2/32',sock)
    self.assertIn('127.0.0.1:'+str(row['droplet_target']),service)
    self.assertIn('IPAddressAllow=127.0.0.1/32',service)
   self.assertEqual(10,len(calls.call_args_list))


class SlotConsumerTests(unittest.TestCase):
 def test_valid_consumer_for_another_slot_is_refused_before_intake(self):
  from types import SimpleNamespace
  with tempfile.TemporaryDirectory() as d:
   slot=Path(d)/'testing'/'proxx';slot.mkdir(parents=True)
   (slot/'.env').write_text('synthetic');(slot/'.env').chmod(0o600)
   values={**config.slot_values('testing','proxx'),'AXXIUM_OIDC_ISSUER':config.ISSUER,
    'AXXIUM_OIDC_PUBLIC_ORIGIN':'https://staging.proxx.promethean.rest','AXXIUM_OIDC_CLIENT_ID':'synthetic',
    'AXXIUM_OIDC_CLIENT_SECRET':'synthetic','AXXIUM_OIDC_ACCOUNT_LINKS':'[]'}
   result=SimpleNamespace(stdout=json.dumps({'services':{'preflight':{'environment':values}}}))
   with patch.object(config.subprocess,'run',return_value=result):
    with self.assertRaisesRegex(ValueError,'preflight refused'):config.preflight(slot,'proxx')
   values['AXXIUM_OIDC_PUBLIC_ORIGIN']='https://testing.proxx.promethean.rest'
   result.stdout=json.dumps({'services':{'preflight':{'environment':values}}})
   with patch.object(config.subprocess,'run',return_value=result):
    self.assertEqual([],config.preflight(slot,'proxx'))


class AuthorityDirectoryTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.root=Path(self.tmp.name);self.directory=self.root/'oidc';self.directory.mkdir(mode=0o700)
  self.file=self.directory/'authority.json';self.file.write_text('{"enabled":true}');self.file.chmod(0o600)
 def check(self):config.private_authority(self.directory,os.getuid())
 def test_directory_and_file_metadata_and_symlink_traversal(self):
  self.check()
  for mode in (0o755,0o600,0o1700):
   self.directory.chmod(mode)
   with self.assertRaises(ValueError):self.check()
  self.directory.chmod(0o700)
  with self.assertRaises(ValueError):config.private_authority(self.directory,os.getuid()+1)
  for mode in (0o644,0o400,0o1600):
   self.file.chmod(mode)
   with self.assertRaises(ValueError):self.check()
  self.file.chmod(0o600)
  for content in ('','x'*1048577):
   self.file.write_text(content)
   with self.assertRaises(ValueError):self.check()
  self.file.write_text('{}')
  link=self.root/'linked';link.symlink_to(self.directory)
  with self.assertRaises(ValueError):config.private_authority(link,os.getuid())
  parent=self.root/'parent';parent.symlink_to(self.root)
  with self.assertRaises(ValueError):config.private_authority(parent/'oidc',os.getuid())
  self.file.unlink();self.file.symlink_to(self.root/'outside')
  with self.assertRaises(ValueError):self.check()
 def test_unrelated_state_or_missing_file_refuses(self):
  other=self.directory/'google-client.json';other.write_text('synthetic')
  with self.assertRaises(ValueError):self.check()
  other.unlink();self.file.unlink()
  with self.assertRaises(ValueError):self.check()
 def test_atomic_replace_visible_by_directory_descriptor_not_pinned_file(self):
  # Directory fd models the directory bind's namespace. A file fd models a
  # pinned single-file bind inode. This uses real rename/open, not Docker.
  directory=os.open(self.directory,os.O_RDONLY|os.O_DIRECTORY)
  pinned=os.open(self.file,os.O_RDONLY)
  self.addCleanup(os.close,directory);self.addCleanup(os.close,pinned)
  old_inode=os.fstat(pinned).st_ino
  replacement=self.directory/'authority.next';replacement.write_text('{"enabled":false}');replacement.chmod(0o600)
  replacement.replace(self.file)
  fd=os.open('authority.json',os.O_RDONLY|os.O_NOFOLLOW,dir_fd=directory)
  try:
   self.assertNotEqual(old_inode,os.fstat(fd).st_ino)
   self.assertEqual(b'{"enabled":false}',os.read(fd,128))
   self.assertEqual(b'{"enabled":true}',os.read(pinned,128))
  finally:os.close(fd)
  self.check()


class OwnerTests(unittest.TestCase):
 def review(self,env='yoga'):
  return {'id':42,'state':'COMMENTED','commit_id':SHA,'submitted_at':NOW.isoformat(),
   'user':{'id':7,'type':'User','login':'synthetic-owner'},
   'body':f'Authorize {env} deployment of proxx {SHA} with Services {CONTROLLER} image sha256:{IMAGE} on '+('droplet' if env=='production' else env)+'.'}
 def admit(self,review,**changes):
  args=dict(source_sha=SHA,controller_sha=CONTROLLER,image_sha256=IMAGE,environment='yoga',service='proxx',now=NOW)
  return policy.deployment_authorization([review],**{**args,**changes})
 def test_native_exact_archive_owner_intent(self):
  self.assertIsNotNone(self.admit(self.review()))
  for key,value in [('source_sha','d'*40),('controller_sha','d'*40),('image_sha256','d'*64),('environment','stealth'),('service','knoxx')]:
   self.assertIsNone(self.admit(self.review(),**{key:value}))
  for key,value in [('state','APPROVED'),('commit_id','d'*40),('id',True),('body','quoted '+self.review()['body']),('submitted_at',(NOW-timedelta(hours=3)).isoformat()),('submitted_at',(NOW+timedelta(seconds=1)).isoformat())]:
   self.assertIsNone(self.admit({**self.review(),key:value}))
  for user in ({'id':7,'type':'Bot','login':'synthetic-owner'},{'id':0,'type':'User','login':'synthetic-owner'}):
   self.assertIsNone(self.admit({**self.review(),'user':user}))
 def test_all_production_qualification_negative_cases(self):
  proof={'source_sha':SHA,'status':'passed','assertions':1}
  self.assertTrue(policy.production_admission(SHA,proof,proof,batches())['admitted'])
  for key,bad in [('source_sha','d'*40),('status','planned'),('assertions',0),('assertions',True),('assertions','1'),('timed_out',True),('warnings',['warning'])]:
   for side in ('integration','e2e'):
    left={**proof,key:bad} if side=='integration' else proof
    right={**proof,key:bad} if side=='e2e' else proof
    self.assertFalse(policy.production_admission(SHA,left,right,batches())['admitted'])
  for key,bad in [('source_sha','d'*40),('status','skipped'),('baseline','failed'),('batch_index',True),('batch_index',4),('timed_out',True),('warnings',['warning']),('mutants',[])]:
   rows=batches();rows[0][key]=bad
   self.assertFalse(policy.production_admission(SHA,proof,proof,rows)['admitted'])
  for key,bad in [('status','survived'),('status','invalid'),('phase','compile'),('fingerprint',''),('timed_out',True),('warnings',['warning'])]:
   rows=batches();rows[0]['mutants'][0][key]=bad
   self.assertFalse(policy.production_admission(SHA,proof,proof,rows)['admitted'])
  for rows in ([],batches()[:3],batches()+batches()[:1],None):
   self.assertFalse(policy.production_admission(SHA,proof,proof,rows)['admitted'])
  rows=batches();rows[1]['mutants'][0]['fingerprint']=rows[0]['mutants'][0]['fingerprint']
  self.assertFalse(policy.production_admission(SHA,proof,proof,rows)['admitted'])


class PreservationTests(unittest.TestCase):
 def test_native_volume_preparation_preserves_existing_file_binds(self):
  import subprocess
  workflow=(ROOT/'.github/workflows/deploy-digitalocean.yml').read_text()
  start=workflow.index('          sed -nE ',workflow.index('# A non-mount alias'))
  end=workflow.index('\n\n          # BEGIN KNOXX PUBLICATION WORKSPACE',start)
  with tempfile.TemporaryDirectory() as d:
   root=Path(d)
   credential=root/'google-client.json'
   credential.write_text('synthetic private fixture')
   (root/'compose.yaml').write_text('services:\n  app:\n    volumes:\n      - ${AXXIUM_STATE_PATH}/google-client.json:/run/client.json:ro\n      - ${AXXIUM_STATE_PATH}/data:/app/data\n')
   result=subprocess.run(['bash','-eu','-c',workflow[start:end]],cwd=root,
    env={'PATH':os.defpath,'AXXIUM_STATE_PATH':str(root)},capture_output=True,text=True,timeout=5)
   self.assertEqual(0,result.returncode,result.stderr)
   self.assertEqual('synthetic private fixture',credential.read_text())
   self.assertTrue((root/'data').is_dir())

 def test_effective_optional_values_survive_legacy_render_without_secret_output(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);old=root/'.env';new=root/'.env.candidate'
   old.write_text('# synthetic');new.write_text("PROXX_IMAGE='sha256:synthetic'\n")
   old.chmod(0o600);new.chmod(0o600)
   values={'AXXIUM_OIDC_ISSUER':config.ISSUER,'AXXIUM_OIDC_PUBLIC_ORIGIN':'https://proxx.promethean.rest',
           'AXXIUM_OIDC_CLIENT_ID':'synthetic','AXXIUM_OIDC_CLIENT_SECRET':'private-fixture','AXXIUM_OIDC_ACCOUNT_LINKS':'[]'}
   with patch.object(preserve,'projection',side_effect=[values,{}]):
    preserve.preserve('proxx',old,new,root/'state')
   self.assertIn("AXXIUM_OIDC_CLIENT_SECRET='private-fixture'",new.read_text())
   self.assertEqual('# synthetic',old.read_text())
   with self.assertRaises(ValueError):preserve.render('proxx',new,'{"UNKNOWN":"x"}')
   with self.assertRaises(ValueError):preserve.render('proxx',new,'{"AXXIUM_OIDC_CLIENT_SECRET":"partial"}')
 def test_disabled_authority_requires_no_directory_and_partial_activation_refuses(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);candidate=root/'candidate';candidate.write_text("AXXIUM_IMAGE='synthetic'\n");candidate.chmod(0o600)
   with patch.object(preserve,'projection',return_value={}):
    self.assertFalse(preserve.preserve('axxium',root/'absent',candidate,root))
   self.assertFalse((root/'oidc').exists())
   self.assertIn("COMPOSE_FILE='compose.yaml'",candidate.read_text())
   self.assertRaises(ValueError,config.validate,'axxium',{'AXXIUM_OIDC_CONFIG_FILE':config.AUTHORITY_FILE})



class NativeComposeTests(unittest.TestCase):
 """Standalone Compose parser only. Never contacts a daemon or starts anything."""
 def setUp(self):
  candidates=[Path(os.environ['COMPOSE_TEST_BINARY'])] if os.environ.get('COMPOSE_TEST_BINARY') else [Path('/usr/libexec/docker/cli-plugins/docker-compose'),Path('/usr/lib/docker/cli-plugins/docker-compose'),Path.home()/'.docker/cli-plugins/docker-compose']
  self.parser=next((p for p in candidates if p.is_file()),None)
  if self.parser is None:self.fail('Standalone native Compose parser required (COMPOSE_TEST_BINARY)')
 def run_model(self,files,env):
  import subprocess
  args=[str(self.parser),'--project-name','integration-fixture']
  for path in files:args+=['-f',str(path)]
  args+=['config','--format','json']
  result=subprocess.run(args,env={'PATH':os.defpath,**env},capture_output=True,text=True,timeout=15,check=True)
  return json.loads(result.stdout)
 def test_real_proxx_config_uses_each_declared_port_and_fixed_image(self):
  for phase in ('testing','staging'):
   row=slots.proxx_slot(phase)
   model=self.run_model([ROOT/'digitalocean/environments/proxx.compose.yaml'],{
    **config.slot_values(phase,'proxx'),'PROXX_IMAGE':'sha256:'+'d'*64,
    'PROXY_AUTH_TOKEN':'synthetic','PROXY_TOKEN_PEPPER':'synthetic','PROXX_SESSION_SECRET':'synthetic','PROXX_POSTGRES_PASSWORD':'synthetic'})
   app=model['services']['proxx'];self.assertEqual('1000:1000',app['user'])
   self.assertEqual(row['host_port'],int(app['ports'][0]['published']))
   self.assertEqual('127.0.0.1',app['ports'][0]['host_ip'])
   self.assertEqual('sha256:'+'d'*64,app['image'])
 def test_real_authority_overlay_mounts_only_dedicated_directory(self):
  model=self.run_model([ROOT/'digitalocean/environments/axxium.compose.yaml',ROOT/'digitalocean/environments/axxium-oidc.compose.yaml'],{
   'AXXIUM_IMAGE':'sha256:'+'d'*64,'AXXIUM_PUBLIC_BASE_URL':'https://axxium.promethean.rest','DB_PASSWORD':'synthetic','JWT_SECRET':'synthetic'})
  app=model['services']['axxium'];mount=next(v for v in app['volumes'] if v['target']=='/run/axxium/oidc')
  self.assertEqual('oidc',Path(mount['source']).name)
  self.assertTrue(mount['read_only'])
  # Compose may omit default false; the raw-YAML test requires explicit false.
  self.assertFalse(mount.get('bind',{}).get('create_host_path',False))
  self.assertEqual(config.AUTHORITY_FILE,app['environment']['AXXIUM_OIDC_CONFIG_FILE'])
 def test_production_keeps_existing_external_ingress_alias(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);operator=root/'operator.env';operator.write_text('EXISTING_SETTING=synthetic\n')
   model=self.run_model([ROOT/'digitalocean/environments/proxx-existing.compose.yaml',
      ROOT/'digitalocean/environments/proxx-production-network.compose.yaml'],{
    **config.slot_values('production','proxx'),'PROXX_IMAGE':'sha256:'+'d'*64,
    'PROXY_AUTH_TOKEN':'synthetic','PROXY_TOKEN_PEPPER':'synthetic','PROXX_SESSION_SECRET':'synthetic',
    'PROXX_EXISTING_BIND_ADDRESS':'127.0.0.1','PROXX_DATABASE_URL':'postgresql://synthetic/existing',
    'PROXX_DATA_PATH':str(root),'PROXX_OPERATOR_ENV_FILE':str(operator)})
   self.assertEqual({'default'},set(model['networks']))
   self.assertEqual('open-hax',model['networks']['default']['name'])
   self.assertTrue(model['networks']['default']['external'])
   self.assertFalse(model['networks']['default'].get('ipam'))
   self.assertEqual(['proxx'],model['services']['proxx']['networks']['default']['aliases'])
   self.assertEqual({'proxx'},set(model['services']))

 def test_real_effective_env_preserved_and_reprojected(self):
  import subprocess
  original=subprocess.run
  def parser_only(command,**kwargs):
   self.assertEqual(['docker','compose'],command[:2])
   return original([str(self.parser),*command[2:]],**kwargs)
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);old=root/'.env';candidate=root/'candidate'
   old.write_text("""AXXIUM_OIDC_ISSUER='https://axxium.promethean.rest/oidc'
AXXIUM_OIDC_PUBLIC_ORIGIN='https://proxx.promethean.rest'
AXXIUM_OIDC_CLIENT_ID='synthetic-client'
AXXIUM_OIDC_CLIENT_SECRET='synthetic-private'
AXXIUM_OIDC_ACCOUNT_LINKS='[]'
""")
   candidate.write_text("PROXX_IMAGE='sha256:synthetic'\n")
   for path in (old,candidate):path.chmod(0o600)
   before=old.read_bytes()
   with patch.object(preserve.subprocess,'run',side_effect=parser_only):
    preserve.preserve('proxx',old,candidate,root)
    projected=preserve.projection('proxx',candidate)
   self.assertEqual('synthetic-private',projected['AXXIUM_OIDC_CLIENT_SECRET'])
   self.assertEqual(before,old.read_bytes())

 def test_existing_stack_projects_preserved_operator_settings_and_exact_database(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);operator=root/'operator.env';operator.write_text('EXISTING_PROVIDER_SETTING=synthetic-retained\n')
   data=root/'data';data.mkdir()
   database='postgresql://synthetic:synthetic@host.docker.internal:5433/existing'
   model=self.run_model([ROOT/'digitalocean/environments/proxx-existing.compose.yaml'],{
    **config.slot_values('yoga','proxx'),'PROXX_IMAGE':'sha256:'+'d'*64,
    'PROXY_AUTH_TOKEN':'synthetic','PROXY_TOKEN_PEPPER':'synthetic','PROXX_SESSION_SECRET':'synthetic',
    'PROXX_EXISTING_BIND_ADDRESS':'0.0.0.0','PROXX_DATABASE_URL':database,'PROXX_DATA_PATH':str(data),
    'PROXX_OPERATOR_ENV_FILE':str(operator)})
   self.assertEqual({'proxx'},set(model['services']))
   app=model['services']['proxx']
   self.assertEqual('synthetic-retained',app['environment']['EXISTING_PROVIDER_SETTING'])
   self.assertEqual(database,app['environment']['DATABASE_URL'])
   self.assertEqual(str(data),app['volumes'][0]['source'])
   self.assertEqual('0.0.0.0',app['ports'][0]['host_ip'])


class ProtectedReceiverTests(unittest.TestCase):
 def test_host_source_archive_binding_is_rechecked_natively(self):
  with tempfile.TemporaryDirectory() as d:
   slot=Path(d)/'yoga'/'proxx';slot.mkdir(parents=True)
   archive=slot/'image.tar';archive.write_bytes(b'synthetic-archive')
   data={'pull_request':12,'controller_sha':CONTROLLER,'host':'yoga','project':'proxx-yoga'}
   good={'admitted':True,'source_sha':SHA,'deployment_authorization':{'host':'yoga'}}
   with patch.object(protected,'enrollment',return_value=data),patch.object(protected,'admit',return_value=good) as admit:
    self.assertEqual('proxx-yoga',protected.authorize(slot,'yoga','proxx',SHA,archive))
    self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(),admit.call_args.kwargs['image_sha256'])
    self.assertEqual(CONTROLLER,admit.call_args.kwargs['controller_sha'])
   for bad in ({**good,'source_sha':'d'*40},{**good,'admitted':False},{**good,'deployment_authorization':{'host':'stealth'}},{}):
    with patch.object(protected,'enrollment',return_value=data),patch.object(protected,'admit',return_value=bad):
     with self.assertRaises(ValueError):protected.authorize(slot,'yoga','proxx',SHA,archive)
 def test_wrong_host_uid_effective_state_or_topology_cannot_enroll(self):
  from types import SimpleNamespace
  with tempfile.TemporaryDirectory() as d:
   slot=Path(d)/'yoga'/'proxx';slot.mkdir(parents=True)
   (slot/'.env').write_text('synthetic')
   names=['compose.yaml','proxx-relay-broker.compose.yaml','proxx-relay-node.compose.yaml']
   for name in names:(slot/name).write_text('fixed-reviewed-synthetic')
   data={'version':1,'environment':'yoga','service':'proxx','host':'yoga','native_hostname':socket.gethostname(),
    'operator_uid':os.getuid(),'controller_sha':CONTROLLER,'pull_request':12,'project':'proxx-yoga',
    'topology_sha256':{name:hashlib.sha256((slot/name).read_bytes()).hexdigest() for name in names},
    'migration':{'runtime':'existing-receiver-compose','database':'synthetic-existing-database',
     'data':str(slot),'bind_address':'0.0.0.0','operator_env_file':str(slot/'.env'),
     'operator_env_sha256':hashlib.sha256((slot/'.env').read_bytes()).hexdigest(),'effective_env_sha256':hashlib.sha256((slot/'.env').read_bytes()).hexdigest(),
     'backup':'synthetic-backup-evidence','rollback':'synthetic-restore-evidence',
     'client_access':'synthetic-host.docker.internal:8789-proof','review':'synthetic-reviewed-plan'}}
   enrollment=slot/'protected-receiver.json'
   # Root metadata is modeled; the sandbox cannot create a root-owned file.
   meta=SimpleNamespace(st_mode=0o100600,st_uid=0,st_size=2048)
   def check(value):
    enrollment.write_text(json.dumps(value))
    with patch.object(protected.os,'fstat',return_value=meta):return protected.enrollment(slot,'yoga','proxx')
   self.assertEqual(data,check(data))
   for key,bad in [('host','stealth'),('native_hostname','other'),('operator_uid',os.getuid()+1),('controller_sha','main'),('project','other'),('pull_request',True),('topology_sha256',{})]:
    with self.assertRaises(ValueError):check({**data,key:bad})
   for key,bad in [('runtime','systemd'),('effective_env_sha256','d'*64),('backup',''),('rollback',''),('client_access','')]:
    with self.assertRaises(ValueError):check({**data,'migration':{**data['migration'],key:bad}})
   check(data);(slot/'proxx-relay-node.compose.yaml').write_text('changed')
   with self.assertRaises(ValueError):check(data)
 def test_existing_token_pepper_session_and_operator_env_cannot_rotate(self):
  from types import SimpleNamespace
  with tempfile.TemporaryDirectory() as d:
   slot=Path(d)/'yoga'/'proxx';slot.mkdir(parents=True)
   operator=slot/'operator.env';operator.write_text('synthetic-existing');operator.chmod(0o600)
   migration={'database':'synthetic-existing-database','data':str(slot),'bind_address':'0.0.0.0',
    'operator_env_file':str(operator),'operator_env_sha256':hashlib.sha256(operator.read_bytes()).hexdigest()}
   values={'PROXX_DATABASE_URL':migration['database'],'PROXX_DATA_PATH':str(slot),
    'PROXX_EXISTING_BIND_ADDRESS':'0.0.0.0','PROXX_OPERATOR_ENV_FILE':str(operator),
    'PROXY_AUTH_TOKEN':'synthetic-token','PROXY_TOKEN_PEPPER':'synthetic-pepper','PROXX_SESSION_SECRET':'synthetic-session'}
   original={'PROXY_AUTH_TOKEN':'synthetic-token','PROXY_TOKEN_PEPPER':'synthetic-pepper','SESSION_SECRET':'synthetic-session'}
   result=SimpleNamespace(stdout=json.dumps({'services':{'preserve':{'environment':original}}}))
   with patch.object(protected,'enrollment',return_value={'migration':migration}),patch.object(protected.subprocess,'run',return_value=result) as run:
    protected.existing_state(slot,values)
    self.assertEqual({'PATH'},set(run.call_args.kwargs['env']))
    for value in ('',config.ISSUER):
     result.stdout=json.dumps({'services':{'preserve':{'environment':{**original,'AXXIUM_RELAY_ISSUER':value}}}})
     with self.assertRaises(ValueError):protected.existing_state(slot,values)
    result.stdout=json.dumps({'services':{'preserve':{'environment':original}}})
    for key in ('PROXY_AUTH_TOKEN','PROXY_TOKEN_PEPPER','PROXX_SESSION_SECRET','PROXX_DATABASE_URL','PROXX_DATA_PATH','PROXX_OPERATOR_ENV_FILE','PROXX_EXISTING_BIND_ADDRESS'):
     with self.assertRaises(ValueError):protected.existing_state(slot,{**values,key:'changed'})
   self.assertEqual('synthetic-existing',operator.read_text())

 def test_missing_enrollment_refuses_before_candidate_intake(self):
  from test_environment_shared_login import r
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);slot=root/'yoga'/'proxx';slot.mkdir(parents=True)
   with patch.object(r,'ROOT',root),patch.object(r,'admitted_selection',return_value=b'prior'), \
        patch.object(r,'preflight') as preflight,patch.object(r,'load_images') as load, \
        patch.object(sys,'argv',['receiver','yoga','proxx']),patch.dict(os.environ,{'SSH_ORIGINAL_COMMAND':'receive yoga proxx '+SHA}):
    with self.assertRaisesRegex(ValueError,'enrollment'):r.main()
    preflight.assert_not_called();load.assert_not_called()
   self.assertFalse((slot/'release.env').exists())


class LaptopRetentionScopeTests(unittest.TestCase):
 def test_valid_enrolled_laptop_does_not_require_droplet_slots(self):
  from test_environment_shared_login import r
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);slot=root/'yoga'/'proxx';slot.mkdir(parents=True)
   def inventory(args):
    if args[:2]==['image','ls'] or args[:2]==['container','ls']:return ''
    raise AssertionError(args)
   with patch.object(r,'ROOT',root),patch.object(r,'enrollment',return_value={'host':'yoga'}) as enroll,patch.object(r,'docker_output',side_effect=inventory):
    self.assertEqual((set(),set(),{},{}),r.reference_inventory())
    self.assertEqual((slot,'yoga','proxx'),enroll.call_args.args)
   (root/'testing').mkdir()
   with patch.object(r,'ROOT',root),patch.object(r,'enrollment',return_value={'host':'yoga'}),patch.object(r,'docker_output') as docker:
    with self.assertRaisesRegex(r.RetentionBlocked,'Mixed'):r.reference_inventory()
    docker.assert_not_called()
   (root/'testing').rmdir()
   with patch.object(r,'ROOT',root),patch.object(r,'enrollment',side_effect=ValueError('unavailable')),patch.object(r,'docker_output') as docker:
    with self.assertRaisesRegex(r.RetentionBlocked,'enrollment'):r.reference_inventory()
    docker.assert_not_called()


class RuntimeEnvironmentTests(unittest.TestCase):
 def test_ambient_values_cannot_redirect_runtime_or_override_preflight(self):
  from test_environment_shared_login import r
  with patch.dict(os.environ,{'COMPOSE_FILE':'untrusted.yaml','DOCKER_HOST':'tcp://untrusted',
     'PROXX_LISTEN_PORT':'1','PROXY_TOKEN_PEPPER':'untrusted'}):
   self.assertEqual({'PATH'},set(r.runtime_environment()))


class ReceiverStateTests(unittest.TestCase):
 def test_proxx_failed_upgrade_preserves_all_private_bytes(self):
  from test_environment_receiver import ReceiverHarness
  h=ReceiverHarness(self)
  slot=h.slot(service='proxx');private=slot/'state'/'data';private.mkdir(parents=True)
  (private/'settings.json').write_text('synthetic-existing-provider-state')
  self.assertEqual(0,h.receive('a',service='proxx').returncode)
  paths=[slot/'.env',private/'settings.json',slot/'release.env',slot/'deployment.json']
  before={path:path.read_bytes() for path in paths}
  state=h.data();state['fail_tags']=[h.tag('b','proxx')];h.save(state)
  result=h.receive('b',service='proxx')
  self.assertNotEqual(0,result.returncode)
  self.assertEqual(before,{path:path.read_bytes() for path in paths})
  self.assertEqual([True,False,True],[row['healthy'] for row in h.data()['health']])



class NativeProtectedCollectorTests(unittest.TestCase):
 def fixture(self):
  pr={'merged':True,'base':{'ref':'main'},'merge_commit_sha':SHA}
  review=OwnerTests().review('production')
  proof={'admitted':True,'source_sha':SHA,'environment':'production'}
  def api(path,**_):
   if path.endswith('/branches/main'):return {'commit':{'sha':SHA}}
   if '/git/trees/' in path:return {'truncated':False,'tree':[{'type':'blob','path':'src/main.ts'},{'type':'blob','path':'.github/CODEOWNERS'}]}
   if path.endswith('/pulls/12'):return copy.deepcopy(pr)
   raise AssertionError(path)
  return pr,review,proof,api
 def test_main_qualification_native_owner_and_full_accepted_tree(self):
  pr,review,proof,api=self.fixture()
  with patch.object(github,'api',side_effect=api),patch.object(github,'qualification',return_value=proof) as qualify, \
       patch.object(github,'pages',return_value=[review]),patch.object(github,'owns_files',return_value=True) as owns:
   result=github.protected_admission('open-hax/proxx',12,'production',pr,CONTROLLER,IMAGE)
   self.assertTrue(result['admitted'])
   self.assertEqual(('open-hax/proxx',SHA,CONTROLLER),qualify.call_args.args)
   self.assertEqual(['src/main.ts','.github/CODEOWNERS'],owns.call_args.args[2])
 def test_missing_or_wrong_native_evidence_never_admits(self):
  pr,review,proof,api=self.fixture()
  for owner,qualified,reviews in [(False,proof,[review]),(True,{'admitted':False},[review]),(True,proof,[])]:
   with patch.object(github,'api',side_effect=api),patch.object(github,'qualification',return_value=qualified), \
        patch.object(github,'pages',return_value=reviews),patch.object(github,'owns_files',return_value=owner):
    self.assertFalse(github.protected_admission('open-hax/proxx',12,'production',pr,CONTROLLER,IMAGE)['admitted'])
  for change in ({'merged':False},{'merge_commit_sha':'d'*40},{'base':{'ref':'staging'}}):
   with patch.object(github,'api',side_effect=api),patch.object(github,'qualification',return_value=proof):
    self.assertFalse(github.protected_admission('open-hax/proxx',12,'production',{**pr,**change},CONTROLLER,IMAGE)['admitted'])
 def test_immutable_qualification_decodes_native_wrapped_base64_and_binds_source(self):
  import base64
  proof={'source_sha':SHA,'status':'passed','assertions':1}
  data={'repository':'open-hax/proxx','source_sha':SHA,'integration':proof,'e2e':proof,'mutation_batches':batches()}
  content=base64.encodebytes(json.dumps(data).encode()).decode()
  with patch.object(github,'api',return_value={'type':'file','encoding':'base64','content':content}) as api:
   self.assertTrue(github.qualification('open-hax/proxx',SHA,CONTROLLER)['admitted'])
   self.assertEqual(f'repos/open-hax/services/contents/digitalocean/qualifications/proxx/{SHA}.json?ref={CONTROLLER}',api.call_args.args[0])
  for field,bad in [('repository','other/proxx'),('source_sha','d'*40)]:
   invalid=base64.encodebytes(json.dumps({**data,field:bad}).encode()).decode()
   with patch.object(github,'api',return_value={'type':'file','encoding':'base64','content':invalid}):
    with self.assertRaises(ValueError):github.qualification('open-hax/proxx',SHA,CONTROLLER)

 def test_native_main_movement_and_truncated_tree_refuse(self):
  pr,review,proof,api=self.fixture()
  for mode in ('main-moved','truncated'):
   branches=[]
   def changed(path,**kw):
    data=api(path,**kw)
    if path.endswith('/branches/main'):
     branches.append(path)
     if mode=='main-moved' and len(branches)>1:return {'commit':{'sha':'d'*40}}
    if '/git/trees/' in path and mode=='truncated':return {**data,'truncated':True}
    return data
   with patch.object(github,'api',side_effect=changed),patch.object(github,'qualification',return_value=proof), \
        patch.object(github,'pages',return_value=[review]),patch.object(github,'owns_files',return_value=True):
    self.assertFalse(github.protected_admission('open-hax/proxx',12,'production',pr,CONTROLLER,IMAGE)['admitted'])

if __name__=='__main__':unittest.main()
