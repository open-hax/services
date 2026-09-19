#!/usr/bin/env node
// Run as the dedicated environment operator, after installing reviewed templates.
import {mkdirSync,existsSync,readFileSync,writeFileSync,copyFileSync,chmodSync} from 'node:fs';
import {generateKeyPairSync,randomBytes} from 'node:crypto';
import {resolve} from 'node:path';
const [rootArg,templateArg]=process.argv.slice(2);
if(!rootArg||!templateArg)throw Error('Usage: prepare-service-environments.mjs ROOT TEMPLATES');
const root=resolve(rootArg),templates=resolve(templateArg);
for(const [environment,index] of [['testing',0],['staging',1]])for(const service of ['knoxx','axxium']){
 const slot=`${root}/${environment}/${service}`;mkdirSync(slot,{recursive:true,mode:0o700});
 copyFileSync(`${templates}/${service}.compose.yaml`,`${slot}/compose.yaml`);
 const origin=`https://${environment}.${service}.promethean.rest`;
 let values;
 if(service==='axxium'){
  values={AXXIUM_PUBLIC_BASE_URL:origin,AXXIUM_LISTEN_PORT:String(18871+index),DB_PASSWORD:randomBytes(32).toString('hex'),JWT_SECRET:randomBytes(48).toString('base64url'),SESSION_COOKIE_SECURE:'true'};
  if(!existsSync(`${slot}/identity-private.json`)){
   const {privateKey,publicKey}=generateKeyPairSync('ed25519');
   writeFileSync(`${slot}/identity-private.json`,JSON.stringify(privateKey.export({format:'jwk'})),{mode:0o600});
   writeFileSync(`${slot}/identity-public.json`,JSON.stringify(publicKey.export({format:'jwk'})),{mode:0o644});
  }
  if(!existsSync(`${slot}/trust.json`))writeFileSync(`${slot}/trust.json`,'{}\n',{mode:0o600});
 }else{
  values={KNOXX_PUBLIC_BASE_URL:origin,KNOXX_AXXIUM_ORIGIN:`https://${environment}.axxium.promethean.rest`,KNOXX_LISTEN_PORT:String(18881+index),KNOXX_SESSION_SECRET:randomBytes(32).toString('hex'),KNOXX_API_KEY:randomBytes(32).toString('hex')};
  for(const dir of ['content','generated-contracts','workspace'])mkdirSync(`${slot}/state/${dir}`,{recursive:true});
 }
 if(!existsSync(`${slot}/.env`))writeFileSync(`${slot}/.env`,Object.entries(values).map(([k,v])=>`${k}=${v}`).join('\n')+'\n',{mode:0o600});
 else if(!readFileSync(`${slot}/.env`,'utf8').includes(`${service.toUpperCase()}_PUBLIC_BASE_URL=${origin}\n`))throw Error('Existing slot origin differs');
 chmodSync(`${slot}/.env`,0o600);
 console.log(`Prepared ${environment}.${service}; instance secrets remain on this host.`);
}
