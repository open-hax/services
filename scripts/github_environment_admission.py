#!/usr/bin/env python3
"""Read GitHub's current PR/timeline/owner evidence; emit an immutable deployment admission."""
import base64,fnmatch,json,os,sys,re
from datetime import datetime,timezone
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from urllib.parse import quote
from environment_policy import testing_admission,staging_admission

def api(path):
 req=Request('https://api.github.com/'+path,headers={'Authorization':'Bearer '+os.environ['GITHUB_TOKEN'],'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28'})
 with urlopen(req,timeout=30) as response:return json.load(response)
def pages(path):
 result=[]
 for page in range(1,101):
  rows=api(path+('&' if '?' in path else '?')+f'per_page=100&page={page}')
  result.extend(rows)
  if len(rows)<100:return result
 raise RuntimeError('GitHub pagination exceeded safe bound')
def label_evidence(repo,number):
 events=pages(f'repos/{repo}/issues/{number}/timeline')
 matches=[e for e in events if e.get('event')=='labeled' and e.get('label',{}).get('name')=='testing']
 return matches[-1] if matches else {}
def owns_files(repo,login,files):
 try:
  result=api(f'repos/{repo}/contents/.github/CODEOWNERS?ref=main')
  text=base64.b64decode(result['content']).decode()
 except HTTPError as error:
  if error.code!=404:raise
  return False  # A repository must declare its code owners on main.
 rules=[]
 for line in text.splitlines():
  words=line.split('#',1)[0].split()
  if words:
   if len(words)<2 or any(not x.startswith('@') for x in words[1:]):return False
   if any(c in words[0] for c in ('!', '[', ']', '\\')):return False
   rules.append((words[0],words[1:]))
 def matches(pattern,path):
  # CODEOWNERS is gitignore-like: a single star must not cross a slash.
  # Unsupported negation, ranges and escaped whitespace fail closed above.
  anchored=pattern.startswith('/') or '/' in pattern.rstrip('/')
  directory=pattern.endswith('/')
  pattern=pattern.strip('/')
  parts=pattern.split('/')
  pieces=[]
  for part in parts:
   if part=='**':pieces.append('(?:[^/]+/)*')
   else:
    pieces.append(''.join('[^/]*' if c=='*' else '[^/]' if c=='?' else re.escape(c) for c in part)+'/')
  expression=''.join(pieces).removesuffix('/')
  prefix='^' if anchored else '(?:^|/)'
  suffix='/' if directory else '(?:/|$)'
  return re.search(prefix+expression+suffix,path) is not None
 def owner_matches(owner):
  if owner.lower()=='@'+login.lower():return True
  if '/' not in owner:return False
  org,team=owner[1:].split('/',1)
  try:return api(f'orgs/{quote(org)}/teams/{quote(team)}/memberships/{quote(login)}').get('state')=='active'
  except HTTPError as error:
   if error.code==404:return False
   raise
 for path in files:
  owners=[]
  for pattern,values in rules:
   if matches(pattern,path):owners=values
  if not any(owner_matches(owner) for owner in owners):return False
 return bool(files)
def admit(repo,number,environment):
 pr=api(f'repos/{repo}/pulls/{number}')
 if environment=='staging':
  result=staging_admission({'merged':pr['merged'],'base':pr['base']['ref'],'merge_sha':pr['merge_commit_sha']})
  if result['admitted'] and api(f'repos/{repo}/branches/main')['commit']['sha']!=result['source_sha']:
   return {'admitted':False,'reason':'a newer main commit superseded this staging candidate'}
  return result
 if environment!='testing':raise ValueError('Only PR testing/staging admissions are supported')
 label=label_evidence(repo,number)
 paths=[]
 for f in pages(f'repos/{repo}/pulls/{number}/files'):
  paths.append(f['filename'])
  if f.get('previous_filename'):paths.append(f['previous_filename'])
 candidate={'number':number,'state':pr['state'],'base':pr['base']['ref'],'head_sha':pr['head']['sha'],
  'testing_label':any(l['name']=='testing' for l in pr['labels']),
  'labeled_at':label.get('created_at'),'labeler_is_code_owner':bool(label.get('actor')) and owns_files(repo,label['actor']['login'],paths)}
 others=[]
 for other in pages(f'repos/{repo}/pulls?state=open'):
  if other['number']!=number and any(l['name']=='testing' for l in other['labels']):
   event=label_evidence(repo,other['number'])
   others.append({'number':other['number'],'state':'open','testing_label':True,'labeled_at':event.get('created_at')})
 return testing_admission(candidate,others,now=datetime.now(timezone.utc))
if __name__=='__main__':
 repo,number,environment=sys.argv[1:]
 if repo not in {'open-hax/knoxx','open-hax/axxium'}:raise SystemExit('Service repository not enabled')
 result=admit(repo,int(number),environment)
 print(json.dumps(result))
 if not result['admitted']:raise SystemExit(1)
 if output:=os.environ.get('GITHUB_OUTPUT'):
  with open(output,'a') as f:
   f.write('source_sha='+result['source_sha']+'\n')
