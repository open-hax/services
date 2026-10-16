#!/usr/bin/env python3
"""Forced-command OCI receiver. Install root-owned; each SSH key fixes one slot.

Only image bytes cross this boundary. Compose, mounts, secrets and commands are
installed by the operator; candidate source never supplies a deployment script.
"""
import fcntl,json,os,re,shlex,subprocess,sys,tarfile,tempfile,time
from pathlib import Path

ROOT=Path('/srv/open-hax/environments')
MAX_ARCHIVE=4*1024**3
IMAGE_ID=re.compile(r'sha256:[0-9a-f]{64}')
OWNED_TAG=re.compile(r'promethean-(?:axxium|knoxx-backend|knoxx-frontend):[0-9a-f]{40}')
REMOVAL_BARRIER='Removal in progress or uncertain; operator review required'

class RetentionBlocked(ValueError):pass

def image_names(service):
 return ['AXXIUM_IMAGE'] if service=='axxium' else ['KNOXX_BACKEND_IMAGE','KNOXX_FRONTEND_IMAGE']

def sync_directory(path):
 descriptor=os.open(path,os.O_RDONLY|os.O_DIRECTORY)
 try:os.fsync(descriptor)
 finally:os.close(descriptor)

def atomic_write(path,text):
 staged=path.with_name(path.name+'.next')
 try:
  with staged.open('w') as output:
   os.fchmod(output.fileno(),0o600);output.write(text);output.flush();os.fsync(output.fileno())
  staged.replace(path);sync_directory(path.parent)
 finally:staged.unlink(missing_ok=True)

def read_ownership():
 path=ROOT/'receiver-images.json'
 if not path.exists():return {'version':1,'owned_tags':{},'refused_tags':{}}
 if path.is_symlink() or not path.is_file():raise RetentionBlocked('Invalid ownership inventory')
 try:data=json.loads(path.read_text())
 except (ValueError,OSError):raise RetentionBlocked('Unreadable ownership inventory')
 if not isinstance(data,dict) or type(data.get('version')) is not int or data['version']!=1 or not isinstance(data.get('owned_tags'),dict) or not isinstance(data.get('refused_tags'),dict):
  raise RetentionBlocked('Malformed ownership inventory')
 if any(not OWNED_TAG.fullmatch(tag) or not isinstance(value,str) or not IMAGE_ID.fullmatch(value) for tag,value in data['owned_tags'].items()):
  raise RetentionBlocked('Malformed owned image reference')
 if any(tag not in data['owned_tags'] or not isinstance(reason,str) or not reason for tag,reason in data['refused_tags'].items()):
  raise RetentionBlocked('Malformed refused image reference')
 return data

def save_ownership(data):atomic_write(ROOT/'receiver-images.json',json.dumps(data,indent=2)+'\n')

def selection_values(text,service):
 try:
  rows=[line.split('=') for line in text.splitlines()]
  if any(len(row)!=2 for row in rows):raise ValueError()
  values=dict(rows)
  if len(values)!=len(rows) or set(values)!=set(image_names(service)) or any(not IMAGE_ID.fullmatch(value) for value in values.values()):raise ValueError()
  return values
 except ValueError:raise RetentionBlocked('Malformed slot image selection')

def selection_ids(path,service):
 if path.is_symlink() or not path.is_file():raise RetentionBlocked('Unavailable slot image selection')
 try:return set(selection_values(path.read_text(),service).values())
 except OSError:raise RetentionBlocked('Unavailable slot image selection')

def admitted_selection(slot,environment,service):
 """A replaced release file alone never establishes a healthy generation."""
 release=slot/'release.env';receipt=slot/'deployment.json';rollback=slot/'rollback.env'
 present=lambda path:path.exists() or path.is_symlink()
 if not any(present(path) for path in (release,receipt,rollback)):return None
 if any(path.is_symlink() or not path.is_file() for path in (release,receipt)):
  raise RetentionBlocked('Active selection lacks matching last healthy evidence')
 try:
  previous=release.read_bytes();values=selection_values(previous.decode(),service);health=json.loads(receipt.read_text())
  if (not isinstance(health,dict) or health.get('status')!='healthy' or health.get('environment')!=environment or
      health.get('service')!=service or not isinstance(health.get('source_sha'),str) or not re.fullmatch('[0-9a-f]{40}',health['source_sha']) or
      health.get('images')!=values):raise ValueError()
  if present(rollback):selection_ids(rollback,service)
  return previous
 except (ValueError,OSError):raise RetentionBlocked('Active selection lacks matching last healthy evidence')

def docker_output(args):
 try:return subprocess.check_output(['docker']+args,text=True,stderr=subprocess.PIPE,timeout=30)
 except (subprocess.SubprocessError,OSError):raise RetentionBlocked('Docker reference inventory unavailable')

def reference_inventory():
 """Read IDs/tags only: never inspect container configuration or slot secrets."""
 protected=set()
 for environment in ('testing','staging'):
  for service in ('knoxx','axxium'):
   slot=ROOT/environment/service
   if slot.is_symlink() or not slot.is_dir():raise RetentionBlocked('Deployment slot inventory unavailable')
   active=slot/'release.env';rollback=slot/'rollback.env'
   if active.exists() or active.is_symlink():protected.update(selection_ids(active,service))
   elif rollback.exists() or (slot/'deployment.json').exists():raise RetentionBlocked('Active slot selection missing')
   if rollback.exists() or rollback.is_symlink():protected.update(selection_ids(rollback,service))
 aliases={};tags={}
 try:
  for line in docker_output(['image','ls','--all','--digests','--no-trunc','--format','{{json .}}']).splitlines():
   row=json.loads(line);image=row['ID'];repository=row['Repository'];tag=row['Tag'];digest=row['Digest']
   if not isinstance(image,str) or not IMAGE_ID.fullmatch(image) or not isinstance(repository,str) or not repository or not isinstance(tag,str) or not tag or not isinstance(digest,str):raise ValueError()
   if digest!='<none>' and not IMAGE_ID.fullmatch(digest):raise ValueError()
   aliases.setdefault(image,set())
   if repository=='<none>':
    if tag!='<none>' or digest!='<none>':raise ValueError()
    continue
   # A digest-pinned pull can have a repository and digest without a tag.
   # Registry digest names are nonreceiver aliases and conserve the image ID.
   if digest!='<none>':aliases[image].add(repository+'@'+digest)
   if tag=='<none>':
    if digest=='<none>':raise ValueError()
    continue
   name=repository+':'+tag
   if name in tags and tags[name]!=image:raise ValueError()
   tags[name]=image;aliases[image].add(name)
  containers=docker_output(['container','ls','--all','--quiet','--no-trunc']).splitlines()
  if len(containers)!=len(set(containers)) or any(not re.fullmatch('[0-9a-f]{64}',value) for value in containers):raise ValueError()
  used=set()
  for start in range(0,len(containers),100):
   batch=containers[start:start+100]
   images=docker_output(['container','inspect','--format','{{.Image}}']+batch).splitlines()
   if len(images)!=len(batch) or any(not IMAGE_ID.fullmatch(value) for value in images):raise ValueError()
   used.update(images)
 except (ValueError,KeyError,TypeError):raise RetentionBlocked('Malformed Docker reference inventory')
 return protected,used,aliases,tags

def cleanup_owned_images(catalog,candidates):
 """Called only after health, under the global receiver lock; remove tags only."""
 result={'status':'complete','retained':[],'removed':[],'refused':[]}
 def persist():
  try:save_ownership(catalog);return True
  except OSError:
   result['status']='failed';result['error']='Retention state persistence failed; operator review required'
   return False
 def refuse(tags,reason,status='blocked'):
  result['status']=status
  for index,tag in enumerate(tags):
   observation=('Not attempted after earlier '+reason if status=='failed' and index else reason)
   original=catalog['refused_tags'].get(tag)
   if original is None or (original==REMOVAL_BARRIER and status=='failed'):catalog['refused_tags'][tag]=observation
   record={'tag':tag,'reason':catalog['refused_tags'][tag]}
   if record['reason']!=observation:record['observation']=observation
   result['refused'].append(record)
  persist()
  return result
 # A failed/uncertain removal is never retried by a later deployment.
 if any(tag in catalog['refused_tags'] for tag in candidates):
  return refuse(candidates,'Prior cleanup refusal requires operator review')
 for index,tag in enumerate(candidates):
  try:
   protected,used,aliases,tags=reference_inventory();image=catalog['owned_tags'][tag]
   if tags.get(tag)!=image:raise RetentionBlocked('Owned tag is missing or rebound')
   reason=('active_or_rollback' if image in protected else 'container_reference' if image in used else
           'nonreceiver_alias' if any(alias not in catalog['owned_tags'] or catalog['owned_tags'][alias]!=image for alias in aliases.get(image,set())) else None)
   if reason:
    result['retained'].append({'tag':tag,'image_id':image,'reason':reason});continue
   # Recheck the exact source-SHA tag immediately before its nonforce removal.
   if not OWNED_TAG.fullmatch(tag) or docker_output(['image','inspect','--format','{{.Id}}',tag]).strip()!=image:
    raise RetentionBlocked('Owned tag changed before removal')
  except RetentionBlocked as error:return refuse(candidates[index:],str(error))
  # Persist refusal before the destructive boundary. A crash or ambiguous
  # command result leaves this barrier in place, so a later deploy cannot retry.
  catalog['refused_tags'][tag]=REMOVAL_BARRIER
  if not persist():
   result['refused'].append({'tag':tag,'reason':'Removal was not attempted: refusal barrier could not be persisted'})
   return result
  try:
   subprocess.run(['docker','image','rm',tag],check=True,timeout=30,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
  except (subprocess.SubprocessError,OSError):return refuse(candidates[index:],'Removal failed or outcome uncertain','failed')
  result['removed'].append(tag);del catalog['owned_tags'][tag];del catalog['refused_tags'][tag]
  if not persist():return result
 return result

def retain_after_health(slot,previous,loaded):
 """Advance rollback only for healthy admission; never adopt old Docker tags."""
 try:
  current=selection_values((slot/'release.env').read_text(),slot.name)
  if previous is not None:
   # Re-admitting an identical keyed selection does not consume the distinct
   # last healthy rollback. The same ID set with swapped roles is different.
   if selection_values(previous.decode(),slot.name)!=current:atomic_write(slot/'rollback.env',previous.decode())
  elif (slot/'rollback.env').exists():raise RetentionBlocked('Rollback exists without previous active generation')
  catalog=read_ownership();candidates=sorted(set(catalog['owned_tags'])-set(loaded))
  for tag,image in loaded.items():
   if not OWNED_TAG.fullmatch(tag) or not IMAGE_ID.fullmatch(image):raise RetentionBlocked('Invalid loaded image reference')
   if tag in catalog['owned_tags'] and catalog['owned_tags'][tag]!=image:raise RetentionBlocked('Recorded source-SHA tag changed image ID')
  catalog['owned_tags'].update(loaded);save_ownership(catalog)
  result=cleanup_owned_images(catalog,candidates)
  result['retained'].extend({'tag':tag,'image_id':image,'reason':'active_healthy'} for tag,image in loaded.items())
  return result
 except RetentionBlocked as error:
  return {'status':'blocked','retained':[],'removed':[],'refused':[{'tag':tag,'reason':str(error)} for tag in loaded]}
 except (OSError,ValueError):
  return {'status':'failed','retained':[],'removed':[],'refused':[{'tag':tag,'reason':'Retention state persistence failed; operator review required'} for tag in loaded]}

def expected_tags(service,sha):
 return {f'promethean-axxium:{sha}'} if service=='axxium' else {f'promethean-knoxx-backend:{sha}',f'promethean-knoxx-frontend:{sha}'}
def validate_archive(path,service,sha):
 with tarfile.open(path,'r:') as archive:
  members=archive.getmembers()
  if any(m.name.startswith('/') or '..' in Path(m.name).parts or m.issym() or m.islnk() for m in members):
   raise ValueError('Unsafe image archive member')
  manifest=archive.getmember('manifest.json')
  if manifest.size>1024*1024:raise ValueError('Oversized image manifest')
  rows=json.load(archive.extractfile(manifest))
  tags=[tag for row in rows for tag in row.get('RepoTags',[])]
  if set(tags)!=expected_tags(service,sha) or len(tags)!=len(set(tags)) or len(rows)!=len(tags):
   raise ValueError('Archive must contain only the admitted image tags')

def load_images(path,environment,service,sha):
 """Import once; a rebuilt owned name never changes an admitted selection.

 The durable marker is a refusal barrier, not permission to replay a command.
 All refused/uncertain imports keep their original evidence for operator review.
 """
 marker=ROOT/'receiver-intake.json'
 if marker.exists() or marker.is_symlink():raise RetentionBlocked('Prior image intake requires operator review: '+str(marker))
 tags=sorted(expected_tags(service,sha));catalog=read_ownership()
 prior={tag:catalog['owned_tags'][tag] for tag in tags if tag in catalog['owned_tags']}
 if prior and set(prior)!=set(tags):raise RetentionBlocked('Incomplete owned image set; refusing image intake')
 for tag,image in prior.items():
  if docker_output(['image','inspect','--format','{{.Id}}',tag]).strip()!=image:
   raise RetentionBlocked('Owned tag is missing or rebound before image intake')
  if docker_output(['image','inspect','--format','{{.Id}}',image]).strip()!=image:
   raise RetentionBlocked('Prior owned image ID unavailable before image intake')
 journal={'schema':'promethean.receiver-intake/v1','status':'in_progress',
          'environment':environment,'service':service,'source_sha':sha,'prior_tags':prior,
          'operations':[],'first_failure':None,'recovery_errors':[]}
 def save():atomic_write(marker,json.dumps(journal,indent=2)+'\n')
 def failure(reason):
  if journal['first_failure'] is None:journal['first_failure']=reason
 def effect(kind,args,timeout,tag=None,image=None):
  operation={'kind':kind,'status':'pending'}
  if tag is not None:operation.update(tag=tag,image_id=image)
  journal['operations'].append(operation);save()
  try:
   subprocess.run(['docker']+args,check=True,timeout=timeout,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
  except (subprocess.SubprocessError,OSError) as error:
   uncertain=isinstance(error,(subprocess.TimeoutExpired,OSError))
   operation['status']='uncertain' if uncertain else 'failed'
   if isinstance(error,subprocess.CalledProcessError):operation['exit_code']=error.returncode
   reason=f'Image {kind} '+('outcome uncertain' if uncertain else 'failed')+': '+type(error).__name__
   failure(reason);save();return reason,uncertain
  operation['status']='succeeded';save();return None,False
 try:
  reason,_=effect('load',['load','--input',str(path)],120)
  loaded={}
  if reason is None:
   try:
    for tag in tags:
     image=docker_output(['image','inspect','--format','{{.Id}}',tag]).strip()
     if not IMAGE_ID.fullmatch(image):raise RetentionBlocked('Malformed loaded image ID')
     loaded[tag]=image
    journal['loaded_tags']=loaded
    if any(loaded[tag]!=image for tag,image in prior.items()):
     failure('Recorded source-SHA tag changed image ID during image intake')
   except RetentionBlocked as error:failure('Post-load inspection failed: '+str(error))
  if journal['first_failure'] is not None:
   # Restore only changed/missing/unknown bindings, each at most once. A native
   # uncertainty stops subsequent effects; readback cannot authorize a retry.
   for tag,image in prior.items():
    try:
     if docker_output(['image','inspect','--format','{{.Id}}',tag]).strip()==image:continue
    except RetentionBlocked:pass
    error,uncertain=effect('restore',['image','tag',image,tag],30,tag,image)
    if error:journal['recovery_errors'].append({'tag':tag,'reason':error})
    if uncertain:break
   for tag,image in prior.items():
    try:
     if docker_output(['image','inspect','--format','{{.Id}}',tag]).strip()!=image:
      raise RetentionBlocked('Prior image binding was not restored')
    except RetentionBlocked as error:journal['recovery_errors'].append({'tag':tag,'reason':str(error)})
   journal['status']='refused';save()
   raise RetentionBlocked(journal['first_failure']+'; intake held for operator review: '+str(marker))
  journal['status']='verified';save()
  marker.unlink();sync_directory(ROOT)
  ordered=[f'promethean-axxium:{sha}'] if service=='axxium' else [f'promethean-knoxx-backend:{sha}',f'promethean-knoxx-frontend:{sha}']
  return {key:loaded[tag] for key,tag in zip(image_names(service),ordered)}
 except OSError as error:
  failure('Image intake state persistence failed: '+type(error).__name__)
  journal['status']='refused'
  # Preserve the first cause even when a later persistence operation also fails.
  # A failed completion unlink may already have removed the marker. Recreate
  # it where storage allows; if this also fails, durable refusal is unavailable
  # and the failed job requires an operator stop (never infer success from absence).
  try:save()
  except OSError:pass
  raise RetentionBlocked(journal['first_failure']+'; operator review required: '+str(marker)) from error

def main():
 environment,service=sys.argv[1:]
 if environment not in {'testing','staging'} or service not in {'knoxx','axxium'}:raise ValueError('Unsupported slot')
 command=shlex.split(os.environ.get('SSH_ORIGINAL_COMMAND',''))
 if len(command)!=4 or command[:3]!=['receive',environment,service] or not re.fullmatch('[0-9a-f]{40}',command[3]):
  raise ValueError('This key only accepts an image archive for its fixed deployment slot')
 sha=command[3]; slot=ROOT/environment/service
 # Keep image loads and runtime changes serialized even across different keys.
 with (ROOT/'.receiver.lock').open('a') as lock:
  fcntl.flock(lock,fcntl.LOCK_EX)
  intake=ROOT/'receiver-intake.json'
  if intake.exists() or intake.is_symlink():raise RetentionBlocked('Prior image intake requires operator review: '+str(intake))
  # Refuse unknown/crash residue before loading images, changing a selection,
  # attempting health or considering cleanup. Recovery is an operator decision.
  previous=admitted_selection(slot,environment,service)
  with tempfile.TemporaryDirectory(prefix='incoming-',dir=ROOT) as scratch:
   image_file=Path(scratch)/'images.tar';total=0
   with image_file.open('wb') as output:
    while chunk:=sys.stdin.buffer.read(1024*1024):
     total+=len(chunk)
     if total>MAX_ARCHIVE:raise ValueError('Image archive exceeds 4 GiB budget')
     output.write(chunk)
   validate_archive(image_file,service,sha)
   values=load_images(image_file,environment,service,sha)
   images=[f'promethean-axxium:{sha}'] if service=='axxium' else [f'promethean-knoxx-backend:{sha}',f'promethean-knoxx-frontend:{sha}']
   # Pin verified IDs only after the complete import transaction succeeds.
   release=slot/'release.env'
   atomic_write(release,''.join(f'{key}={value}\n' for key,value in values.items()))
   compose=['docker','compose','--project-name',f'{service}-{environment}','--env-file',str(slot/'.env'),'--env-file',str(release),'-f',str(slot/'compose.yaml')]
   try:
    subprocess.run(compose+['up','-d','--wait','--wait-timeout','180','--pull','never'],cwd=slot,check=True,timeout=240)
   except Exception:
    if previous is not None:
     atomic_write(release,previous.decode())
     subprocess.run(compose+['up','-d','--wait','--wait-timeout','180','--pull','never'],cwd=slot,check=True,timeout=240)
    else:
     # Preserve database volumes and its state on a first failed application start.
     try:subprocess.run(compose+['stop']+(['axxium'] if service=='axxium' else ['backend','frontend']),cwd=slot,check=True,timeout=120)
     finally:
      # No healthy generation existed before this attempt. Do not let the
      # failed selection become a later deployment's purported rollback.
      release.unlink(missing_ok=True);sync_directory(slot)
    raise
   cleanup=retain_after_health(slot,previous,dict(zip(images,values.values())))
   receipt={'environment':environment,'service':service,'source_sha':sha,'images':values,'deployed_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'status':'healthy','image_cleanup':cleanup}
   atomic_write(slot/'deployment.json',json.dumps(receipt,indent=2)+'\n');print(json.dumps(receipt))
   return 0 if cleanup['status']=='complete' else 3
if __name__=='__main__':raise SystemExit(main())
