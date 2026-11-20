#!/usr/bin/env python3
"""Exercise inactive Caddy transport with synthetic keys/origin, no host ports."""
from __future__ import annotations

import datetime
from contextlib import closing
import hashlib
import http.client
import json
import os
from pathlib import Path
import shutil
import signal
import ssl
import subprocess
import sys
import tempfile
import time
import uuid

if sys.flags.optimize:
    print(json.dumps({"status": "refused", "reason": "Run the fixture without Python optimization; guards must remain active", "resourcesCreated": False}))
    raise SystemExit(1)

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "workstations/embeddinggemma2/transport"
CADDY = "sha256:612f0ff47f33888e3b61a8db399ff2dc22c2cefb8cb652d86a619e52eabcd51f"
NODE = "sha256:66bc7408b58869d57a4d97f1e814dddf8ba49e3d8c2339c93b3bfadeaddb4e40"
NAME = "embedding-provider.fixture"


def run(args, *, data=None, timeout=30, check=True):
    result = subprocess.run(args, input=data, capture_output=True, timeout=timeout)
    if check and result.returncode:
        raise RuntimeError("Controlled fixture command failed; private output suppressed")
    return result


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inspect(cid):
    return json.loads(run(["docker", "inspect", cid]).stdout)[0]


def ip(cid, network):
    return inspect(cid)["NetworkSettings"]["Networks"][network]["IPAddress"]


def make_identity(scratch, role, ca, *, server=False):
    target = scratch / role
    target.mkdir(mode=0o700)
    key, csr, cert = [target / ("identity." + suffix) for suffix in ("key", "csr", "crt")]
    run(["openssl", "req", "-new", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256",
         "-nodes", "-keyout", str(key), "-out", str(csr), "-subj", "/CN=" + (NAME if server else role)])
    ext = target / "extensions"
    ext.write_text("basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\n"
                   + ("extendedKeyUsage=serverAuth\nsubjectAltName=DNS:" + NAME if server else "extendedKeyUsage=clientAuth") + "\n")
    run(["openssl", "x509", "-req", "-in", str(csr), "-CA", str(ca / "ca.crt"),
         "-CAkey", str(ca / "ca.key"), "-CAcreateserial", "-days", "1", "-extfile", str(ext), "-out", str(cert)])
    key.chmod(0o600)
    return target


def make_ca(scratch, name):
    target = scratch / name
    target.mkdir(mode=0o700)
    run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256",
         "-nodes", "-days", "1", "-subj", "/CN=" + name,
         "-addext", "basicConstraints=critical,CA:TRUE", "-addext", "keyUsage=critical,keyCertSign,cRLSign",
         "-keyout", str(target / "ca.key"), "-out", str(target / "ca.crt")])
    (target / "ca.key").chmod(0o600)
    return target


def verify():
    owner = "eg2-transport-fixture-" + uuid.uuid4().hex[:10]
    scratch = Path(tempfile.mkdtemp(prefix=owner + "-"))
    containers, network = [], None
    result = {"owner": owner, "observedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
              "status": "failed", "caddyImageId": CADDY, "syntheticOriginImageId": NODE,
              "productionChanged": False, "hostPublishedPorts": 0, "realModelOrCredentialsUsed": False,
              "tailnetOrServeChanged": False, "modelAdmissionScope": "Synthetic upstream only; Proxx owns real model routing",
              "tlsNegativeScope": "Wrong server name/CA are direct Python TLS controls; actual Caddy consumer misconfiguration is not qualified",
              "sourceHashes": {str(p.relative_to(ROOT)): digest(p) for p in [*CONFIG.glob("*.Caddyfile"), CONFIG / "README.md", Path(__file__).resolve()]}, "cases": []}

    def container(label, image, args, *, mounts=(), env=(), start=True):
        command = ["docker", "create", "--name", owner + "-" + label, "--label", "codex.owner=" + owner,
                   "--network", owner, "--read-only", "--user", f"{os.getuid()}:{os.getgid()}",
                   "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true", "--cpus", ".25",
                   "--memory", "64m", "--memory-swap", "64m", "--pids-limit", "32"]
        if image == CADDY:
            command += ["--cap-add", "NET_BIND_SERVICE", "--tmpfs", f"/tmp:rw,noexec,nosuid,size=8m,uid={os.getuid()},gid={os.getgid()}"]
        for src, dst in mounts:
            command += ["--mount", f"type=bind,src={src},dst={dst},readonly"]
        for key, value in env:
            command += ["--env", key + "=" + value]
        command += ["--entrypoint", "caddy" if image == CADDY else "node", image, *args]
        cid = run(command).stdout.decode().strip()
        containers.append(cid)
        if start:
            run(["docker", "start", cid])
        return cid

    def record(label, actual, expected):
        result["cases"].append({"case": label, "actual": actual, "expected": expected, "passed": actual == expected})
        assert actual == expected, label

    try:
        assert os.getuid() != 0
        for image in (CADDY, NODE):
            assert json.loads(run(["docker", "image", "inspect", image]).stdout)[0]["Id"] == image
        network = run(["docker", "network", "create", "--internal", "--label", "codex.owner=" + owner, owner]).stdout.decode().strip()
        server_ca, client_ca = make_ca(scratch, "server-ca"), make_ca(scratch, "client-ca")
        server = make_identity(scratch, "provider", server_ca, server=True)
        client = make_identity(scratch, "consumer", client_ca)
        rogue = make_identity(scratch, "untrusted-consumer", server_ca)
        for target, pairs in [(server, [("identity.crt", "server.crt"), ("identity.key", "server.key")]),
                              (client, [("identity.crt", "client.crt"), ("identity.key", "client.key")])]:
            for src, dst in pairs:
                shutil.copyfile(target / src, target / dst)
                (target / dst).chmod(0o600)
        shutil.copyfile(client_ca / "ca.crt", server / "client-ca.crt")
        shutil.copyfile(server_ca / "ca.crt", client / "server-ca.crt")
        # This is a disposable test double, never an installed service or real
        # model gate. Its observations prove transport denial before upstream.
        origin_code = """const http=require('node:http');let rows=[];http.createServer((q,r)=>{if(q.url==='/fixture/observations'){r.setHeader('Content-Type','application/json');return r.end(JSON.stringify(rows));}let parts=[];q.on('data',b=>parts.push(b));q.on('end',()=>{let body;try{body=JSON.parse(Buffer.concat(parts).toString()||'{}')}catch{body={}}rows.push({method:q.method,path:q.url,model:body.model||null,ambientHeaders:['authorization','proxy-authorization','cookie','x-open-hax-bridge-auth'].some(k=>k in q.headers)});r.setHeader('Content-Type','application/json');if(q.url==='/health')return r.end('{\"ok\":true}');if(q.url==='/v1/models')return r.end('{\"data\":[{\"id\":\"embeddinggemma-2\"}]}');if(body.model!=='embeddinggemma-2'){r.statusCode=400;return r.end('{}')}const send=()=>r.end(JSON.stringify({model:'embeddinggemma-2',data:[{index:0,embedding:[1,...Array(767).fill(0)]}],usage:{prompt_tokens:1}}));if(body.input==='fixture-delay')setTimeout(send,1200);else send();});}).listen(8081,'0.0.0.0');"""
        origin = container("origin", NODE, ["-e", origin_code])
        caller = container("caller", NODE, ["-e", "setInterval(()=>{},1000)"])
        provider_file = scratch / "provider.Caddyfile"
        consumer_file = scratch / "consumer.Caddyfile"
        shutil.copyfile(CONFIG / "provider.Caddyfile", provider_file)
        shutil.copyfile(CONFIG / "consumer.Caddyfile", consumer_file)
        # Addresses are explicitly reserved by creating the fixed peers before
        # their programs read configuration. No host listener or static guess.
        provider = container("provider", CADDY, ["run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"],
                             mounts=[(provider_file, "/etc/caddy/Caddyfile"), (server, "/run/tls")], start=False)
        consumer = container("consumer", CADDY, ["run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"],
                             mounts=[(consumer_file, "/etc/caddy/Caddyfile"), (client, "/run/tls")], start=False)
        # Docker allocates endpoints when started; placeholders are replaced
        # only in task-owned copied configuration, never repository templates.
        addresses = {}
        for cid in [provider, consumer]:
            run(["docker", "network", "disconnect", owner, cid])
            endpoint = json.loads(run(["docker", "network", "inspect", network]).stdout)[0]
            subnet = endpoint["IPAM"]["Config"][0]["Subnet"]
            prefix = subnet.rsplit('.', 1)[0]
            addresses[cid] = prefix + (".20" if cid == provider else ".21")
            run(["docker", "network", "connect", "--ip", addresses[cid], owner, cid])
        values = {"EMBEDDING_TLS_NAME": NAME, "EMBEDDING_UPSTREAM_TIMEOUT": "300ms"}
        for file, extra in [(provider_file, {"EMBEDDING_BIND": addresses[provider], "EMBEDDING_APPROVED_SOURCE": addresses[consumer] + "/32", "EMBEDDING_NATIVE_ORIGIN": ip(origin, owner) + ":8081"}),
                            (consumer_file, {"EMBEDDING_BIND": addresses[consumer], "EMBEDDING_APPROVED_SOURCE": ip(caller, owner) + "/32", "EMBEDDING_PROVIDER_ADDRESS": addresses[provider]})]:
            text = file.read_text()
            for key, value in {**values, **extra}.items():
                text = text.replace("{$" + key + "}", value)
            assert "{$" not in text
            file.write_text(text)
        for cid in [provider, consumer]:
            run(["docker", "start", cid])
        time.sleep(.6)

        def request(path, method="GET", body=None, headers=None):
            payload = {"host": addresses[consumer], "port": 8081, "path": path, "method": method,
                       "headers": headers or {}, "body": body}
            program = "const http=require('node:http');const p=JSON.parse(require('node:fs').readFileSync(0,'utf8'));let b=p.body===null?'':typeof p.body==='string'?p.body:JSON.stringify(p.body);const q=http.request({...p,headers:{...p.headers,'Content-Length':Buffer.byteLength(b)}},r=>{let a=[];r.on('data',x=>a.push(x));r.on('end',()=>console.log(JSON.stringify({status:r.statusCode,bytes:Buffer.concat(a).length,body:Buffer.concat(a).toString()})))});q.on('error',()=>process.exit(2));q.end(b);"
            response = json.loads(run(["docker", "exec", "-i", caller, "node", "-e", program], data=json.dumps(payload).encode()).stdout)
            return response

        def observations():
            with closing(http.client.HTTPConnection(ip(origin, owner), 8081, timeout=2)) as connection:
                connection.request("GET", "/fixture/observations")
                return json.loads(connection.getresponse().read())

        record("allowed-health", request("/health")["status"], 200)
        record("allowed-models", request("/v1/models")["status"], 200)
        before = len(observations())
        with closing(http.client.HTTPConnection(addresses[consumer], 8081, timeout=2)) as connection:
            connection.request("GET", "/health")
            record("unapproved-alias-source", connection.getresponse().status, 403)
        record("source-denial-before-upstream", len(observations()), before)
        for label, path, method in [("chat-denied", "/v1/chat/completions", "POST"), ("method-denied", "/v1/embeddings", "PUT"), ("wrong-path-denied", "/arbitrary", "GET")]:
            record(label, request(path, method)["status"], 403)
        record("path-denial-before-upstream", len(observations()), before)
        answer = request("/v1/embeddings", "POST", {"model": "embeddinggemma-2", "input": "fixture"},
                         {"Content-Type": "application/json", "Authorization": "fixture", "Proxy-Authorization": "fixture", "Cookie": "fixture", "X-Open-Hax-Bridge-Auth": "fixture"})
        record("allowed-embedding", answer["status"], 200)
        record("ambient-headers-stripped", observations()[-1]["ambientHeaders"], False)
        record("synthetic-worker-wrong-model", request("/v1/embeddings", "POST", {"model": "wrong-model"})["status"], 400)
        record("body-cap", request("/v1/embeddings", "POST", "x" * (129 * 1024))["status"], 413)
        before = len(observations())
        context = ssl.create_default_context(cafile=str(server_ca / "ca.crt"))
        denied = False
        try:
            import socket
            with socket.create_connection((addresses[provider], 8443), timeout=2) as sock:
                with context.wrap_socket(sock, server_hostname=NAME) as secure:
                    secure.sendall(b"GET /health HTTP/1.1\r\nHost: embedding-provider.fixture\r\nConnection: close\r\n\r\n")
                    denied = not secure.recv(128)
        except ssl.SSLError:
            denied = True
        record("missing-client-cert-denied", denied, True)
        record("tls-denial-before-upstream", len(observations()), before)
        context.load_cert_chain(str(client / "client.crt"), str(client / "client.key"))
        with socket.create_connection((addresses[provider], 8443), timeout=2) as sock:
            with context.wrap_socket(sock, server_hostname=NAME) as secure:
                secure.sendall(b"GET /health HTTP/1.1\r\nHost: embedding-provider.fixture\r\nConnection: close\r\n\r\n")
                response = secure.recv(128)
        record("valid-mtls-unapproved-provider-source", response.startswith(b"HTTP/1.1 403"), True)
        rogue_context = ssl.create_default_context(cafile=str(server_ca / "ca.crt"))
        rogue_context.load_cert_chain(str(rogue / "identity.crt"), str(rogue / "identity.key"))
        denied = False
        try:
            with socket.create_connection((addresses[provider], 8443), timeout=2) as sock:
                with rogue_context.wrap_socket(sock, server_hostname=NAME) as secure:
                    secure.sendall(b"GET /health HTTP/1.1\r\nHost: embedding-provider.fixture\r\nConnection: close\r\n\r\n")
                    denied = not secure.recv(128)
        except ssl.SSLError:
            denied = True
        record("wrong-client-ca-denied", denied, True)
        record("all-auth-denials-before-upstream", len(observations()), before)
        wrong_ca = ssl.create_default_context(cafile=str(client_ca / "ca.crt"))
        wrong_ca.load_cert_chain(str(client / "client.crt"), str(client / "client.key"))
        for label, ctx, hostname in [("direct-client-wrong-server-name-denied", context, "different.fixture"),
                                     ("direct-client-wrong-server-ca-denied", wrong_ca, NAME)]:
            denied = False
            try:
                with socket.create_connection((addresses[provider], 8443), timeout=2) as sock:
                    with ctx.wrap_socket(sock, server_hostname=hostname):
                        pass
            except ssl.SSLError:
                denied = True
            record(label, denied, True)
        started = time.monotonic()
        record("upstream-deadline", request("/v1/embeddings", "POST", {"model": "embeddinggemma-2", "input": "fixture-delay"})["status"], 504)
        record("deadline-is-bounded", time.monotonic() - started < 1.0, True)
        run(["docker", "stop", "--time", "2", provider])
        record("owned-provider-outage", request("/health")["status"], 502)
        run(["docker", "start", provider])
        time.sleep(.5)
        record("owned-provider-restored", request("/health")["status"], 200)
        for cid in containers:
            state = inspect(cid)
            assert not state["HostConfig"].get("PortBindings") and state["HostConfig"]["Memory"] == 64 * 1024 * 1024
        result["status"] = "passed"
    except BaseException as error:
        result["errorClass"] = type(error).__name__
        result["reason"] = str(error) if isinstance(error, AssertionError) else "Fixture failed; raw diagnostics suppressed"
    finally:
        # Reconcile possible create effects even if the client was interrupted
        # before returning the ID. The unique fixture label is checked again
        # before removing exactly this owned subset; no daemon-wide cleanup.
        owned = run(["docker", "ps", "-aq", "--filter", "label=codex.owner=" + owner]).stdout.decode().split()
        for cid in reversed(owned):
            state = inspect(cid)
            assert state["Config"]["Labels"]["codex.owner"] == owner
            run(["docker", "rm", "-f", cid])
        for nid in run(["docker", "network", "ls", "-q", "--filter", "label=codex.owner=" + owner]).stdout.decode().split():
            state = json.loads(run(["docker", "network", "inspect", nid]).stdout)[0]
            assert state["Labels"]["codex.owner"] == owner and not state["Containers"]
            run(["docker", "network", "rm", nid])
        shutil.rmtree(scratch)
        result["syntheticKeysRemoved"] = not scratch.exists()
        result["ownedContainersRemaining"] = len(run(["docker", "ps", "-aq", "--filter", "label=codex.owner=" + owner]).stdout.split())
        result["ownedNetworksRemaining"] = len(run(["docker", "network", "ls", "-q", "--filter", "label=codex.owner=" + owner]).stdout.split())
    return result


if __name__ == "__main__":
    def interrupted(_signum, _frame):
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, interrupted)
    result = verify()
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["status"] == "passed" else 1)
