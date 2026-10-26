#!/usr/bin/env node
// Run as the dedicated environment operator, after installing reviewed templates.
import {mkdirSync,existsSync,writeFileSync,copyFileSync,chmodSync,readFileSync} from 'node:fs';
import {generateKeyPairSync,randomBytes} from 'node:crypto';
import {resolve} from 'node:path';
import {spawnSync} from 'node:child_process';

function validateExistingSlot(slot,expected){
 // Compose owns dotenv syntax and interpolation. This fixed stdin model emits
 // only selected public values; no whole env file/config or parser error leaks.
 const environment=Object.fromEntries(Object.keys(expected).map(key=>[key,`\${${key}-}`]));
 const model={services:{slot:{image:'scratch',environment}}};
 const parsed=spawnSync('docker',['compose','--project-name','slot-validation',
  '--project-directory',slot,'--env-file',`${slot}/.env`,'-f','-','config','--format','json'],
  {input:JSON.stringify(model),encoding:'utf8',env:{PATH:process.env.PATH??'/usr/bin:/bin'},
   timeout:10000,killSignal:'SIGKILL',maxBuffer:1024*1024});
 if(parsed.error||parsed.status!==0||parsed.signal)throw Error('Existing slot configuration could not be parsed by Docker Compose');
 let values;
 try{values=JSON.parse(parsed.stdout)?.services?.slot?.environment;}
 catch{throw Error('Existing slot configuration could not be parsed by Docker Compose');}
 if(!values||Object.entries(expected).some(([key,value])=>values[key]!==value))
  throw Error('Existing slot configuration differs from selected environment');
}

const slotLaw=JSON.parse(readFileSync(new URL('./service_slots.json',import.meta.url),'utf8'));
const rows=slotLaw.proxx;
if(slotLaw.version!==1||Object.keys(rows).sort().join(',')!=='production,staging,stealth,testing,yoga')throw Error('Incomplete slot law');
const caddyPorts=new Set(slotLaw.reserved_caddy_ports),targets=new Set([18871,18872,18881,18882]),hosts=new Set();
for(const [name,row] of Object.entries(rows)){
 const expectedHost=['stealth','yoga'].includes(name)?name:'droplet';
 if(row.host!==expectedHost||row.hostname!==(name==='production'?'proxx.promethean.rest':`${name}.proxx.promethean.rest`)
  ||row.project!==({testing:'proxx-testing',staging:'proxx-staging',production:'proxx',stealth:'proxx-local',yoga:'proxx-yoga'})[name]
  ||row.role!==(['testing','staging'].includes(name)?'isolated':'existing-stack')||row.app_port!==8789
  ||!Number.isInteger(row.host_port)||row.host_port<1024||row.host_port>65535)throw Error('Invalid slot role');
 const host=`${row.host}:${row.host_port}`;
 if(hosts.has(host))throw Error('Colliding host port');hosts.add(host);
 if(name==='production'){
  if(row.caddy_port!==null||row.droplet_target!==null)throw Error('Production uses direct Caddy network');
 }else{
  for(const [key,used] of [['caddy_port',caddyPorts],['droplet_target',targets]]){
   const port=row[key];
   if(!Number.isInteger(port)||port<1024||port>65535||used.has(port))throw Error('Colliding ingress port');used.add(port);
  }
  if(['testing','staging'].includes(name)&&row.host_port!==row.droplet_target)throw Error('Wrong local target');
 }
}
const [rootArg,templateArg]=process.argv.slice(2);
if(!rootArg||!templateArg)throw Error('Usage: prepare-service-environments.mjs ROOT TEMPLATES');
if(typeof process.getuid!=='function'||process.getuid()!==1000)
 throw Error('prepare-service-environments.mjs must run as UID 1000');
const root=resolve(rootArg),templates=resolve(templateArg);
for(const [environment,index] of [['testing',0],['staging',1]])for(const service of ['knoxx','axxium','proxx']){
 const slot=`${root}/${environment}/${service}`;mkdirSync(slot,{recursive:true,mode:0o700});
 const origin=`https://${environment}.${service}.promethean.rest`;
 const declared=slotLaw.proxx[environment];
 if(!declared||declared.host!=='droplet'||declared.role!=='isolated')throw Error('Invalid declared slot');
 const expected=service==='proxx'
  ?{PROXX_PUBLIC_HOST:declared.hostname,PROXX_LISTEN_PORT:String(declared.host_port)}
  :service==='axxium'
  ?{AXXIUM_PUBLIC_BASE_URL:origin,AXXIUM_LISTEN_PORT:String(18871+index)}
  :{KNOXX_PUBLIC_BASE_URL:origin,KNOXX_AXXIUM_ORIGIN:`https://${environment}.axxium.promethean.rest`,KNOXX_LISTEN_PORT:String(18881+index)};
 if(existsSync(`${slot}/.env`))validateExistingSlot(slot,expected);
 copyFileSync(`${templates}/${service}.compose.yaml`,`${slot}/compose.yaml`);
 if(service==='axxium')copyFileSync(`${templates}/axxium-oidc.compose.yaml`,`${slot}/axxium-oidc.compose.yaml`);
 let values;
 if(service==='axxium'){
  values={...expected,DB_PASSWORD:randomBytes(32).toString('hex'),JWT_SECRET:randomBytes(48).toString('base64url'),SESSION_COOKIE_SECURE:'true'};
  if(!existsSync(`${slot}/identity-private.json`)){
   const {privateKey,publicKey}=generateKeyPairSync('ed25519');
   writeFileSync(`${slot}/identity-private.json`,JSON.stringify(privateKey.export({format:'jwk'})),{mode:0o600});
   writeFileSync(`${slot}/identity-public.json`,JSON.stringify(publicKey.export({format:'jwk'})),{mode:0o644});
  }
  if(!existsSync(`${slot}/trust.json`))writeFileSync(`${slot}/trust.json`,'{}\n',{mode:0o600});
 }else if(service==='proxx'){
  values={...expected,PROXY_AUTH_TOKEN:randomBytes(32).toString('hex'),PROXY_TOKEN_PEPPER:randomBytes(32).toString('hex'),PROXX_SESSION_SECRET:randomBytes(32).toString('hex'),PROXX_POSTGRES_PASSWORD:randomBytes(32).toString('hex')};
  mkdirSync(`${slot}/state/data`,{recursive:true});
  for(const role of ['broker','node'])copyFileSync(`${templates}/proxx-relay-${role}.compose.yaml`,`${slot}/proxx-relay-${role}.compose.yaml`);
 }else{
  values={...expected,KNOXX_SESSION_SECRET:randomBytes(32).toString('hex'),KNOXX_API_KEY:randomBytes(32).toString('hex')};
  for(const dir of ['content','generated-contracts','workspace'])mkdirSync(`${slot}/state/${dir}`,{recursive:true});
 }
 if(!existsSync(`${slot}/.env`))writeFileSync(`${slot}/.env`,Object.entries(values).map(([k,v])=>`${k}=${v}`).join('\n')+'\n',{mode:0o600});
 chmodSync(`${slot}/.env`,0o600);
 console.log(`Prepared ${environment}.${service}; instance secrets remain on this host.`);
}
