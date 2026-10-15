#!/usr/bin/env python3
"""Read GitHub's current PR/timeline/owner evidence; emit an immutable deployment admission."""
import base64,fnmatch,json,os,sys,re
from datetime import datetime,timezone
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from urllib.parse import quote
from environment_policy import testing_admission,staging_admission,testing_authorization,timestamp

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
def label_identity(event):
 if type(event.get('id')) is not int or event['id']<=0:
  raise ValueError('testing label needs a positive native event ID')
 created_at=timestamp(event.get('created_at')).isoformat()
 actor=event.get('actor') or {}
 if not isinstance(actor,dict):raise ValueError('invalid testing label actor')
 return {'id':event['id'],'created_at':created_at,
  'actor':{key:actor.get(key) for key in ('id','login','type')}}
def testing_claims(repo,number):
 claims=[]
 for other in pages(f'repos/{repo}/pulls?state=open'):
  if other['number']!=number and any(label['name']=='testing' for label in other['labels']):
   event=label_identity(label_evidence(repo,other['number']))
   claims.append({'number':other['number'],'state':'open','testing_label':True,
    'labeled_at':event['created_at'],'label_event_id':event['id']})
 return sorted(claims,key=lambda claim:claim['number'])
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
  for index,part in enumerate(parts):
   last=index==len(parts)-1
   if part=='**':pieces.append('.*' if last else '(?:[^/]+/)*')
   else:
    pieces.append(''.join('[^/]*' if c=='*' else '[^/]' if c=='?' else re.escape(c) for c in part)+('' if last else '/'))
  expression=''.join(pieces)
  prefix='^' if anchored else '(?:^|/)'
  wildcard_leaf=any(c in parts[-1] for c in '*?') and parts[-1]!='**'
  suffix='/' if directory else '$' if wildcard_leaf or parts[-1]=='**' else '(?:/|$)'
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
 try:label_binding=label_identity(label)
 except (TypeError,ValueError):
  return {'admitted':False,'reason':'missing or invalid native testing label evidence'}
 paths=[]
 for f in pages(f'repos/{repo}/pulls/{number}/files'):
  paths.append(f['filename'])
  if f.get('previous_filename'):paths.append(f['previous_filename'])
 now=datetime.now(timezone.utc)
 authorization=testing_authorization(pages(f'repos/{repo}/pulls/{number}/reviews'),label,pr['head']['sha'],now=now)
 candidate={'number':number,'state':pr['state'],'base':pr['base']['ref'],'head_sha':pr['head']['sha'],
  'authorization_sha':authorization['head_sha'] if authorization else None,
  'testing_label':any(l['name']=='testing' for l in pr['labels']),
  'labeled_at':label.get('created_at'),'labeler_is_code_owner':bool(label.get('actor')) and owns_files(repo,label['actor']['login'],paths)}
 try:others=testing_claims(repo,number)
 except (TypeError,ValueError):
  return {'admitted':False,'reason':'missing or invalid competing testing claim evidence'}
 result=testing_admission(candidate,others,now=now)
 if not result['admitted']:return result
 current=api(f'repos/{repo}/pulls/{number}')
 if (current['head']['sha']!=pr['head']['sha'] or current['base']!=pr['base'] or current['state']!=pr['state']
     or not any(value['name']=='testing' for value in current['labels'])):
  return {'admitted':False,'reason':'pull request changed while collecting testing authorization'}
 try:
  current_others=testing_claims(repo,number)
  current_label=label_identity(label_evidence(repo,number))
 except (TypeError,ValueError):
  return {'admitted':False,'reason':'missing or invalid final testing label evidence'}
 if current_others!=others:
  return {'admitted':False,'reason':'competing testing claims changed while collecting authorization'}
 if current_label!=label_binding:
  return {'admitted':False,'reason':'selected testing label changed while collecting authorization'}
 result['testing_authorization']=authorization
 return result
if __name__=='__main__':
 repo,number,environment=sys.argv[1:]
 if repo not in {'open-hax/knoxx','open-hax/axxium'}:raise SystemExit('Service repository not enabled')
 result=admit(repo,int(number),environment)
 print(json.dumps(result))
 if not result['admitted']:raise SystemExit(1)
 if output:=os.environ.get('GITHUB_OUTPUT'):
  with open(output,'a') as f:
   f.write('source_sha='+result['source_sha']+'\n')
