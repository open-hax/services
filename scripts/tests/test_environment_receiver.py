import hashlib,importlib.util,io,json,os,subprocess,sys,tarfile,tempfile,unittest
from pathlib import Path
spec=importlib.util.spec_from_file_location('receiver',Path(__file__).parents[1]/'receive-service-environment.py')
receiver=importlib.util.module_from_spec(spec);spec.loader.exec_module(receiver)
class ReceiverTests(unittest.TestCase):
 def archive(self,tags,extra=None):
  tmp=tempfile.NamedTemporaryFile();self.addCleanup(tmp.close)
  with tarfile.open(tmp.name,'w') as out:
   payload=json.dumps([{'RepoTags':tags,'Config':'config.json','Layers':[]}]).encode();entry=tarfile.TarInfo('manifest.json');entry.size=len(payload);out.addfile(entry,io.BytesIO(payload))
   if extra:out.addfile(tarfile.TarInfo(extra),io.BytesIO())
  return tmp.name
 def test_only_admitted_image_can_be_loaded(self):
  sha='a'*40
  receiver.validate_archive(self.archive(['promethean-axxium:'+sha]),'axxium',sha)
  for tags in [['caddy:latest'],['promethean-axxium:'+sha,'caddy:latest'],['promethean-axxium:'+'b'*40]]:
   with self.assertRaises(ValueError):receiver.validate_archive(self.archive(tags),'axxium',sha)
 def test_archive_paths_cannot_escape(self):
  for path in ['../outside','/etc/passwd']:
   with self.assertRaises(ValueError):receiver.validate_archive(self.archive(['promethean-axxium:'+'a'*40],path),'axxium','a'*40)

# The receiver is executed in another process. Only its Docker executable is
# doubled; archive admission, stdin, locking, files and rollback are real.
DOCKER_DOUBLE = r'''#!/usr/bin/env python3
import hashlib,json,os,sys,tarfile
from pathlib import Path
p=Path(os.environ['RECEIVER_TEST_DOCKER_STATE']);s=json.loads(p.read_text());a=sys.argv[1:]
s['calls'].append(a)
def finish(code=0,text=''):
 p.write_text(json.dumps(s));print(text,end='');sys.exit(code)
if a[:1]==['load']:
 with tarfile.open(a[a.index('--input')+1]) as t:
  for row in json.load(t.extractfile('manifest.json')):
   for tag in row['RepoTags']:s['images'][tag]='sha256:'+hashlib.sha256(tag.encode()).hexdigest()
 finish()
if a[:2]==['image','inspect']:
 tag=a[-1]
 if tag not in s['images']:finish(1)
 finish(text=s['images'][tag]+'\n')
if a[:2]==['image','ls']:
 if s.get('inventory')=='unavailable-images':finish(1)
 if s.get('inventory')=='malformed-images':finish(text='not-json\n')
 rows=[json.dumps({'ID':i,'Repository':t.rsplit(':',1)[0],'Tag':t.rsplit(':',1)[1]}) for t,i in s['images'].items()]
 finish(text='\n'.join(rows)+('\n' if rows else ''))
if a[:2]==['container','ls']:
 if '--all' not in a:finish(2)
 if s.get('inventory')=='unavailable-containers':finish(1)
 if s.get('inventory')=='malformed-containers':finish(text='invalid-container-id\n')
 finish(text='\n'.join(s['containers'])+('\n' if s['containers'] else ''))
if a[:2]==['container','inspect']:
 ids=a[a.index('--format')+2:]
 if s.get('inventory')=='malformed-container-images':finish(text='not-an-image-id\n')
 if any(i not in s['containers'] for i in ids):finish(1)
 finish(text='\n'.join(s['containers'][i] for i in ids)+'\n')
if a[:2]==['image','rm']:
 if len(a)!=3 or a[2].startswith('sha256:'):finish(2)
 tag=a[2];s['removal_attempts'].append(tag)
 if s.get('fail_remove') or tag not in s['images'] or s['images'][tag] in s['containers'].values():finish(1)
 del s['images'][tag];s['removed'].append(tag);finish()
if a[:1]==['compose']:
 if 'up' in a:
  release=Path(a[[i for i,x in enumerate(a) if x=='--env-file'][-1]+1])
  values=[x.split('=',1)[1] for x in release.read_text().splitlines()]
  failed=any(s['images'].get(t) in values for t in s.get('fail_tags',[]))
  s['health'].append({'images':values,'healthy':not failed,'removals_before':list(s['removed'])})
  finish(1 if failed else 0)
 if 'stop' in a:finish()
finish(2)
'''


class ReceiverHarness:
 def __init__(self,test):
  self.tmp=tempfile.TemporaryDirectory();test.addCleanup(self.tmp.cleanup)
  self.root=Path(self.tmp.name);self.bin=self.root/'bin';self.bin.mkdir()
  docker=self.bin/'docker';docker.write_text(DOCKER_DOUBLE);docker.chmod(0o700)
  self.state=self.root/'docker.json'
  self.save({'images':{},'containers':{},'calls':[],'health':[],'removed':[],'removal_attempts':[]})
  for env in ('testing','staging'):
   for service in ('knoxx','axxium'):
    slot=self.slot(env,service);slot.mkdir(parents=True);(slot/'.env').write_text('');(slot/'compose.yaml').write_text('services: {}\n')
 def slot(self,env='testing',service='axxium'):return self.root/env/service
 def data(self):return json.loads(self.state.read_text())
 def save(self,data):self.state.write_text(json.dumps(data))
 def tag(self,letter,service='axxium'):return 'promethean-'+service+':'+letter*40
 def image_id(self,tag):return 'sha256:'+hashlib.sha256(tag.encode()).hexdigest()
 def selection(self,path):return dict(line.split('=',1) for line in path.read_text().splitlines())
 def receive(self,letter,env='testing',service='axxium'):
  tags=receiver.expected_tags(service,letter*40);stream=io.BytesIO()
  with tarfile.open(fileobj=stream,mode='w') as out:
   payload=json.dumps([{'RepoTags':[t],'Config':'config.json','Layers':[]} for t in sorted(tags)]).encode()
   entry=tarfile.TarInfo('manifest.json');entry.size=len(payload);out.addfile(entry,io.BytesIO(payload))
  script="import importlib.util,sys;from pathlib import Path;s=importlib.util.spec_from_file_location('receiver',sys.argv[1]);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);m.ROOT=Path(sys.argv[2]);sys.argv=['receiver',sys.argv[3],sys.argv[4]];raise SystemExit(m.main())"
  source=Path(receiver.__file__).resolve()
  return subprocess.run([sys.executable,'-c',script,str(source),str(self.root),env,service],input=stream.getvalue(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=15,env={'PATH':str(self.bin)+os.pathsep+os.environ['PATH'],'RECEIVER_TEST_DOCKER_STATE':str(self.state),'SSH_ORIGINAL_COMMAND':f'receive {env} {service} '+letter*40})
 def receipt(self,env='testing',service='axxium'):return json.loads((self.slot(env,service)/'deployment.json').read_text())


class ReceiverLifecycleTests(unittest.TestCase):
 def harness(self):return ReceiverHarness(self)
 def healthy(self,h,letter,**kwargs):
  result=h.receive(letter,**kwargs);self.assertEqual(0,result.returncode,result.stderr.decode());return h.receipt(**kwargs)
 def generations(self,h):self.healthy(h,'a');self.healthy(h,'b')
 def test_three_healthy_generations_keep_active_and_one_rollback(self):
  h=self.harness();self.generations(h);self.healthy(h,'c');d=h.data();receipt=h.receipt()
  self.assertEqual([h.tag('a')],d['removed'])
  self.assertEqual({'AXXIUM_IMAGE':h.image_id(h.tag('c'))},h.selection(h.slot()/'release.env'))
  self.assertEqual({'AXXIUM_IMAGE':h.image_id(h.tag('b'))},h.selection(h.slot()/'rollback.env'))
  self.assertEqual([],d['health'][-1]['removals_before'])
  self.assertEqual('healthy',receipt['status']);self.assertEqual('complete',receipt['image_cleanup']['status'])
  self.assertEqual([h.tag('a')],receipt['image_cleanup']['removed'])
  for call in d['calls']:
   self.assertNotIn('--force',call);self.assertNotIn('prune',call)
   if call[:2]==['image','rm']:self.assertEqual(3,len(call));self.assertFalse(call[-1].startswith('sha256:'))
 def test_failed_upgrade_preserves_active_rollback_and_receipt(self):
  h=self.harness();self.generations(h)
  before={p.name:p.read_bytes() for p in (h.slot()/'release.env',h.slot()/'rollback.env',h.slot()/'deployment.json')}
  d=h.data();d['fail_tags']=[h.tag('c')];h.save(d);result=h.receive('c')
  self.assertNotEqual(0,result.returncode)
  self.assertEqual(before,{(h.slot()/name).name:(h.slot()/name).read_bytes() for name in before})
  self.assertEqual([],h.data()['removal_attempts']);self.assertFalse(h.data()['health'][-2]['healthy']);self.assertTrue(h.data()['health'][-1]['healthy'])
 def test_first_failed_start_stops_apps_without_cleanup(self):
  h=self.harness();d=h.data();d['fail_tags']=[h.tag('a')];h.save(d)
  self.assertNotEqual(0,h.receive('a').returncode);self.assertEqual([],h.data()['removal_attempts'])
  self.assertFalse((h.slot()/'deployment.json').exists())
  self.assertTrue(any('stop' in c for c in h.data()['calls']))
 def test_every_other_slots_active_and_rollback_ids_are_conserved(self):
  for env,service in [('testing','knoxx'),('staging','knoxx'),('staging','axxium')]:
   for filename in ('release.env','rollback.env'):
    with self.subTest(env=env,service=service,selection=filename):
     h=self.harness();self.generations(h);names=['AXXIUM_IMAGE'] if service=='axxium' else ['KNOXX_BACKEND_IMAGE','KNOXX_FRONTEND_IMAGE']
     (h.slot(env,service)/'release.env').write_text(''.join(k+'='+h.image_id(h.tag('b'))+'\n' for k in names))
     (h.slot(env,service)/filename).write_text(''.join(k+'='+h.image_id(h.tag('a'))+'\n' for k in names))
     self.healthy(h,'c');self.assertNotIn(h.tag('a'),h.data()['removed'])
 def test_all_containers_including_stopped_production_conserve_image_ids(self):
  h=self.harness();self.generations(h);d=h.data();d['containers']['d'*64]=h.image_id(h.tag('a'));h.save(d)
  self.healthy(h,'c');self.assertEqual([],h.data()['removed'])
  self.assertTrue(any(c[:2]==['container','ls'] and '--all' in c for c in h.data()['calls']))
 def test_nonreceiver_alias_conserves_entire_image(self):
  h=self.harness();self.generations(h);d=h.data();d['images']['production/untouched:latest']=h.image_id(h.tag('a'));h.save(d)
  self.healthy(h,'c');self.assertEqual([],h.data()['removed'])
 def test_unrecorded_source_sha_tags_are_never_swept(self):
  h=self.harness();d=h.data();legacy=h.tag('d');d['images'][legacy]=h.image_id(legacy);h.save(d)
  self.generations(h);self.healthy(h,'c');self.assertEqual([h.tag('a')],h.data()['removed']);self.assertIn(legacy,h.data()['images'])
 def test_invalid_or_unavailable_docker_inventories_refuse_cleanup_after_health(self):
  for mode in ('unavailable-images','malformed-images','unavailable-containers','malformed-containers','malformed-container-images'):
   with self.subTest(mode=mode):
    h=self.harness();self.generations(h);d=h.data();d['inventory']=mode;d['containers']['d'*64]=h.image_id(h.tag('b'));h.save(d)
    result=h.receive('c');self.assertEqual(3,result.returncode,result.stderr.decode());r=h.receipt()
    self.assertEqual('healthy',r['status']);self.assertEqual('blocked',r['image_cleanup']['status']);self.assertTrue(r['image_cleanup']['refused']);self.assertEqual([],h.data()['removal_attempts'])
 def test_malformed_slot_selection_refuses_cleanup(self):
  h=self.harness();self.generations(h);(h.slot('staging','knoxx')/'rollback.env').write_text('KNOXX_BACKEND_IMAGE=untrusted:latest\n')
  self.assertEqual(3,h.receive('c').returncode);self.assertEqual('healthy',h.receipt()['status']);self.assertEqual('blocked',h.receipt()['image_cleanup']['status']);self.assertEqual([],h.data()['removal_attempts'])
 def test_missing_slot_refuses_cleanup(self):
  h=self.harness();self.generations(h);slot=h.slot('staging','knoxx');(slot/'.env').unlink();(slot/'compose.yaml').unlink();slot.rmdir()
  self.assertEqual(3,h.receive('c').returncode);self.assertEqual([],h.data()['removal_attempts'])
 def test_malformed_ownership_catalog_does_not_turn_into_legacy_sweep(self):
  h=self.harness();self.generations(h);(h.root/'receiver-images.json').write_text('{broken')
  self.assertEqual(3,h.receive('c').returncode);self.assertEqual('healthy',h.receipt()['status']);self.assertEqual('blocked',h.receipt()['image_cleanup']['status']);self.assertEqual([],h.data()['removal_attempts'])
 def test_failed_removal_is_reported_without_health_rollback_or_automatic_retry(self):
  h=self.harness();self.generations(h);d=h.data();d['fail_remove']=True;h.save(d)
  self.assertEqual(3,h.receive('c').returncode);r=h.receipt();self.assertEqual('healthy',r['status']);self.assertEqual('failed',r['image_cleanup']['status']);self.assertEqual([],r['image_cleanup']['removed']);self.assertTrue(r['image_cleanup']['refused'])
  self.assertEqual(h.image_id(h.tag('c')),h.selection(h.slot()/'release.env')['AXXIUM_IMAGE'])
  d=h.data();d['fail_remove']=False;h.save(d)
  self.assertEqual(3,h.receive('d').returncode);self.assertEqual([h.tag('a')],h.data()['removal_attempts'])
 def test_knoxx_generations_retire_only_both_old_recorded_tags(self):
  h=self.harness()
  for letter in 'abc':self.healthy(h,letter,service='knoxx')
  self.assertEqual(receiver.expected_tags('knoxx','a'*40),set(h.data()['removed']))
  self.assertEqual(2,len(h.selection(h.slot(service='knoxx')/'rollback.env')))

if __name__=='__main__':unittest.main()
