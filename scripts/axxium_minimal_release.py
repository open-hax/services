#!/usr/bin/env python3
"""Bounded Axxium-only production transition under Services' canonical queue.

The workflow must admit the source/artifact and real production qualification
before invoking --apply. This controller preserves the incumbent private env,
database and PDS, and never builds/loads an image or restores production data.
There is no automatic retry after a journal exists or a command outcome is
uncertain. The separate protected-receiver proposal is not required or claimed.
"""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request

from axxium_oidc_ingress import maintenance, prepare

RUNTIME = Path('/srv/open-hax/services/axxium')
STATE = Path('/srv/open-hax/state/axxium')
CADDY = Path('/srv/open-hax/services/caddy/Caddyfile')
MANAGED = Path('/srv/open-hax/state/caddy/managed')
LOCK = RUNTIME/'.axxium-production.lock'
PENDING = RUNTIME/'.axxium-production-pending'
APP, DB, PDS, PROXY = 'axxium-axxium-1', 'axxium-axxium-db-1', 'axxium-pds-1', 'caddy-caddy-1'
SOURCE = '58234df96ca86507e6cdd87f65aaa9ab35621415'
IMAGE = 'sha256:6aed20a3439dc29c6bcb43cf3f1e0bf41f65a72734975eb2db922898c45e4652'
ISSUER = 'https://axxium.promethean.rest/oidc'


class Refused(RuntimeError): pass
class KnownFailure(Refused): pass
class Uncertain(Refused): pass


def digest(data): return hashlib.sha256(data).hexdigest()


def file_digest(path):
    with path.open('rb') as source: return hashlib.file_digest(source, 'sha256').hexdigest()


def controlled_file(path,*,secret=False):
    info=path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid!=1000 or info.st_nlink!=1
            or info.st_mode&0o002 or secret and stat.S_IMODE(info.st_mode)!=0o600):
        raise Refused('Existing deployment file protection changed')
    return info


def rehearsal_environment(original,database,proxy_ip):
    """Use only the fixed discrete DB configuration of the selected source.

    The 58234 config reads DB_HOST/PORT/NAME/USER/PASSWORD. Refuse alternate
    libpq/URL inputs rather than allowing an older image to reinterpret them.
    Docker run's env-file is literal; values come from the incumbent process,
    not Compose's dollar-escaped configuration projection.
    """
    alternate={'DATABASE_URL','DB_URL','PGHOST','PGDATABASE','PGUSER','PGPASSWORD',
               'PGPORT','PGSERVICE','PGSERVICEFILE','PGPASSFILE'}
    if (not re.fullmatch('axxium_verify_[0-9a-f]{24}',database)
            or any(original.get(key) for key in alternate)
            or any(original.get(k)!=v for k,v in {'DB_HOST':'axxium-db','DB_PORT':'5432',
                    'DB_NAME':'axxium','DB_USER':'axxium'}.items())
            or not original.get('DB_PASSWORD')):
        raise Refused('Rehearsal cannot be isolated from production database selection')
    values={k:v for k,v in original.items() if k not in ('PATH','NODE_VERSION','YARN_VERSION')}
    values.update(DB_NAME=database,AXXIUM_OIDC_CONFIG_FILE='/run/axxium/oidc/authority.json',AXXIUM_TRUST_PROXY=proxy_ip)
    if any('\n' in value or '\r' in value for value in values.values()):
        raise Refused('Unrepresentable private rehearsal environment')
    return values


def private_read(path, *, maximum=1048576):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) != 0o600 or info.st_nlink != 1
                or info.st_size > maximum): raise Refused('Private input protection refused')
        with os.fdopen(fd, 'rb', closefd=False) as stream: return stream.read(maximum+1)
    finally: os.close(fd)


def validate_plan(plan):
    keys = {'schema','host','source_sha','image_id','stage_run_id','artifact_id','artifact_sha256',
            'source_pr','stage_workflow_path','stage_controller_sha','expected','authority'}
    expected = {'app_container_id','app_image_id','app_environment_sha256','db_container_id',
                'db_image_id','db_environment_sha256','pds_container_id','pds_image_id',
                'pds_environment_sha256','caddy_container_id','caddy_image_id','compose_sha256',
                'environment_file_sha256','caddyfile_sha256','managed_caddy_sha256','caddy_proxy_ip',
                'app_writable_layer_sha256'}
    if (not isinstance(plan,dict) or set(plan)!=keys or plan['schema']!='axxium-minimal-production/v1'
            or plan['host']!='knoxx.promethean.rest' or plan['source_sha']!=SOURCE
            or plan['image_id']!=IMAGE or plan['source_pr']!=37
            or plan['stage_run_id']!=40905644420 or plan['artifact_id']!=12911496785
            or plan['stage_workflow_path']!='.github/workflows/service-environment-staging.yml'
            or not re.fullmatch('[0-9a-f]{40}',plan.get('stage_controller_sha',''))
            or not re.fullmatch('[0-9a-f]{64}',plan.get('artifact_sha256',''))
            or not isinstance(plan['expected'],dict) or set(plan['expected'])!=expected
            or plan['authority']!={'issuer':ISSUER,'client_id':'knoxx-colab',
                'callback_uri':'https://knoxx.promethean.rest/colab/auth/callback'}):
        raise Refused('Unreviewed Axxium production plan')
    for key,value in plan['expected'].items():
        if key=='caddy_proxy_ip':
            try:ipaddress.IPv4Address(value)
            except (ValueError,TypeError):raise Refused('Invalid observed proxy address') from None
            continue
        pattern = 'sha256:[0-9a-f]{64}' if key.endswith('_image_id') else '[0-9a-f]{64}'
        if not isinstance(value,str) or not re.fullmatch(pattern,value):
            raise Refused('Invalid incumbent identity in plan')
    return plan


def execute(plan, host):
    """Order the native transaction; uncertain effects never authorize rollback."""
    validate_plan(plan)
    with host.lock():
        host.preflight(plan)
        host.claim(plan)
        candidate_attempted = False
        stopped = False
        def step(name, action):
            host.record(name,'pending')
            try:
                action()
            except KnownFailure:
                host.record(name,'known_failure')
                raise
            except BaseException:
                # Recording may itself be uncertain. No further runtime effect
                # is permitted if the pending/success publication fails.
                raise
            host.record(name,'succeeded')
        try:
            step('fence_public_api',lambda:host.fence(plan))
            stopped = True
            step('stop_incumbent',lambda:host.stop_app(plan))
            step('verified_before_backup',lambda:host.backup(plan,'before'))
            step('candidate_rollback_rehearsal',lambda:host.rehearse(plan,'before',candidate=True))
            candidate_attempted = True
            step('start_candidate',lambda:host.start_candidate(plan))
            step('private_candidate_health',lambda:host.private_health(plan))
            step('stop_candidate_for_after_backup',lambda:host.stop_app(plan))
            step('verified_after_backup',lambda:host.backup(plan,'after'))
            step('current_state_rollback_rehearsal',lambda:host.rehearse(plan,'after',candidate=False))
            step('restart_verified_candidate',lambda:host.start_candidate(plan))
            step('final_identity_and_state',lambda:host.final_check(plan))
            step('publish_oidc_route',lambda:host.publish(plan))
            step('public_oidc_health',lambda:host.public_health(plan))
            step('healthy_receipt',lambda:host.commit(plan))
            host.record('transaction','healthy')
            host.finish(plan,'healthy')
            return host.receipt('healthy')
        except KnownFailure:
            # A native rollback independently observes the actual image/state,
            # captures current data and rehearses the old image against it.
            step('rollback',lambda:host.rollback(plan,stopped,candidate_attempted))
            host.record('transaction','rolled_back')
            host.finish(plan,'rolled_back')
            return host.receipt('rolled_back')


class NativeHost:
    def __init__(self,plan_digest=None):
        self.operation = None; self.sequence = 0; self.uncertain = False
        self.lock_fd = None; self.lock_identity = None
        self.original = None; self.backups = {}; self.before_data = None
        self.plan_digest=plan_digest
        self.pending_bytes=None
        self.candidate_id=None
        self.incumbent_image_id=None;self.rollback_id=None

    def command(self,args,*,data=None,timeout=60,output=None,effect=False):
        try:
            result=subprocess.run(args,input=data,stdout=output or subprocess.PIPE,
                                  stderr=subprocess.PIPE,timeout=timeout)
        except (OSError,subprocess.TimeoutExpired):
            self.uncertain=True; raise Uncertain('Native command outcome unavailable') from None
        if result.returncode:
            if effect:
                self.uncertain=True;raise Uncertain('Native mutator returned unsuccessfully; actual effect requires review')
            raise KnownFailure('Native observation failed; private diagnostics suppressed')
        return result.stdout if output is None else b''

    def inspect(self,name):
        try:return json.loads(self.command(['docker','inspect',name]))[0]
        except (ValueError,IndexError,TypeError):raise Refused('Native inventory unavailable') from None

    def recheck_lock(self):
        if self.uncertain:raise Uncertain('Transaction uncertainty requires operator recovery')
        info=os.fstat(self.lock_fd);current=LOCK.lstat()
        if ((info.st_dev,info.st_ino)!=(current.st_dev,current.st_ino)
                or (info.st_dev,info.st_ino)!=self.lock_identity
                or not stat.S_ISREG(current.st_mode) or current.st_nlink!=1
                or current.st_uid not in (0,os.getuid()) or current.st_mode&0o022):
            self.uncertain=True;raise Uncertain('Axxium deployment lock identity changed')
        if self.pending_bytes is not None:
            try:current=private_read(PENDING)
            except (OSError,Refused):self.uncertain=True;raise Uncertain('Production refusal marker unavailable') from None
            if current!=self.pending_bytes:
                self.uncertain=True;raise Uncertain('Production refusal marker changed')

    @contextmanager
    def lock(self):
        # This cooperating lock is deployment-account owned. The canonical
        # GitHub queue serializes other production scopes; manual/external
        # Docker or ingress writers are not made exclusive by this file.
        self.lock_fd=os.open(LOCK,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW|os.O_CLOEXEC,0o600)
        try:
            info=os.fstat(self.lock_fd);self.lock_identity=(info.st_dev,info.st_ino)
            if stat.S_IMODE(info.st_mode)!=0o600:raise Refused('Private deployment lock mode refused')
            os.fsync(self.lock_fd)
            self.sync_directory(LOCK.parent)
            self.recheck_lock()
            try:fcntl.flock(self.lock_fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise Refused('Axxium deployment already in progress') from None
            yield
        finally:os.close(self.lock_fd);self.lock_fd=None

    def unchanged_dependencies(self,plan):
        self.recheck_lock();expected=plan['expected']
        controlled_file(RUNTIME/'compose.yaml');controlled_file(RUNTIME/'.env',secret=True)
        controlled_file(CADDY)
        for name,prefix in ((DB,'db'),(PDS,'pds'),(PROXY,'caddy')):
            d=self.inspect(name)
            if (d['Id']!=expected[prefix+'_container_id'] or d['Image']!=expected[prefix+'_image_id']
                    or d['State']['Status']!='running'
                    or d['State'].get('Health',{}).get('Status')!='healthy'):
                raise Uncertain('A preserved dependency changed or is not healthy')
            if prefix in ('db','pds') and self.environment_hash(d)!=expected[prefix+'_environment_sha256']:
                raise Uncertain('A preserved dependency environment changed')
        if file_digest(RUNTIME/'compose.yaml')!=expected['compose_sha256'] or file_digest(RUNTIME/'.env')!=expected['environment_file_sha256']:
            raise Uncertain('Private incumbent configuration changed')
        if self.managed_digest()!=expected['managed_caddy_sha256']:
            raise Uncertain('Unrelated managed ingress changed')

    @staticmethod
    def environment_hash(container):
        return digest(json.dumps(sorted(container['Config'].get('Env') or []),separators=(',',':')).encode())

    @staticmethod
    def managed_digest():
        rows=[]
        for p in sorted(MANAGED.glob('*.caddy')):
            if p.is_symlink() or not p.is_file():raise Refused('Unexpected managed ingress input')
            rows.append({'name':p.name,'sha256':file_digest(p)})
        return digest(json.dumps(rows,sort_keys=True,separators=(',',':')).encode())

    def identity_check(self):
        self.command([sys.executable,str(Path(__file__).with_name('provision_axxium_colab_identity.py')),'--check'])

    def preflight(self,plan):
        if os.getuid()!=1000:raise Refused('The canonical deploy UID1000 is required')
        try:PENDING.lstat()
        except FileNotFoundError:pass
        else:raise Refused('Production operation requires independent recovery')
        self.unchanged_dependencies(plan);self.identity_check()
        old=self.inspect(APP);e=plan['expected']
        if (old['Id']!=e['app_container_id'] or old['Image']!=e['app_image_id']
                or old['State'].get('Health',{}).get('Status')!='healthy'
                or self.environment_hash(old)!=e['app_environment_sha256']):
            raise Refused('Incumbent application differs from reviewed observation')
        self.command(['docker','image','inspect',plan['image_id']])
        network=json.loads(self.command(['docker','network','inspect','axxium_database']))[0]
        if network.get('Internal') is not True:raise Refused('Database rehearsal network is not isolated')
        proxy=self.inspect(PROXY)['NetworkSettings']['Networks']['open-hax']
        self.proxy_ip=str(ipaddress.IPv4Address(proxy['IPAddress']))
        if self.proxy_ip!=e['caddy_proxy_ip']:raise Refused('Observed trusted proxy address differs')
        self.original=CADDY.read_bytes()
        if digest(self.original)!=e['caddyfile_sha256']:raise Refused('Incumbent ingress bytes changed')
        self.final=prepare(self.original,e['caddyfile_sha256'])
        self.maintenance=maintenance(self.original,e['caddyfile_sha256'])
        for data in (self.maintenance,self.final):self.validate_caddy(data)
        self.old_environment=dict(x.split('=',1) for x in old['Config']['Env'])
        self.original_layer(plan)
        size=int(self.sql('axxium',"SELECT pg_database_size(current_database())").strip())
        if shutil.disk_usage(STATE).free<max(1024**3,size*4):raise Refused('Insufficient private backup space')

    def private_write(self,path,data):
        flags=os.O_WRONLY|os.O_CREAT|os.O_NOFOLLOW|os.O_CLOEXEC|os.O_EXCL
        fd=os.open(path,flags,0o600)
        try:
            os.fchmod(fd,0o600)
            with os.fdopen(fd,'wb',closefd=False) as out:out.write(data);out.flush();os.fsync(fd)
        finally:os.close(fd)
        parent=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:os.fsync(parent)
        finally:os.close(parent)

    @staticmethod
    def sync_directory(path):
        fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW|os.O_CLOEXEC)
        try:os.fsync(fd)
        finally:os.close(fd)

    def claim(self,plan):
        self.incumbent_image_id=plan['expected']['app_image_id']
        root=STATE/'operations'
        if not root.exists():root.mkdir(mode=0o700);self.sync_directory(STATE)
        meta=root.lstat()
        if not stat.S_ISDIR(meta.st_mode) or meta.st_uid!=os.getuid() or stat.S_IMODE(meta.st_mode)!=0o700:
            raise Refused('Private operation directory protection refused')
        self.operation=root/plan['source_sha']
        try:self.operation.mkdir(mode=0o700)
        except FileExistsError:raise Refused('Existing operation requires independent recovery; no replay') from None
        self.sync_directory(root)
        self.private_write(self.operation/'plan.json',(json.dumps(plan,sort_keys=True,separators=(',',':'))+'\n').encode())
        marker=json.dumps({'schema':'axxium-production-pending/v1','operation':str(self.operation),
                           'source_sha':SOURCE,'plan_sha256':self.plan_digest},sort_keys=True).encode()
        self.private_write(PENDING,marker)
        self.pending_bytes=marker
        for source,name in ((RUNTIME/'compose.yaml','incumbent.compose.yaml'),(RUNTIME/'.env','incumbent.env'),(CADDY,'incumbent.Caddyfile')):
            self.private_write(self.operation/name,source.read_bytes())

    def record(self,name,status):
        self.recheck_lock()
        if not re.fullmatch('[a-z][a-z0-9_]{0,63}',name) or status not in ('pending','succeeded','known_failure','healthy','rolled_back'):
            self.uncertain=True;raise Uncertain('Invalid operation journal phase')
        data=(json.dumps({'sequence':self.sequence,'phase':name,'status':status},sort_keys=True)+'\n').encode()
        try:self.private_write(self.operation/f'{self.sequence:03d}-{name}-{status}.json',data)
        except BaseException:self.uncertain=True;raise
        self.sequence+=1

    def validate_caddy(self,data):
        self.command(['docker','exec','-i',PROXY,'caddy','validate','--adapter','caddyfile','--config','-'],data=data)

    def replace_caddy(self,plan,data):
        self.unchanged_dependencies(plan)
        current=CADDY.read_bytes()
        if digest(current) not in {digest(self.original),digest(self.maintenance),digest(self.final)}:
            raise Uncertain('Unexpected ingress selection; no replacement permitted')
        self.validate_caddy(data)
        # The incumbent has a single-file bind. Preserve that inode: replacing
        # the pathname would leave the container bound to historical bytes.
        fd=os.open(CADDY,os.O_RDWR|os.O_NOFOLLOW|os.O_CLOEXEC)
        try:
            info=os.fstat(fd)
            path_info=CADDY.lstat()
            if not stat.S_ISREG(path_info.st_mode) or (info.st_dev,info.st_ino)!=(path_info.st_dev,path_info.st_ino):
                raise Uncertain('Ingress inode changed before write')
            self.recheck_lock()
            try:
                with os.fdopen(fd,'r+b',closefd=False) as stream:
                    if digest(stream.read())!=digest(current):raise Uncertain('Ingress bytes changed before write')
                    stream.seek(0);stream.write(data);stream.truncate();stream.flush();os.fsync(fd)
                path_info=CADDY.lstat()
                if not stat.S_ISREG(path_info.st_mode) or (info.st_dev,info.st_ino)!=(path_info.st_dev,path_info.st_ino):
                    raise Uncertain('Ingress inode changed during write')
                bound=self.command(['docker','exec',PROXY,'cat','/etc/caddy/Caddyfile'])
                if digest(bound)!=digest(data):raise Uncertain('Live ingress bind does not show candidate bytes')
            except BaseException:
                self.uncertain=True;raise
        finally:os.close(fd)
        self.command(['docker','exec','-i',PROXY,'caddy','reload','--adapter','caddyfile','--config','-'],data=data,effect=True)

    def http(self,path):
        try:
            with urllib.request.urlopen('https://axxium.promethean.rest'+path,timeout=15) as r:return r.status,r.read(1048576)
        except urllib.error.HTTPError as e:return e.code,e.read(1048576)
        except (OSError,urllib.error.URLError):raise KnownFailure('Public route observation unavailable') from None

    def fence(self,plan):
        self.replace_caddy(plan,self.maintenance)
        if self.http('/health')[0]!=503:raise KnownFailure('Public authority write fence not observed')

    def compose(self,*args,overlay=None):
        command=['docker','compose','--project-name','axxium','--project-directory',str(RUNTIME),
                 '--env-file',str(RUNTIME/'.env'),'-f',str(RUNTIME/'compose.yaml')]
        if overlay:command+=['-f',str(overlay)]
        return self.command(command+list(args),timeout=660,effect=True)

    def stop_app(self,plan):
        self.unchanged_dependencies(plan)
        if self.candidate_id is None:self.original_layer(plan)
        self.compose('stop','--timeout','610','axxium')
        if self.inspect(APP)['State']['Running']:raise Uncertain('Application stop not established')

    def original_layer(self,plan):
        changes=self.command(['docker','diff',APP]).decode().splitlines()
        if digest(json.dumps(sorted(changes),separators=(',',':')).encode())!=plan['expected']['app_writable_layer_sha256']:
            raise Uncertain('Incumbent writable layer changed; preserve it before replacement')

    def sql(self,database,query):
        return self.command(['docker','exec',DB,'psql','-U','axxium','-d',database,'-Atq','-v','ON_ERROR_STOP=1','-c',query])

    def table_hashes(self,database):
        names=self.sql(database,"SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename").decode().splitlines()
        result={}
        for name in names:
            if not re.fullmatch('[a-z_][a-z0-9_]{0,62}',name):raise Refused('Unexpected database table identifier')
            raw=self.sql(database,f'SELECT row_to_json(t)::text FROM public."{name}" t')
            if len(raw)>64*1024*1024:raise Refused('Database verification output exceeds bound')
            result[name]=digest(b'\n'.join(sorted(raw.splitlines())))
        return result

    def backup(self,plan,label):
        self.unchanged_dependencies(plan)
        writers=self.sql('axxium',"SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid() AND backend_type='client backend'")
        if writers.strip()!=b'0':raise KnownFailure('Application database is not quiescent for backup')
        path=self.operation/(label+'.pgdump')
        with path.open('xb') as out:
            path.chmod(0o600)
            self.command(['docker','exec',DB,'pg_dump','-U','axxium','-d','axxium','-Fc'],timeout=180,output=out)
            out.flush();os.fsync(out.fileno())
        clone='axxium_verify_'+secrets.token_hex(12)
        self.command(['docker','exec',DB,'createdb','-U','axxium','--template','template0',clone],effect=True)
        self.private_write(self.operation/(label+'-verification-db.json'),json.dumps({'database':clone}).encode())
        with path.open('rb') as source:
            try:
                p=subprocess.run(['docker','exec','-i',DB,'pg_restore','-U','axxium','-d',clone,'--exit-on-error'],stdin=source,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=180)
            except (OSError,subprocess.TimeoutExpired):self.uncertain=True;raise Uncertain('Restore verification outcome unavailable') from None
        if p.returncode:
            self.uncertain=True;raise Uncertain('Private restore returned unsuccessfully; retain verification database')
        if self.table_hashes('axxium')!=self.table_hashes(clone):raise KnownFailure('Restored backup differs from quiescent database')
        self.backups[label]={'path':str(path),'sha256':file_digest(path),'database':clone}
        self.private_write(self.operation/(label+'-backup.json'),json.dumps(self.backups[label],sort_keys=True).encode())

    def probe(self,plan,database,image,credentials,signup):
        name='axxium-verify-'+secrets.token_hex(12)
        values=rehearsal_environment(self.old_environment,database,self.proxy_ip)
        env=self.operation/(name+'.env')
        if any('\n' in v or '\r' in v for v in values.values()):raise Refused('Unrepresentable private environment')
        self.private_write(env,('\n'.join(k+'='+v for k,v in values.items())+'\n').encode())
        self.private_write(self.operation/(name+'.json'),json.dumps({'name':name,'database':database,'image_id':image,'scope':'isolated-rehearsal'}).encode())
        self.command(['docker','run','-d','--pull','never','--name',name,'--network','axxium_database','--user','1000:1000',
            '--label','open-hax.axxium-operation='+self.operation.name,
            '--cap-drop','ALL','--security-opt','no-new-privileges','--memory','512m','--pids-limit','256',
            '--tmpfs','/tmp:rw,noexec,nosuid,size=16m','--env-file',str(env),
            '--mount','type=bind,src='+str(STATE/'google-client.json')+',dst=/run/axxium/google-client.json,readonly',
            '--mount','type=bind,src='+str(STATE/'oidc')+',dst=/run/axxium/oidc,readonly',image],effect=True)
        self.wait_app(name)
        program='''const fs=require('fs');(async()=>{const d=JSON.parse(fs.readFileSync(0,'utf8'));const origin='http://127.0.0.1:8787';if(d.signup){const s=await fetch(origin+'/api/auth/signup',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(d.credentials)});if(s.status!==200)throw Error('signup');}const r=await fetch(origin+'/api/auth/login',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(d.credentials)});if(r.status!==200)throw Error('login');const b=await r.json();const cookie=r.headers.get('set-cookie')?.split(';')[0];if(!cookie||b.actor?.kind!=='human')throw Error('session');const m=await fetch(origin+'/api/actors/me',{headers:{cookie}});const body=await m.json();if(m.status!==200||body.actor?.id!==b.actor.id)throw Error('me');console.log('verified');})().catch(()=>process.exit(1));'''
        self.command(['docker','exec','-i',name,'node','-e',program],data=json.dumps({'signup':signup,'credentials':credentials}).encode(),effect=True)
        self.command(['docker','stop','--time','610',name],timeout=660,effect=True)
        self.command(['docker','rm',name],effect=True)

    def rehearse(self,plan,label,*,candidate):
        credentials={'email':'adoption-'+secrets.token_hex(12)+'@invalid.test','password':secrets.token_urlsafe(36)}
        clone=self.backups[label]['database']
        if candidate:self.probe(plan,clone,plan['image_id'],credentials,True)
        self.probe(plan,clone,plan['expected']['app_image_id'],credentials,not candidate)
        self.command(['docker','exec',DB,'dropdb','-U','axxium',clone],effect=True)
        self.private_write(self.operation/(label+'-compatibility.json'),json.dumps({'source_sha':SOURCE,'image_id':IMAGE,'old_image_id':plan['expected']['app_image_id'],'candidate_then_old':candidate,'password_session_roundtrip':True}).encode())

    def wait_app(self,name):
        for _ in range(60):
            d=self.inspect(name)
            if d['State']['Status']!='running':raise KnownFailure('Application did not remain running')
            try:
                self.command(['docker','exec',name,'node','-e',"fetch('http://127.0.0.1:8787/health').then(r=>process.exit(r.ok?0:1)).catch(()=>process.exit(1))"],timeout=10)
                if d['State'].get('Health',{}).get('Status') in (None,'healthy'):return
                time.sleep(2)
            except KnownFailure:time.sleep(2)
        raise KnownFailure('Application health deadline expired')

    def start_candidate(self,plan):
        self.unchanged_dependencies(plan)
        if self.candidate_id is None:self.original_layer(plan)
        overlay={'services':{'axxium':{'image':plan['image_id'],'stop_grace_period':'610s','environment':{
            'AXXIUM_OIDC_CONFIG_FILE':'/run/axxium/oidc/authority.json','AXXIUM_TRUST_PROXY':self.proxy_ip},
            'volumes':[{'type':'bind','source':str(STATE/'oidc'),'target':'/run/axxium/oidc','read_only':True,'bind':{'create_host_path':False}}]}}}
        path=self.operation/'candidate.compose.json';raw=json.dumps(overlay).encode()
        if path.exists():
            if private_read(path)!=raw:raise Uncertain('Candidate overlay identity changed')
        else:self.private_write(path,raw)
        self.compose('up','-d','--no-deps','--pull','never','axxium',overlay=path)
        selected=self.inspect(APP)
        if selected['Image']!=plan['image_id']:raise Uncertain('Candidate selection does not match admitted image')
        self.candidate_id=selected['Id']

    def private_health(self,plan):
        self.wait_app(APP);self.unchanged_dependencies(plan)
        if self.http('/health')[0]!=503:raise KnownFailure('Authority maintenance fence is no longer observed')
        d=self.inspect(APP)
        if d['Image']!=plan['image_id'] or d['Id']!=self.candidate_id:raise Uncertain('Selected application identity differs')
        env=dict(x.split('=',1) for x in d['Config']['Env'])
        for key,value in self.old_environment.items():
            if key not in ('PATH','NODE_VERSION','YARN_VERSION','AXXIUM_OIDC_CONFIG_FILE','AXXIUM_TRUST_PROXY') and env.get(key)!=value:
                raise Uncertain('Incumbent application setting was not preserved')
        if self.inspect(PROXY)['NetworkSettings']['Networks']['open-hax']['IPAddress']!=self.proxy_ip:
            raise Uncertain('Trusted proxy address changed')
        raw=self.command(['docker','exec',PROXY,'wget','-q','-O','-',
            '--header','X-Forwarded-Proto: https','--header','Host: axxium.promethean.rest',
            'http://axxium:8787/oidc/.well-known/openid-configuration'])
        self.discovery(raw)

    @staticmethod
    def discovery(raw):
        try:data=json.loads(raw)
        except ValueError:raise KnownFailure('OIDC metadata unavailable') from None
        if data.get('issuer')!=ISSUER or 'S256' not in data.get('code_challenge_methods_supported',[]):
            raise KnownFailure('Production issuer or PKCE metadata differs')

    def final_check(self,plan):
        self.private_health(plan);self.identity_check()
        if self.http('/health')[0]!=503:raise Uncertain('Public authority fence changed before publication')

    def publish(self,plan):self.replace_caddy(plan,self.final)

    def public_health(self,plan):
        status,body=self.http('/oidc/.well-known/openid-configuration')
        if status!=200:raise KnownFailure('Public issuer is unavailable')
        self.discovery(body)
        if self.http('/health')[0]!=200 or self.http('/xrpc/_health')[0]!=200:
            raise KnownFailure('Public authority or preserved PDS health failed')

    def commit(self,plan):
        self.unchanged_dependencies(plan)
        current=self.inspect(APP)
        if (current['Id']!=self.candidate_id or current['Image']!=plan['image_id']
                or current['State'].get('Health',{}).get('Status')!='healthy'
                or file_digest(CADDY)!=digest(self.final)):
            raise Uncertain('Final application or ingress selection changed')
        verify=Path(__file__).resolve().parents[1]/'digitalocean/services/axxium/verify.sh'
        self.command(['bash',str(verify)])
        self.private_write(self.operation/'deployment.json',json.dumps(self.receipt('healthy'),sort_keys=True,indent=2).encode())

    def rollback(self,plan,stopped,candidate_attempted):
        self.unchanged_dependencies(plan)
        d=self.inspect(APP)
        allowed=(plan['image_id'],plan['expected']['app_image_id']) if candidate_attempted else (plan['expected']['app_image_id'],)
        identities={plan['expected']['app_container_id'],self.candidate_id}-{None}
        if d['Image'] not in allowed or d['Id'] not in identities:raise Uncertain('Unknown application prevents rollback')
        if candidate_attempted:
            self.replace_caddy(plan,self.maintenance);self.stop_app(plan)
            self.backup(plan,'failed');self.rehearse(plan,'failed',candidate=False)
        if stopped:
            path=self.operation/'rollback.compose.json'
            self.private_write(path,json.dumps({'services':{'axxium':{'image':plan['expected']['app_image_id']}}}).encode())
            self.compose('up','-d','--no-deps','--pull','never','axxium',overlay=path)
            self.wait_app(APP)
            if self.environment_hash(self.inspect(APP))!=plan['expected']['app_environment_sha256']:
                raise Uncertain('Rollback did not preserve incumbent environment')
        self.replace_caddy(plan,self.original)
        if self.http('/health')[0]!=200 or self.http('/xrpc/_health')[0]!=200:
            raise Uncertain('Rollback health unverified')
        current=self.inspect(APP)
        if (current['Image']!=plan['expected']['app_image_id']
                or current['State'].get('Health',{}).get('Status')!='healthy'
                or self.environment_hash(current)!=plan['expected']['app_environment_sha256']):
            raise Uncertain('Final rollback selection differs from incumbent observation')
        self.rollback_id=current['Id']
        self.private_write(self.operation/'deployment.json',json.dumps(self.receipt('rolled_back'),sort_keys=True).encode())

    def receipt(self,status):
        return {'schema':'axxium-minimal-deployment/v1','status':status,
                'requested_source_sha':SOURCE,'source_sha':SOURCE if status=='healthy' else None,
                'candidate_image_id':IMAGE,'image_id':IMAGE if status=='healthy' else self.incumbent_image_id,
                'app_container_id':self.candidate_id if status=='healthy' else self.rollback_id,
                'plan_sha256':self.plan_digest,'incumbent_source_sha':None,'operation':str(self.operation),
                'backup_sha256':{key:value['sha256'] for key,value in self.backups.items()},
                'database_restore':False,'human_login_verified':False}

    def finish(self,plan,status):
        """Clear only this verified terminal operation, never an unknown hold."""
        self.recheck_lock()
        receipt=json.loads(private_read(self.operation/'deployment.json'))
        terminal=json.loads(private_read(self.operation/f'{self.sequence-1:03d}-transaction-{status}.json'))
        if (receipt!=self.receipt(status) or terminal.get('phase')!='transaction'
                or terminal.get('status')!=status or terminal.get('sequence')!=self.sequence-1
                or status not in ('healthy','rolled_back')):
            raise Uncertain('Terminal deployment evidence does not match the refusal marker')
        saved=self.pending_bytes
        try:
            PENDING.unlink()
            self.sync_directory(PENDING.parent)
        except OSError:
            # Unlink may precede a failed directory sync. Re-establish a hold
            # when storage permits; never treat absence as successful recovery.
            try:
                if not PENDING.exists():self.private_write(PENDING,saved)
            except OSError:pass
            self.uncertain=True
            raise Uncertain('Refusal marker completion is not durable; operator stop required') from None
        self.pending_bytes=None


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan',required=True,type=Path)
    parser.add_argument('--plan-sha256',required=True)
    mode=parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--apply',action='store_true');mode.add_argument('--check',action='store_true')
    args=parser.parse_args();raw=private_read(args.plan)
    if digest(raw)!=args.plan_sha256:raise Refused('Controller plan digest differs')
    plan=validate_plan(json.loads(raw));host=NativeHost(args.plan_sha256)
    if args.check:
        with host.lock():host.preflight(plan)
        print(json.dumps({'status':'preflight_passed','source_sha':SOURCE,'image_id':IMAGE}));return 0
    result=execute(plan,host);print(json.dumps(result,sort_keys=True))
    return 0 if result['status']=='healthy' else 3


if __name__=='__main__':
    try:raise SystemExit(main())
    except (Refused,OSError,ValueError,KeyError,TypeError):
        print('Axxium deployment refused; preserve the private operation journal and inspect native state.',file=sys.stderr)
        raise SystemExit(2)
