const assert = require('node:assert/strict')
const fs = require('node:fs')
const {createHash, randomBytes, createPublicKey, verify} = require('node:crypto')
const origin = 'https://axxium.fixture.test:8443'
const authority = JSON.parse(fs.readFileSync('/fixture/authority.json'))
const client = authority.clients[0]
const jar = new Map()
const hostCookie = () => [...jar].map(([key,value]) => key + '=' + value).join('; ')
async function request(path, options = {}) {
  const headers = {...options.headers, cookie: hostCookie()}
  const result = await fetch(new URL(path, origin), {...options, headers, redirect:'manual', signal:AbortSignal.timeout(4000)})
  for (const entry of result.headers.getSetCookie()) {
    const [name, ...value] = entry.split(';')[0].split('=')
    if (value.join('=')) jar.set(name, value.join('='))
    else jar.delete(name)
  }
  return result
}
async function success(path) {
  const response = await request(path)
  assert.equal(response.status, 200, path)
  return response
}
function assertSecureSession(response) {
  const session = response.headers.getSetCookie().find(value => value.startsWith('axxium_session='))
  assert.match(session, /; Secure/i)
  assert.match(session, /; HttpOnly/i)
}
const json = data => ({method:'POST', headers:{'content-type':'application/json', origin}, body:JSON.stringify(data)})
const form = data => ({method:'POST', headers:{'content-type':'application/x-www-form-urlencoded', origin}, body:new URLSearchParams(data)})
async function main() {
  const phase = process.argv[2]
  const account = {email:'fixture-owner@example.invalid', password:'disposable-fixture-phrase'}
  const fenced = phase.endsWith('_fenced')
  if (fenced) {
    for (const path of ['/portal/index.html', '/health', '/api/auth/config', '/oidc/.well-known/openid-configuration']) {
      const result = await request(path)
      assert.equal(result.status, 503, path)
      assert.equal(await result.text(), 'Axxium fixture fenced')
    }
  } else {
    assert.match(await (await success('/portal/index.html')).text(), /Axxium/i)
    assert.equal((await (await success('/health')).json()).status, 'ok')
    assert.equal((await (await success('/api/auth/config')).json()).publicBaseUrl, origin)
  }
  assert.equal((await (await success('/.well-known/did.json')).json()).id, 'did:web:axxium.fixture.test')
  const pdsHealth = await (await success('/xrpc/_health')).json()
  assert.equal(typeof pdsHealth.version, 'string')
  const pds = await (await success('/xrpc/com.atproto.server.describeServer')).json()
  assert.equal(pds.did, 'did:web:axxium.fixture.test')
  const pdsOAuth = await (await success('/.well-known/oauth-authorization-server')).json()
  assert.equal(pdsOAuth.issuer, 'https://axxium.fixture.test')
  const direct = await fetch('http://axxium-shared:8787/oidc/.well-known/openid-configuration', {
    headers:{'x-forwarded-proto':'https', 'x-forwarded-host':'axxium.fixture.test:8443'},
    signal:AbortSignal.timeout(4000)
  })
  assert.equal(direct.status, 503)
  const unrelated = await fetch('http://axxium.fixture.test:8081/', {signal:AbortSignal.timeout(4000)})
  assert.equal(unrelated.status, 200)
  assert.equal(await unrelated.text(), 'unrelated-route')
  if (fenced) {
    console.log(JSON.stringify({phase, apex:503, directUntrusted:503, did:200, pds:200, pdsOAuth:true, otherRoute:200}))
    return
  }
  if (phase === 'before') {
    const signup = await request('/api/auth/signup', json(account))
    assert.equal(signup.status, 200)
    assertSecureSession(signup)
  }
  const login = await request('/api/auth/login', json(account))
  assert.equal(login.status, 200)
  assertSecureSession(login)
  const actor = (await login.json()).actor.id
  assert.equal((await (await success('/api/auth/me')).json()).actor.id, actor)
  const discovery = await (await success('/oidc/.well-known/openid-configuration')).json()
  assert.equal(discovery.issuer, origin+'/oidc')
  assert.ok(discovery.code_challenge_methods_supported.includes('S256'))
  const verifier = randomBytes(32).toString('base64url')
  const state = randomBytes(16).toString('hex')
  const nonce = randomBytes(16).toString('hex')
  const redirectUri = client['redirect-uris'][0]
  let url = discovery.authorization_endpoint + '?' + new URLSearchParams({
    client_id:client.id, redirect_uri:redirectUri, response_type:'code', scope:'openid profile',
    state, nonce, code_challenge_method:'S256', code_challenge:createHash('sha256').update(verifier).digest('base64url')
  })
  let code
  for (let i = 0; i < 20; i++) {
    if (url.startsWith(redirectUri+'?')) {
      const redirect = new URL(url)
      assert.equal(redirect.searchParams.get('state'), state)
      code = redirect.searchParams.get('code')
      break
    }
    assert.equal(new URL(url).origin, origin)
    let response = await request(url)
    if (response.status === 200 && new URL(url).pathname.startsWith('/oidc/interaction/')) {
      const html = await response.text()
      const csrf = html.match(/name=csrf value='([^']+)'/)?.[1]
      assert.ok(csrf)
      assert.match(response.headers.getSetCookie().find(value=>value.startsWith('__Host-axxium_oidc_csrf=')), /; Secure/i)
      response = await request(url, form({csrf, decision:'approve'}))
    }
    const location = response.headers.get('location')
    assert.ok(location, 'OIDC redirect missing at status '+response.status)
    url = new URL(location, origin).href
  }
  assert.ok(code)
  const tokens = await fetch(discovery.token_endpoint, {
    method:'POST', headers:{'content-type':'application/x-www-form-urlencoded'},
    body:new URLSearchParams({client_id:client.id, client_secret:client.secret,
      grant_type:'authorization_code', code, code_verifier:verifier, redirect_uri:redirectUri}),
    signal:AbortSignal.timeout(4000)
  })
  if (!tokens.ok) {
    const failure = await tokens.json()
    throw new Error('OIDC token exchange HTTP '+tokens.status+': '+failure.error)
  }
  const [header,payload,signature] = (await tokens.json()).id_token.split('.')
  const claims = JSON.parse(Buffer.from(payload,'base64url'))
  const keyId = JSON.parse(Buffer.from(header,'base64url')).kid
  const jwk = (await (await success(discovery.jwks_uri)).json()).keys.find(key => key.kid === keyId)
  assert.ok(verify('RSA-SHA256', Buffer.from(header+'.'+payload), createPublicKey({key:jwk,format:'jwk'}), Buffer.from(signature,'base64url')))
  assert.equal(claims.iss, discovery.issuer)
  assert.equal(claims.sub, actor)
  assert.equal(claims.aud, client.id)
  assert.equal(claims.nonce, nonce)
  console.log(JSON.stringify({phase, portal:200, health:200, signedIn:true, secureSession:true, oidcPkce:true, idTokenSignature:true, directUntrusted:503, did:200, pds:200,pdsOAuth:true,otherRoute:200,actor}))
}
main().catch(error=>{console.error(error);process.exit(1)})
