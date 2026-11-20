#!/usr/bin/env python3
"""Verify an existing private worker; never creates, restarts or routes one."""
from __future__ import annotations

import argparse
import datetime
import hashlib
import http.client
import json
import math
from pathlib import Path, PurePosixPath
import re
import struct
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "workstations/embeddinggemma2/profile.json"
FIXTURE = "Knoxx embedding fallback checksum fixture."


class Refused(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise Refused(message)


def docker(*args):
    result = subprocess.run(["docker", *args], capture_output=True, timeout=30)
    require(result.returncode == 0, "Docker readback failed; raw diagnostic suppressed")
    return result.stdout


def mounted_model(container, role):
    args = container["Config"].get("Cmd") or []
    positions = [i for i, flag in enumerate(args[:-1]) if flag in ("--model", "-m")]
    require(len(positions) == 1, "Worker must select one explicit model file")
    model = PurePosixPath(args[positions[0] + 1])
    matches = [m for m in container["Mounts"] if m["Type"] == "bind"
               and (model == PurePosixPath(m["Destination"])
                    or PurePosixPath(m["Destination"]) in model.parents)]
    require(len(matches) == 1 and not matches[0]["RW"], "Model must be a single read-only bind")
    mount = matches[0]
    relative = model.relative_to(mount["Destination"])
    require(".." not in relative.parts, "Model path escapes its mount")
    if role == "yoga":
        require(str(model) == "/models/model.gguf", "Yoga model mount differs")
    return Path(mount["Source"]) / relative


def validate_runtime(container, profile, role, networks):
    require(container["State"]["Running"] and not container["State"]["OOMKilled"],
            "Worker is stopped or OOM-killed")
    require(not container["HostConfig"]["Privileged"], "Privileged worker refused")
    config, host = container["Config"], container["HostConfig"]
    model = mounted_model(container, role)
    if role == "yoga":
        require(container["RestartCount"] == 0, "Yoga worker has restarted; qualify a clean startup")
        require(container["Image"] == profile["vulkan_image_id"]
                and config["Image"] == profile["vulkan_image"], "Yoga image binding differs")
        require(config.get("Entrypoint") == ["/app/llama-server"]
                and config.get("Cmd") == profile["yoga_command"], "Yoga command differs")
        require(config["User"] == profile["yoga_user"]
                and host.get("GroupAdd") == [profile["yoga_render_gid"]], "Yoga identity differs")
        require(host["NanoCpus"] == profile["yoga_cpu_nano_limit"]
                and host["Memory"] == profile["yoga_memory_bytes"]
                and host["MemorySwap"] == host["Memory"], "Yoga CPU/memory/no-swap caps differ")
        require(host["PidsLimit"] == 128 and host["ReadonlyRootfs"]
                and host.get("CapDrop") == ["ALL"]
                and not host.get("CapAdd") and host.get("PidMode") != "host"
                and host.get("IpcMode") != "host" and host.get("UTSMode") != "host"
                and "no-new-privileges:true" in host.get("SecurityOpt", []),
                "Yoga process/root/capability boundary differs")
        require(len(container["Mounts"]) == 1, "Yoga has additional host mounts")
        require(host.get("Tmpfs") == {"/tmp": "rw,noexec,nosuid,size=128m,uid=1002,gid=1002"},
                "Yoga temporary filesystem differs")
        require(host.get("Devices") == [{"PathOnHost": "/dev/dri/renderD128",
                                         "PathInContainer": "/dev/dri/renderD128",
                                         "CgroupPermissions": "rwm"}], "Yoga render device differs")
        require(len(networks) == 1 and networks[0].get("Internal") is True
                and not networks[0].get("Attachable")
                and len(networks[0].get("Containers", {})) == 1,
                "Yoga must occupy its own internal non-attachable bridge")
        require(host.get("NetworkMode") == networks[0]["Name"], "Yoga network mode differs")
        require(not host.get("PortBindings") and not host.get("PublishAllPorts")
                and all(not value for value in container["NetworkSettings"].get("Ports", {}).values()),
                "Yoga must have no requested or effective published ports")
        require(type(container["State"].get("Pid")) is int and container["State"]["Pid"] > 1,
                "Yoga worker namespace is unavailable")
        return model, None
    else:
        require(profile["stealth_existing_network"] in container["NetworkSettings"]["Networks"],
                "Existing Stealth fallback network differs")
        net = container["NetworkSettings"]["Networks"][profile["stealth_existing_network"]]
        require(profile["stealth_existing_alias"] in net.get("Aliases", []),
                "Existing Stealth fallback alias differs")
    ports = host.get("PortBindings", {})
    # Read-only verification does not bless a broad listener, even on the
    # incumbent fallback. Use this exact loopback selection for the HTTP probe.
    bindings = ports.get("8081/tcp", [])
    require(set(ports) == {"8081/tcp"} and len(bindings) == 1
            and bindings[0]["HostIp"] == "127.0.0.1", "Worker host exposure is not loopback-only")
    port = int(bindings[0]["HostPort"])
    require(1024 <= port <= 65535, "Worker loopback port is invalid")
    return model, port


def validate_embedding(response, profile):
    require(isinstance(response, dict) and response.get("model") == profile["model"],
            "Returned embedding model differs")
    data = response.get("data")
    require(isinstance(data, list) and len(data) == 1 and data[0].get("index") == 0,
            "Embedding response cardinality/index differs")
    vector = data[0].get("embedding")
    require(isinstance(vector, list) and len(vector) == profile["dimensions"]
            and all(type(x) in (int, float) and math.isfinite(x) for x in vector),
            "Embedding dimensions or finite numeric values differ")
    norm = math.sqrt(sum(x*x for x in vector))
    require(math.isfinite(norm) and 0.99 <= norm <= 1.01, "Embedding is not unit-normalized")
    tokens = response.get("usage", {}).get("prompt_tokens")
    require(type(tokens) is int and tokens > 0, "Positive embedding token usage is absent")
    return {"model": response["model"], "dimensions": len(vector), "allFinite": True,
            "l2Norm": norm, "promptTokens": tokens,
            "float32leSha256": hashlib.sha256(struct.pack("<" + "f"*len(vector), *vector)).hexdigest()}


def infer(port):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=25)
    try:
        body = json.dumps({"model": "embeddinggemma-2", "input": FIXTURE,
                           "encoding_format": "float"})
        start = time.monotonic()
        connection.request("POST", "/v1/embeddings", body,
                           {"Content-Type": "application/json"})
        response = connection.getresponse()
        require(response.status == 200, "Worker embedding request did not return HTTP 200")
        raw = response.read(256 * 1024 + 1)
        require(len(raw) <= 256 * 1024, "Worker embedding response exceeds bound")
        return json.loads(raw), round((time.monotonic() - start)*1000)
    finally:
        connection.close()


def infer_in_namespace(pid):
    # Execute fixed trusted host code in the existing worker's network
    # namespace only. No container filesystem writes or outside listener.
    client = """import http.client,json
c=http.client.HTTPConnection('127.0.0.1',8081,timeout=25)
try:
 c.request('POST','/v1/embeddings',json.dumps({'model':'embeddinggemma-2','input':'Knoxx embedding fallback checksum fixture.','encoding_format':'float'}),{'Content-Type':'application/json'})
 r=c.getresponse();body=r.read(262145)
 if r.status!=200 or len(body)>262144:raise ValueError('Bounded embedding response refused')
 print(json.dumps(json.loads(body)))
finally:c.close()
"""
    start = time.monotonic()
    result = subprocess.run(["nsenter", "-t", str(pid), "-n", sys.executable, "-c", client],
                            capture_output=True, timeout=30)
    require(result.returncode == 0, "Worker namespace embedding request failed; raw diagnostic suppressed")
    return json.loads(result.stdout), round((time.monotonic() - start)*1000)


def verify(container_name, role):
    profile = json.loads(PROFILE.read_text())
    container = json.loads(docker("inspect", container_name))[0]
    network_names = list(container["NetworkSettings"]["Networks"])
    networks = json.loads(docker("network", "inspect", *network_names)) if network_names else []
    model, port = validate_runtime(container, profile, role, networks)
    digest = hashlib.sha256()
    with model.open("rb") as source:
        while block := source.read(8 * 1024 * 1024):
            digest.update(block)
    require(digest.hexdigest() == profile["model_sha256"], "Actual mounted GGUF checksum differs")
    gpu = None
    if role == "yoga":
        logs = subprocess.run(["docker", "logs", container_name], capture_output=True,
                              timeout=15)
        require(logs.returncode == 0, "Yoga startup GPU log unavailable")
        text = (logs.stdout + logs.stderr).decode(errors="replace")
        offloads = re.findall(r"offloaded (\d+)/(\d+) layers to GPU", text)
        require("RADV REMBRANDT" in text and any(a == b == "25" for a, b in offloads),
                "Actual RADV GPU and complete 25-layer offload not proven")
        gpu = {"backend": "Vulkan", "device": "AMD RADV REMBRANDT", "offloadedLayers": 25}
    body, elapsed = infer_in_namespace(container["State"]["Pid"]) if role == "yoga" else infer(port)
    embedding = validate_embedding(body, profile)
    if role == "yoga":
        logs = subprocess.run(["docker", "logs", container_name], capture_output=True, timeout=15)
        require(logs.returncode == 0, "Yoga post-request log unavailable")
        require(FIXTURE.encode() not in logs.stdout + logs.stderr,
                "Synthetic embedding input was written to runtime logs")
    return {"status": "passed", "observedAt": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "role": role, "containerId": container["Id"], "imageId": container["Image"],
            "modelSha256": digest.hexdigest(), "hostPublishedPort": port, "gpu": gpu,
            "probeTransport": "worker-network-namespace" if role == "yoga" else "host-loopback",
            "embedding": {**embedding, "elapsedMs": elapsed},
            "liveConfigurationChanged": False, "crossHostTransportQualified": False,
            "sharedProxxRoutingChanged": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--role", choices=("yoga", "stealth"), required=True)
    parser.add_argument("--container", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(verify(args.container, args.role), sort_keys=True))
        return 0
    except Exception as error:
        print(json.dumps({"status": "failed", "reason": str(error) if isinstance(error, Refused)
                          else "Worker verification failed; raw diagnostic suppressed",
                          "errorClass": type(error).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
