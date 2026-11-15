#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

SERVICES = Path(__file__).resolve().parents[1]
BUNDLE = SERVICES / "digitalocean/ingress/axxium-production/rehearsal"
BASE = Path.home() / ".local/share/codex-work"

import axxium_ingress_caddy as caddy
import axxium_ingress_docker as topology
import axxium_ingress_host as controller
import axxium_ingress_transaction as journal
from axxium_minimal_release import Refused

DOCKER = ["docker", "--host", "unix:///var/run/docker.sock"]
MANAGED = "/etc/caddy/managed"

def fixture_paths(root, source):
    global ROOT, SOURCE, NAMES, OPERATION, MARKER, PLAN, PATHS, LOCAL_CONTRACT
    ROOT, SOURCE = root.resolve(), source.resolve(strict=True)
    if ROOT.parent != BASE.resolve() or not re.fullmatch(r"services-92-host-rehearsal\.[A-Za-z0-9_-]{6,16}", ROOT.name):
        raise SystemExit("Unrecognized synthetic fixture path")
    suffix = ROOT.suffix[1:]
    NAMES = {role: f"axxium-ingress-{unit}-{suffix}" for role, unit in
             {"app":"app", "proxy":"caddy", "db":"db", "pds":"pds", "shared":"shared", "dedicated":"dedicated"}.items()}
    OPERATION = hashlib.sha256(suffix.encode()).hexdigest()[:32]
    MARKER = ROOT / "runtime/.axxium-production-pending"
    PLAN = ROOT / "plan.json"
    PATHS = journal.JournalPaths(runtime=ROOT/"runtime", state=ROOT/"state")
    LOCAL_CONTRACT = {"name": NAMES["dedicated"], "appAlias": "axxium-ingress",
                      "proxyAddress": "10.253.247.2", "subnet": "10.253.247.0/28",
                      "gateway": "10.253.247.1", "dynamicPool": "10.253.247.8/29"}


def digest(value):
    return hashlib.sha256(value).hexdigest()

def file_hash(path):
    return digest(path.read_bytes())

def structure(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()

def shell(*args):
    return ["bash", str(BUNDLE/"fixture.sh"), str(ROOT), str(SERVICES), str(SOURCE), *args]

def docker(host, *args):
    return host.command([*DOCKER, *args], timeout=15)

def inspect(host, kind, name):
    return json.loads(docker(host, kind, "inspect", name))[0]

def git_head(path):
    return subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()

def dependency_pin(inspections):
    return {role: {key: inspections[role][key] for key in ("Id", "Image")}
            for role in ("db", "pds")}

def probes(host, phase):
    return json.loads(host.command(shell("probe", phase), timeout=40))

def require(condition, details):
    if not condition:
        raise Refused(details)

def ensure_isolated(inspections):
    for item in inspections.values():
        require(item["State"]["Running"], "Expected fixture service stopped")
        require(not item["HostConfig"]["PortBindings"], "Fixture published a host port")
        for mount in item["Mounts"]:
            if mount["Type"] == "bind":
                allowed = (ROOT, SOURCE)
                src = Path(mount["Source"])
                require(any(src.is_relative_to(root) for root in allowed), "Outside fixture bind")

def startup_facts():
    root = ROOT/"Caddyfile"
    managed = ROOT/"managed/axxium-production.caddy"
    configs = [ROOT/x for x in ("root-base", "legacy-site", "legacy-site-fenced",
                                "dedicated-site", "dedicated-site-fenced", "did.json")]
    return {p.name: file_hash(p) for p in configs} | {
        "current_root": file_hash(root), "current_dedicated": file_hash(managed) if managed.exists() else None,
        "unrelated": file_hash(ROOT/"managed/unrelated.caddy")}

def peer_readback(host, mode, inspect_):
    return topology.readback(host, proxy=inspect_["proxy"]["Id"], app=inspect_["app"]["Id"],
                             database=inspect_["db"]["Id"], pds=inspect_["pds"]["Id"],
                             shared=NAMES["shared"], dedicated=NAMES["dedicated"],
                             contract=LOCAL_CONTRACT, mode=mode, shared_alias="axxium-shared")

class Fixture:
    def __init__(self, plan, interrupt):
        self.plan = plan
        self.interrupt = interrupt

    def observe(self, host, states):
        inspections = {role: inspect(host, "container", NAMES[role])
                       for role in ("app", "proxy", "db", "pds")}
        ensure_isolated(inspections)
        require(dependency_pin(inspections) == self.plan["dependencies"], "Database or PDS identity changed")
        require(inspections["app"]["Image"] == self.plan["app_image"]
                and inspections["proxy"]["Image"] == self.plan["proxy_image"], "Serving image changed")
        facts = startup_facts()
        require(all(facts[k] == v for k,v in self.plan["static"].items()), "Admitted static input changed")
        path = NAMES["proxy"]
        caddy_readback = caddy.readback(host, inspections["proxy"]["Id"], ROOT/"Caddyfile",
                                       ROOT/"managed", container_managed=MANAGED)
        require(caddy_readback["startup_sha256"] == caddy_readback["active_sha256"],
                "Active and startup Caddy configuration disagree")
        self.last_caddy = caddy_readback
        require(caddy_readback["root_sha256"] == facts["current_root"],
                "Caddy bound root differs from host file")
        did = docker(host, "exec", path, "cat", "/etc/caddy/axxium-identity/did.json")
        require(digest(did) == facts["did.json"], "Caddy bound DID differs from host file")
        mode = "dedicated" if NAMES["dedicated"] in inspections["proxy"]["NetworkSettings"]["Networks"] else "shared"
        expected_networks = {NAMES["shared"], NAMES["dedicated"]} if mode == "dedicated" else {NAMES["shared"]}
        for role in ("app", "proxy"):
            require(set(inspections[role]["NetworkSettings"]["Networks"]) == expected_networks,
                    "Unexpected application network attachment")
        topology_readback = peer_readback(host, mode, inspections)
        peer = topology_readback["network"]
        route = "dedicated" if mode == "dedicated" else "legacy"
        fenced_site = (ROOT/(route + "-site-fenced")).read_bytes()
        fenced = (facts["current_dedicated"] == digest(fenced_site) if mode == "dedicated"
                  else facts["current_root"] == digest((ROOT/"root-base").read_bytes() + fenced_site))
        filename = route + "-site" + ("-fenced" if fenced else "")
        expected_site = ROOT/filename
        if mode == "dedicated":
            require(facts["current_dedicated"] == file_hash(expected_site)
                    and facts["current_root"] == facts["root-base"], "Wrong tracked dedicated route")
        else:
            require(facts["current_dedicated"] is None and
                    facts["current_root"] == digest((ROOT/"root-base").read_bytes() + expected_site.read_bytes()),
                    "Wrong tracked shared route")
        family = mode if mode == "dedicated" or peer["proxy_address"] == "10.253.246.2" else "restored"
        state = family + ("_fenced" if fenced else "_live")
        require(state in states, "Fixture readback differs from admitted phase alternatives")
        private = json.loads(docker(host, "exec", path, "wget", "-qO-",
            "--header=X-Forwarded-Proto: https", "--header=X-Forwarded-Host: axxium.fixture.test:8443",
            "http://" + peer["upstream"] + "/oidc/.well-known/openid-configuration"))
        require(private["issuer"] == "https://axxium.fixture.test:8443/oidc", "OIDC failed from Caddy peer")
        tls = probes(host, state)
        require(tls["directUntrusted"] == 503 and tls["pdsOAuth"] and tls["did"] == tls["otherRoute"] == 200,
                "Expected preserved public routes differ")
        if fenced:
            require(tls["apex"] == 503, "Required public fence unavailable")
        else:
            require(tls["oidcPkce"] and tls["idTokenSignature"] and tls["secureSession"]
                    and tls["actor"] == self.plan["actor"], "Synthetic actor did not survive the phase")
        stable = {"state": state, "peer": peer, "startup": facts, "caddy_config": caddy_readback, "topology": topology_readback,
                  "dependencies": self.plan["dependencies"]}
        runtime_sha256 = digest(structure(topology_readback))
        evidence = {"state": state, "observed_ns": time.time_ns(), "routes": tls, "peer": peer,
                    "runtime_sha256": runtime_sha256,
                    "root": facts["current_root"], "did": facts["did.json"], "caddy": caddy_readback}
        with (ROOT/"observations.jsonl").open("a") as receipts:
            receipts.write(json.dumps(evidence, sort_keys=True)+"\n")
        return journal.Observation(state, digest(structure(stable)), digest(structure(evidence)), runtime_sha256)

    def effect(self, host, phase):
        dedicated = phase in ("select_dedicated", "publish_dedicated", "fence_dedicated")
        fenced = not phase.startswith("publish_")
        site = "dedicated" if dedicated else "legacy"
        payload = (ROOT/(site + "-site" + ("-fenced" if fenced else ""))).read_bytes()
        root = (ROOT/"root-base").read_bytes()
        identity = inspect(host, "container", NAMES["proxy"])["Id"]
        caddy.publish(host, identity, ROOT/"Caddyfile", ROOT/"managed",
                      root_bytes=root if dedicated else root+payload, route_name=caddy.ROUTE_NAME,
                      route_bytes=payload if dedicated else None, container_managed=MANAGED,
                      expected_before=self.last_caddy)
        if phase in ("select_dedicated", "restore_shared"):
            require(probes(host, "switch_fenced")["apex"] == 503, "Switch requires a verified public fence")
            host.recheck_lock()
            host.command(shell(phase), timeout=100, effect=True)
        if self.interrupt:
            os.kill(os.getpid(), signal.SIGKILL)

def pin():
    from axxium_minimal_release import NativeHost
    host = NativeHost()
    facts = startup_facts()
    snapshots = {role: inspect(host, "container", NAMES[role]) for role in ("app","proxy","db","pds")}
    ensure_isolated(snapshots)
    plan = {"base_services":git_head(SERVICES), "source":git_head(SOURCE),
            "built_server_sha256":file_hash(SOURCE/"dist/server.js"), "dependencies":dependency_pin(snapshots),
            "app_image":snapshots["app"]["Image"], "proxy_image":snapshots["proxy"]["Image"],
            "actor":json.loads((ROOT/"initial.jsonl").read_bytes())["actor"],
            "static":{k:v for k,v in facts.items() if not k.startswith("current_")}}
    with PLAN.open("xb") as output:
        output.write(structure(plan))

def worker(action, phase=None):
    if action == "pin":
        pin()
        return
    raw = PLAN.read_bytes()
    result = controller.execute(Fixture(json.loads(raw), os.environ.get("INTERRUPT") == "1"),
                                OPERATION, digest(raw), action, phase, paths=PATHS)
    print(json.dumps(result, sort_keys=True))


def run_worker(action, phase=None, *, interrupted=False, refusal=None):
    cmd = [sys.executable, "-B", str(Path(__file__).resolve()), "worker", str(ROOT), str(SOURCE), action]
    if phase is not None:
        cmd.append(phase)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=140,
                            env=dict(os.environ, INTERRUPT="1" if interrupted else "0"))
    if interrupted:
        expected = -signal.SIGKILL
    elif refusal:
        expected = 1
    else:
        expected = 0
    if result.returncode != expected or (refusal and refusal not in result.stderr):
        raise RuntimeError(f"{action} {phase or ''} returned {result.returncode}: {result.stderr.strip()}")
    if result.stdout:
        print(result.stdout.strip(), flush=True)
    return json.loads(result.stdout) if result.stdout else None


def marker_identity():
    stat = MARKER.stat()
    return stat.st_dev, stat.st_ino, MARKER.read_bytes()


def observed_container_ids():
    names = [NAMES[role] for role in ("app", "proxy", "db", "pds")]
    return subprocess.check_output([*DOCKER, "inspect", "--type=container", "--format", "{{.Id}}", *names],
                                   timeout=15).decode().splitlines()


def reconcile_after_restart(phase, later_action, later_phase, refusal, target):
    before = marker_identity()
    old_ids = observed_container_ids()
    run_worker("step", phase, interrupted=True)
    after_effect = observed_container_ids()
    if any(old == new for old, new in zip(old_ids[:2], after_effect[:2], strict=True)) or old_ids[2:] != after_effect[2:]:
        raise RuntimeError("Cutover did not replace only Caddy and Axxium")
    if marker_identity() != before:
        raise RuntimeError("Worker interruption changed the exact ingress hold")
    run_worker(later_action, later_phase, refusal=refusal)
    readback = run_worker("reconcile")
    if readback["status"] != "verified" or readback["state"] != target:
        raise RuntimeError("Fresh worker did not verify the interrupted effect")
    if marker_identity() != before:
        raise RuntimeError("Reconciliation changed the exact ingress hold")
    if observed_container_ids() != after_effect:
        raise RuntimeError("Read-only reconciliation changed container identities")
    with (ROOT/"restart-proof.jsonl").open("a") as receipts:
        receipts.write(json.dumps({"phase": phase, "worker": "SIGKILL", "state": target,
                                   "hold_device": before[0], "hold_inode": before[1],
                                   "hold_sha256": digest(before[2]), "container_ids": after_effect,
                                   "blocked_while_pending": later_action, "effect_replayed": False}) + "\n")


def rehearse(source):
    BASE.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="services-92-host-rehearsal.", dir=BASE))
    fixture_paths(root, source)
    shutil.copyfile(BUNDLE/"probe.cjs", ROOT/"probe.cjs")
    print(json.dumps({"private_receipts": str(ROOT), "operation": OPERATION}), flush=True)
    try:
        with (ROOT/"initial.jsonl").open("xb") as receipt:
            subprocess.run(shell("setup"), stdout=receipt, check=True, timeout=120)
        run_worker("pin")
        run_worker("start")
        run_worker("step", "fence_shared")
        reconcile_after_restart("select_dedicated", "step", "publish_dedicated",
                                "Reconcile the pending phase", "dedicated_fenced")
        run_worker("step", "publish_dedicated")
        run_worker("step", "fence_dedicated")
        reconcile_after_restart("restore_shared", "finish", None,
                                "A reconciled terminal state is required", "restored_fenced")
        run_worker("step", "publish_restored")
        closed = run_worker("finish")
        if MARKER.exists() or closed["status"] != "finished" or closed["state"] != "restored_live":
            raise RuntimeError("Exact rollback readback did not close the ingress hold")
        if (ROOT/"legacy-root").read_bytes() != (ROOT/"Caddyfile").read_bytes():
            raise RuntimeError("Original shared Caddy root bytes changed")
        if closed["records"] != 14:
            raise RuntimeError("Rehearsal did not record one intent and verification for each phase")
    finally:
        subprocess.run(shell("clean"), check=True, timeout=60)
    print(json.dumps({"restored": closed["state"], "records": closed["records"], "cleanup": "verified"}))


def main():
    os.umask(0o077)
    os.environ["DOCKER_HOST"] = DOCKER[2]
    os.environ.pop("DOCKER_CONTEXT", None)
    if len(sys.argv) > 1 and sys.argv[1] == "worker":
        _, _, root, source, action, *phase = sys.argv
        fixture_paths(Path(root), Path(source))
        worker(action, *phase)
        return
    parser = argparse.ArgumentParser(description="Rehearse Axxium ingress recovery against disposable local Docker containers.")
    parser.add_argument("axxium_source", type=Path, help="Local Axxium checkout with compiled dist/server.js and installed node_modules")
    args = parser.parse_args()
    rehearse(args.axxium_source)


if __name__ == "__main__":
    main()
