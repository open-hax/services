import hashlib,importlib.util,io,json,os,subprocess,sys,tarfile,tempfile,unittest
from pathlib import Path
spec=importlib.util.spec_from_file_location('receiver',Path(__file__).parents[1]/'receive-service-environment.py')
receiver=importlib.util.module_from_spec(spec);spec.loader.exec_module(receiver)
# Literal Docker29.9.0 `image ls --all --digests --no-trunc --format '{{json .}}'`
# row retained from the first native retention qualification, 2026-10-15.
NAMED_UNTAGGED_DIGEST_ROW=json.loads(r'''{"Containers":"1","CreatedAt":"2026-07-27 13:47:31 -0500 CDT","CreatedSince":"2 months ago","Digest":"sha256:98c19ced6600f2924e80b92d701cd867d8f7ef0c4dde516c619484e31e47f103","ID":"sha256:dacbdaa86a43fb9ed58174a53d15627780829ba4c34badb15263d4104c0a37ec","Repository":"ollama/ollama","SharedSize":"N/A","Size":"4.76GB","Tag":"<none>","UniqueSize":"N/A"}''')
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
s['known_ids']=sorted(set(s.get('known_ids',[]))|set(s['images'].values()))
def finish(code=0,text=''):
 p.write_text(json.dumps(s));print(text,end='');sys.exit(code)
if a[:1]==['load']:
 s['load_count']=s.get('load_count',0)+1
 s.setdefault('load_markers',[]).append((p.parent/'receiver-intake.json').exists())
 with tarfile.open(a[a.index('--input')+1]) as t:
  for row in json.load(t.extractfile('manifest.json')):
   for tag in row['RepoTags']:
    s['images'][tag]=s.get('load_ids',{}).get(tag,'sha256:'+hashlib.sha256(tag.encode()).hexdigest())
    s['known_ids']=sorted(set(s['known_ids'])|{s['images'][tag]})
    if s.get('partial_load'):finish(1)
 finish(s.get('load_exit',0))
if a[:2]==['image','inspect']:
 tag=a[-1]
 if tag.startswith('sha256:'):
  finish(0 if tag in s['known_ids'] and tag not in s.get('missing_ids',[]) else 1,text=tag+'\n')
 if s.get('inspect_fail_after_load') and s.get('load_count',0)>=s['inspect_fail_after_load'] and tag in s.get('load_ids',{}):finish(1)
 if tag not in s['images']:finish(1)
 finish(text=s['images'][tag]+'\n')
if a[:2]==['image','tag']:
 old,tag=a[-2:];s.setdefault('restore_attempts',[]).append({'tag':tag,'image_id':old})
 if old not in s['known_ids'] or tag in s.get('fail_restore',[]):finish(1)
 s['images'][tag]=s.get('restore_ids',{}).get(tag,old);finish()
if a[:2]==['image','ls']:
 if s.get('inventory')=='unavailable-images':finish(1)
 if s.get('inventory')=='malformed-images':finish(text='not-json\n')
 rows=[json.dumps({'ID':i,'Repository':t.rsplit(':',1)[0],'Tag':t.rsplit(':',1)[1],'Digest':'<none>'}) for t,i in s['images'].items()]
 rows.extend(json.dumps(row) for row in s.get('extra_image_rows',[]))
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
 def receive(self,letter,env='testing',service='axxium',barrier_fault=None,release_fault=False,operation_fault=None,intake_fault=None,finish_fault=None):
  tags=receiver.expected_tags(service,letter*40);stream=io.BytesIO()
  with tarfile.open(fileobj=stream,mode='w') as out:
   payload=json.dumps([{'RepoTags':[t],'Config':'config.json','Layers':[]} for t in sorted(tags)]).encode()
   entry=tarfile.TarInfo('manifest.json');entry.size=len(payload);out.addfile(entry,io.BytesIO(payload))
  script="""import importlib.util,sys,json,os
from pathlib import Path
s=importlib.util.spec_from_file_location('receiver',sys.argv[1]);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);m.ROOT=Path(sys.argv[2])
fault=os.environ.get('RECEIVER_TEST_BARRIER_FAULT')
release_fault=os.environ.get('RECEIVER_TEST_RELEASE_DIR_FAULT')
intake_fault=os.environ.get('RECEIVER_TEST_INTAKE_FAULT')
if fault or release_fault or intake_fault:
 original=m.atomic_write
 def injected(path,text):
  release=release_fault and path.name=='release.env'
  intake=intake_fault and path.name=='receiver-intake.json'
  if intake and intake_fault=='crash-after-marker':
   original(path,text);raise KeyboardInterrupt('simulated crash after durable intake marker')
  if not release and not intake and (not fault or path.name!='receiver-images.json' or 'Removal in progress or uncertain' not in text):return original(path,text)
  fsync=m.os.fsync;calls=[]
  def failing_fsync(fd):
   calls.append(fd);(m.ROOT/('release-fault.json' if release else 'barrier-fault.json')).write_text(json.dumps({'fault':'directory' if release else fault,'fsync_calls':len(calls)}))
   if len(calls)==(1 if (intake and intake_fault=='file') or (not release and fault=='file') else 2):raise OSError('injected durable state failure')
   return fsync(fd)
  m.os.fsync=failing_fsync
  try:return original(path,text)
  finally:m.os.fsync=fsync
 m.atomic_write=injected
operation_fault=os.environ.get('RECEIVER_TEST_OPERATION_FAULT')
if operation_fault:
 original_run=m.subprocess.run
 def injected_run(args,*a,**kw):
  result=original_run(args,*a,**kw)
  if args[:2]==['docker','load']:
   if operation_fault=='load-timeout':raise m.subprocess.TimeoutExpired(args,kw.get('timeout',1))
   if operation_fault=='crash-after-load':raise KeyboardInterrupt('simulated crash after load')
  if args[:3]==['docker','image','tag']:
   if operation_fault=='crash-after-restore':raise KeyboardInterrupt('simulated crash after restoration')
   if operation_fault=='restore-timeout':raise m.subprocess.TimeoutExpired(args,kw.get('timeout',1))
   if operation_fault=='restore-oserror':raise OSError('injected uncertain restoration')
  return result
 m.subprocess.run=injected_run
finish_fault=os.environ.get('RECEIVER_TEST_FINISH_FAULT')
if finish_fault:
 original_sync=m.sync_directory;original_write=m.atomic_write;failed=[]
 def finish_sync(path):
  if path==m.ROOT and not (path/'receiver-intake.json').exists() and not failed:
   failed.append(True);raise OSError('injected intake marker removal directory fsync failure')
  return original_sync(path)
 def recovery_write(path,text):
  if failed and path.name=='receiver-intake.json' and finish_fault=='recovery-write-fails':raise OSError('injected unavailable recovery marker write')
  return original_write(path,text)
 m.sync_directory=finish_sync;m.atomic_write=recovery_write
sys.argv=['receiver',sys.argv[3],sys.argv[4]];raise SystemExit(m.main())
"""
  source=Path(receiver.__file__).resolve()
  return subprocess.run([sys.executable,'-B','-c',script,str(source),str(self.root),env,service],input=stream.getvalue(),stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=15,env={'PATH':str(self.bin)+os.pathsep+os.environ['PATH'],'RECEIVER_TEST_DOCKER_STATE':str(self.state),'SSH_ORIGINAL_COMMAND':f'receive {env} {service} '+letter*40,**({'RECEIVER_TEST_BARRIER_FAULT':barrier_fault} if barrier_fault else {}),**({'RECEIVER_TEST_RELEASE_DIR_FAULT':'1'} if release_fault else {}),**({'RECEIVER_TEST_OPERATION_FAULT':operation_fault} if operation_fault else {}),**({'RECEIVER_TEST_INTAKE_FAULT':intake_fault} if intake_fault else {}),**({'RECEIVER_TEST_FINISH_FAULT':finish_fault} if finish_fault else {})})
 def receipt(self,env='testing',service='axxium'):return json.loads((self.slot(env,service)/'deployment.json').read_text())


class ReceiverLifecycleTests(unittest.TestCase):
 def harness(self):return ReceiverHarness(self)
 def healthy(self,h,letter,**kwargs):
  result=h.receive(letter,**kwargs);self.assertEqual(0,result.returncode,result.stderr.decode());return h.receipt(**kwargs)
 def generations(self,h):self.healthy(h,'a');self.healthy(h,'b')
 def protected_bytes(self,h,service='axxium'):
  paths=[h.slot(service=service)/name for name in ('release.env','rollback.env','deployment.json')]+[h.root/'receiver-images.json']
  return {str(path.relative_to(h.root)):path.read_bytes() if path.exists() else None for path in paths}
 def assert_intake_refused(self,h,before,result,service='axxium'):
  self.assertNotEqual(0,result.returncode,result.stdout.decode())
  self.assertEqual(before,self.protected_bytes(h,service))
  self.assertEqual([],h.data()['removal_attempts'])
  marker=h.root/'receiver-intake.json';self.assertTrue(marker.exists())
  data=json.loads(marker.read_text());self.assertEqual('promethean.receiver-intake/v1',data['schema']);self.assertTrue(data['first_failure'])
  retained=marker.read_bytes();calls=h.data()['calls'];self.assertNotEqual(0,h.receive('c',service=service).returncode)
  self.assertEqual(calls,h.data()['calls']);self.assertEqual(retained,marker.read_bytes())
 def test_same_source_different_image_refuses_before_release_and_restores_old_tag(self):
  h=self.harness();self.generations(h);before=self.protected_bytes(h);old=h.data()['images'][h.tag('b')]
  d=h.data();d['load_ids']={h.tag('b'):'sha256:'+'e'*64};h.save(d);health=len(d['health'])
  result=h.receive('b');self.assert_intake_refused(h,before,result)
  self.assertEqual(old,h.data()['images'][h.tag('b')]);self.assertEqual(health,len(h.data()['health']))
  self.assertEqual([{'tag':h.tag('b'),'image_id':old}],h.data()['restore_attempts'])
 def test_paired_knoxx_rebuild_restores_complete_old_expected_map(self):
  for changed_roles in [('knoxx-backend',),('knoxx-backend','knoxx-frontend')]:
   with self.subTest(changed_roles=changed_roles):
    h=self.harness();self.healthy(h,'a',service='knoxx');self.healthy(h,'b',service='knoxx');before=self.protected_bytes(h,'knoxx')
    old={tag:h.data()['images'][tag] for tag in receiver.expected_tags('knoxx','b'*40)};d=h.data()
    d['load_ids']={h.tag('b',role):'sha256:'+str(index+6)*64 for index,role in enumerate(changed_roles)};h.save(d)
    result=h.receive('b',service='knoxx');self.assert_intake_refused(h,before,result,'knoxx')
    self.assertEqual(old,{tag:h.data()['images'][tag] for tag in old})
    self.assertEqual(set(d['load_ids']),{row['tag'] for row in h.data()['restore_attempts']})
 def test_partial_failed_load_and_postload_inspect_failure_restore_without_start(self):
  for changes,operation in [({'partial_load':True},None),({'load_exit':1},None),({},'load-timeout'),({'inspect_fail_after_load':3},None)]:
   with self.subTest(changes=changes,operation=operation):
    h=self.harness();self.generations(h);before=self.protected_bytes(h);old=h.data()['images'][h.tag('b')]
    d=h.data();d.update(changes);d['load_ids']={h.tag('b'):'sha256:'+'e'*64};h.save(d);health=len(d['health'])
    result=h.receive('b',operation_fault=operation);self.assert_intake_refused(h,before,result)
    self.assertEqual(old,h.data()['images'][h.tag('b')]);self.assertEqual(health,len(h.data()['health']))
    self.assertEqual(1,len(h.data()['restore_attempts']))
 def test_restoration_failure_or_wrong_readback_preserves_first_failure_and_custody(self):
  for changes in ({'fail_restore':True},{'wrong_restore':True}):
   with self.subTest(changes=changes):
    h=self.harness();self.generations(h);before=self.protected_bytes(h);d=h.data();d['load_ids']={h.tag('b'):'sha256:'+'e'*64}
    if changes.get('fail_restore'):d['fail_restore']=[h.tag('b')]
    else:d['restore_ids']={h.tag('b'):'sha256:'+'f'*64}
    h.save(d);result=h.receive('b');self.assert_intake_refused(h,before,result)
    marker=json.loads((h.root/'receiver-intake.json').read_text());self.assertIn('changed image ID',marker['first_failure'])
    self.assertEqual(1,len(h.data()['restore_attempts']));self.assertTrue(marker['recovery_errors'])
 def test_uncertain_first_knoxx_restoration_never_attempts_second_role(self):
  for fault in ('restore-timeout','restore-oserror'):
   with self.subTest(fault=fault):
    h=self.harness();self.healthy(h,'a',service='knoxx');self.healthy(h,'b',service='knoxx');before=self.protected_bytes(h,'knoxx')
    d=h.data();backend=h.tag('b','knoxx-backend');frontend=h.tag('b','knoxx-frontend');old={tag:d['images'][tag] for tag in (backend,frontend)}
    d['load_ids']={backend:'sha256:'+'6'*64,frontend:'sha256:'+'7'*64};h.save(d)
    result=h.receive('b',service='knoxx',operation_fault=fault);self.assert_intake_refused(h,before,result,'knoxx')
    self.assertEqual([{'tag':backend,'image_id':old[backend]}],h.data()['restore_attempts'])
    self.assertEqual(old[backend],h.data()['images'][backend]);self.assertEqual(d['load_ids'][frontend],h.data()['images'][frontend])
    marker=json.loads((h.root/'receiver-intake.json').read_text());restores=[row for row in marker['operations'] if row['kind']=='restore']
    self.assertEqual(1,len(restores));self.assertEqual('uncertain',restores[0]['status']);self.assertIn('changed image ID',marker['first_failure'])
    self.assertTrue(any(row['tag']==frontend for row in marker['recovery_errors']))
 def test_final_marker_removal_sync_failure_recreates_a_refusal_when_storage_allows(self):
  h=self.harness();before=self.protected_bytes(h);result=h.receive('a',finish_fault='recreate')
  self.assert_intake_refused(h,before,result)
  marker=json.loads((h.root/'receiver-intake.json').read_text());self.assertEqual('refused',marker['status'])
  self.assertIn('state persistence failed',marker['first_failure']);self.assertEqual([],h.data()['health'])
 def test_unlink_plus_unavailable_recovery_write_reports_storage_limit_without_start(self):
  h=self.harness();before=self.protected_bytes(h);result=h.receive('a',finish_fault='recovery-write-fails')
  self.assertNotEqual(0,result.returncode);self.assertEqual(before,self.protected_bytes(h));self.assertEqual([],h.data()['health']);self.assertEqual([],h.data()['removal_attempts'])
  self.assertFalse((h.root/'receiver-intake.json').exists(), 'absence after failed storage recovery is an explicit operational limitation')
  self.assertIn('Image intake state persistence failed',result.stderr.decode());self.assertIn('operator review required',result.stderr.decode())
 def test_marker_durability_and_crash_windows_never_automatically_retry(self):
  for fault in ('file','directory','crash-after-marker'):
   with self.subTest(fault=fault):
    h=self.harness();before=self.protected_bytes(h);result=h.receive('a',intake_fault=fault)
    self.assertNotEqual(0,result.returncode);self.assertEqual([],h.data()['calls']);self.assertEqual(before,self.protected_bytes(h))
  for operation in ('crash-after-load','crash-after-restore'):
   with self.subTest(operation=operation):
    h=self.harness();self.generations(h);before=self.protected_bytes(h);d=h.data();d['load_ids']={h.tag('b'):'sha256:'+'e'*64};h.save(d)
    self.assertNotEqual(0,h.receive('b',operation_fault=operation).returncode)
    marker=h.root/'receiver-intake.json';self.assertTrue(marker.exists());retained=marker.read_bytes();calls=h.data()['calls']
    self.assertNotEqual(0,h.receive('c').returncode);self.assertEqual(calls,h.data()['calls']);self.assertEqual(retained,marker.read_bytes());self.assertEqual(before,self.protected_bytes(h))
 def test_normal_load_has_durable_marker_and_success_clears_only_current_marker(self):
  h=self.harness();self.healthy(h,'a');self.assertEqual([True],h.data()['load_markers']);self.assertFalse((h.root/'receiver-intake.json').exists())
 def test_owned_tag_and_old_id_preflight_refuses_before_load(self):
  for mode in ('rebound','missing-id','partial-knoxx-catalog'):
   with self.subTest(mode=mode):
    h=self.harness();service='knoxx' if mode=='partial-knoxx-catalog' else 'axxium';self.healthy(h,'a',service=service)
    d=h.data();tag=sorted(receiver.expected_tags(service,'a'*40))[0]
    if mode=='rebound':d['images'][tag]='sha256:'+'e'*64
    elif mode=='missing-id':d['missing_ids']=[d['images'][tag]]
    else:
     p=h.root/'receiver-images.json';catalog=json.loads(p.read_text());del catalog['owned_tags'][tag];p.write_text(json.dumps(catalog))
    h.save(d);before=self.protected_bytes(h,service);loads=d['load_count'];result=h.receive('a',service=service)
    self.assertNotEqual(0,result.returncode);self.assertEqual(loads,h.data()['load_count']);self.assertEqual(before,self.protected_bytes(h,service));self.assertEqual([],h.data()['removal_attempts'])
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
 def test_failed_first_then_healthy_does_not_adopt_failed_generation_as_rollback(self):
  h=self.harness();d=h.data();d['fail_tags']=[h.tag('a')];h.save(d)
  self.assertNotEqual(0,h.receive('a').returncode)
  self.assertFalse((h.slot()/'release.env').exists())
  self.healthy(h,'b');self.assertFalse((h.slot()/'rollback.env').exists())
  self.assertEqual({'AXXIUM_IMAGE':h.image_id(h.tag('b'))},h.selection(h.slot()/'release.env'))
  self.assertEqual([],h.data()['removal_attempts'])
 def test_repeat_same_healthy_selection_preserves_distinct_rollback(self):
  h=self.harness();self.generations(h);self.healthy(h,'b')
  self.assertEqual({'AXXIUM_IMAGE':h.image_id(h.tag('a'))},h.selection(h.slot()/'rollback.env'))
  self.assertEqual([],h.data()['removed'])
  self.healthy(h,'c');self.assertEqual([h.tag('a')],h.data()['removed'])
  self.assertEqual({'AXXIUM_IMAGE':h.image_id(h.tag('b'))},h.selection(h.slot()/'rollback.env'))
 def test_swapped_knoxx_roles_are_a_distinct_keyed_selection(self):
  h=self.harness();self.healthy(h,'a',service='knoxx');previous=h.selection(h.slot(service='knoxx')/'release.env')
  d=h.data();d['load_ids']={h.tag('b','knoxx-backend'):previous['KNOXX_FRONTEND_IMAGE'],h.tag('b','knoxx-frontend'):previous['KNOXX_BACKEND_IMAGE']};h.save(d)
  self.healthy(h,'b',service='knoxx')
  self.assertEqual(previous,h.selection(h.slot(service='knoxx')/'rollback.env'))
  current=h.selection(h.slot(service='knoxx')/'release.env');self.assertEqual(set(previous.values()),set(current.values()));self.assertNotEqual(previous,current)
 def test_removal_never_starts_without_durable_file_and_directory_barrier(self):
  for fault,count in [('file',1),('directory',2)]:
   with self.subTest(fault=fault):
    h=self.harness();self.generations(h)
    self.assertEqual(3,h.receive('c',barrier_fault=fault).returncode)
    self.assertEqual([],h.data()['removal_attempts'])
    self.assertEqual({'fault':fault,'fsync_calls':count},json.loads((h.root/'barrier-fault.json').read_text()))
    self.assertEqual('healthy',h.receipt()['status']);self.assertEqual('failed',h.receipt()['image_cleanup']['status']);self.assertTrue(h.receipt()['image_cleanup']['refused'])
 def test_unconfirmed_release_after_directory_fsync_failure_refuses_next_admission(self):
  h=self.harness();self.generations(h);receipt=(h.slot()/'deployment.json').read_bytes();rollback=(h.slot()/'rollback.env').read_bytes()
  self.assertNotEqual(0,h.receive('c',release_fault=True).returncode)
  self.assertEqual({'fault':'directory','fsync_calls':2},json.loads((h.root/'release-fault.json').read_text()))
  self.assertEqual(h.image_id(h.tag('c')),h.selection(h.slot()/'release.env')['AXXIUM_IMAGE'])
  calls=h.data()['calls'];self.assertNotEqual(0,h.receive('d').returncode)
  self.assertEqual(calls,h.data()['calls']);self.assertEqual(receipt,(h.slot()/'deployment.json').read_bytes());self.assertEqual(rollback,(h.slot()/'rollback.env').read_bytes())
  self.assertEqual([],h.data()['removal_attempts'])
 def test_release_without_any_healthy_receipt_refuses_admission(self):
  h=self.harness();(h.slot()/'release.env').write_text('AXXIUM_IMAGE='+h.image_id(h.tag('a'))+'\n')
  self.assertNotEqual(0,h.receive('b').returncode);self.assertEqual([],h.data()['calls']);self.assertFalse((h.slot()/'rollback.env').exists())
 def test_keyed_release_must_match_last_healthy_receipt_not_only_its_id_set(self):
  h=self.harness();self.healthy(h,'a',service='knoxx');slot=h.slot(service='knoxx');previous=h.selection(slot/'release.env')
  (slot/'release.env').write_text('KNOXX_BACKEND_IMAGE='+previous['KNOXX_FRONTEND_IMAGE']+'\nKNOXX_FRONTEND_IMAGE='+previous['KNOXX_BACKEND_IMAGE']+'\n')
  calls=h.data()['calls'];self.assertNotEqual(0,h.receive('b',service='knoxx').returncode);self.assertEqual(calls,h.data()['calls']);self.assertFalse((slot/'rollback.env').exists())
 def test_existing_original_healthy_receipt_shape_remains_admissible(self):
  h=self.harness();self.healthy(h,'a');receipt=h.receipt();receipt.pop('image_cleanup',None);(h.slot()/'deployment.json').write_text(json.dumps(receipt))
  self.healthy(h,'b');self.assertEqual({'AXXIUM_IMAGE':h.image_id(h.tag('a'))},h.selection(h.slot()/'rollback.env'))
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
 def test_literal_native_named_untagged_digest_row_allows_other_retirement(self):
  h=self.harness();self.generations(h);d=h.data();d['extra_image_rows']=[NAMED_UNTAGGED_DIGEST_ROW];h.save(d)
  self.healthy(h,'c');self.assertEqual([h.tag('a')],h.data()['removed'])
  self.assertTrue(any(c[:2]==['image','ls'] and '--digests' in c for c in h.data()['calls']))
 def test_nonreceiver_digest_alias_conserves_owned_image(self):
  h=self.harness();self.generations(h);d=h.data();d['extra_image_rows']=[{**NAMED_UNTAGGED_DIGEST_ROW,'ID':h.image_id(h.tag('a'))}];h.save(d)
  self.healthy(h,'c');self.assertEqual([],h.data()['removed'])
  self.assertTrue(any(row['tag']==h.tag('a') and row['reason']=='nonreceiver_alias' for row in h.receipt()['image_cleanup']['retained']))
 def test_malformed_named_untagged_digest_row_refuses_cleanup(self):
  h=self.harness();self.generations(h);d=h.data();d['extra_image_rows']=[{**NAMED_UNTAGGED_DIGEST_ROW,'Digest':'unknown-digest'}];h.save(d)
  self.assertEqual(3,h.receive('c').returncode);self.assertEqual([],h.data()['removal_attempts']);self.assertEqual('blocked',h.receipt()['image_cleanup']['status'])
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
  h=self.harness();self.generations(h);(h.root/'receiver-images.json').write_text('{broken');before=self.protected_bytes(h);calls=h.data()['calls']
  self.assertNotEqual(0,h.receive('c').returncode);self.assertEqual(before,self.protected_bytes(h));self.assertEqual(calls,h.data()['calls']);self.assertEqual([],h.data()['removal_attempts'])
 def test_failed_removal_is_reported_without_health_rollback_or_automatic_retry(self):
  h=self.harness();self.generations(h);d=h.data();d['fail_remove']=True;h.save(d)
  self.assertEqual(3,h.receive('c').returncode);r=h.receipt();self.assertEqual('healthy',r['status']);self.assertEqual('failed',r['image_cleanup']['status']);self.assertEqual([],r['image_cleanup']['removed']);self.assertTrue(r['image_cleanup']['refused'])
  self.assertEqual(h.image_id(h.tag('c')),h.selection(h.slot()/'release.env')['AXXIUM_IMAGE'])
  original_reason=json.loads((h.root/'receiver-images.json').read_text())['refused_tags'][h.tag('a')]
  d=h.data();d['fail_remove']=False;h.save(d)
  self.assertEqual(3,h.receive('d').returncode);self.assertEqual([h.tag('a')],h.data()['removal_attempts'])
  self.assertEqual(original_reason,json.loads((h.root/'receiver-images.json').read_text())['refused_tags'][h.tag('a')])
 def test_existing_uncertain_barrier_keeps_its_original_cause(self):
  h=self.harness();self.generations(h);path=h.root/'receiver-images.json';catalog=json.loads(path.read_text());cause=receiver.REMOVAL_BARRIER
  catalog['refused_tags'][h.tag('a')]=cause;path.write_text(json.dumps(catalog))
  self.assertEqual(3,h.receive('c').returncode);self.assertEqual([],h.data()['removal_attempts'])
  self.assertEqual(cause,json.loads(path.read_text())['refused_tags'][h.tag('a')])
 def test_knoxx_generations_retire_only_both_old_recorded_tags(self):
  h=self.harness()
  for letter in 'abc':self.healthy(h,letter,service='knoxx')
  self.assertEqual(receiver.expected_tags('knoxx','a'*40),set(h.data()['removed']))
  self.assertEqual(2,len(h.selection(h.slot(service='knoxx')/'rollback.env')))

if __name__=='__main__':unittest.main()
