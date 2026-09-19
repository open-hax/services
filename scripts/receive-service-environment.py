#!/usr/bin/env python3
"""Forced-command OCI receiver. Install root-owned; each SSH key fixes one slot.

Only image bytes cross this boundary. Compose, mounts, secrets and commands are
installed by the operator; candidate source never supplies a deployment script.
"""
import fcntl,json,os,re,shlex,subprocess,sys,tarfile,tempfile,time
from pathlib import Path

ROOT=Path('/srv/open-hax/environments')
MAX_ARCHIVE=4*1024**3

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
  with tempfile.TemporaryDirectory(prefix='incoming-',dir=ROOT) as scratch:
   image_file=Path(scratch)/'images.tar';total=0
   with image_file.open('wb') as output:
    while chunk:=sys.stdin.buffer.read(1024*1024):
     total+=len(chunk)
     if total>MAX_ARCHIVE:raise ValueError('Image archive exceeds 4 GiB budget')
     output.write(chunk)
   validate_archive(image_file,service,sha)
   subprocess.run(['docker','load','--input',str(image_file)],check=True)
   names=['AXXIUM_IMAGE'] if service=='axxium' else ['KNOXX_BACKEND_IMAGE','KNOXX_FRONTEND_IMAGE']
   images=[f'promethean-axxium:{sha}'] if service=='axxium' else [f'promethean-knoxx-backend:{sha}',f'promethean-knoxx-frontend:{sha}']
   # Pin the loaded image IDs: tags cannot change what compose starts later.
   values={key:subprocess.check_output(['docker','image','inspect','--format','{{.Id}}',tag],text=True).strip() for key,tag in zip(names,images)}
   release=slot/'release.env'; previous=release.read_bytes() if release.exists() else None
   staged=slot/'release.next';staged.write_text(''.join(f'{key}={value}\n' for key,value in values.items()));staged.chmod(0o600);staged.replace(release)
   compose=['docker','compose','--project-name',f'{service}-{environment}','--env-file',str(slot/'.env'),'--env-file',str(release),'-f',str(slot/'compose.yaml')]
   try:
    subprocess.run(compose+['up','-d','--wait','--wait-timeout','180','--pull','never'],cwd=slot,check=True,timeout=240)
   except Exception:
    if previous is not None:
     release.write_bytes(previous)
     subprocess.run(compose+['up','-d','--wait','--wait-timeout','180','--pull','never'],cwd=slot,check=True,timeout=240)
    else:
     # Preserve database volumes and its state on a first failed application start.
     subprocess.run(compose+['stop']+(['axxium'] if service=='axxium' else ['backend','frontend']),cwd=slot,check=True)
    raise
   receipt={'environment':environment,'service':service,'source_sha':sha,'images':values,'deployed_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'status':'healthy'}
   (slot/'deployment.json').write_text(json.dumps(receipt,indent=2)+'\n');print(json.dumps(receipt))
if __name__=='__main__':main()
