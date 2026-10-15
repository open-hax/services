#!/usr/bin/env python3
"""Exercise the committed website Nginx config with disposable synthetic bytes.

Requires a local Docker daemon and an already available nginx-unprivileged image.
No application build, deployment state, or real publication content is read.
Use --config PATH to demonstrate that an older config fails the same assertions.
"""

import argparse
import http.client
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "digitalocean/services/website/nginx.conf"
DEFAULT_IMAGE = "nginxinc/nginx-unprivileged:1.27"
MAIN_CONFIG = """worker_processes 1;
pid /tmp/nginx.pid;
error_log /dev/stderr warn;
events { worker_connections 64; }
http {
    include /etc/nginx/mime.types;
    default_type application/octet-stream;
    access_log off;
    client_body_temp_path /tmp/client;
    proxy_temp_path /tmp/proxy;
    fastcgi_temp_path /tmp/fastcgi;
    uwsgi_temp_path /tmp/uwsgi;
    scgi_temp_path /tmp/scgi;
    include /etc/nginx/conf.d/default.conf;
}
"""


def docker(*arguments):
    return subprocess.run(
        ["docker", *arguments], check=True, text=True, capture_output=True
    ).stdout.strip()


def write_fixture(root, relative_path, content):
    target = root / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    for parent in target.parents:
        parent.chmod(0o755)
        if parent == root:
            break
    target.write_text(content)
    target.chmod(0o644)
    return target


def request(port, path):
    # http.client preserves the raw target, including dot segments and percent
    # escapes. Browser/client normalization would hide the traversal regression.
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        connection.request("GET", path)
        response = connection.getresponse()
        return response.status, dict(response.getheaders()), response.read().decode()
    finally:
        connection.close()


def run(config, image):
    metadata = json.loads(docker("image", "inspect", image))[0]
    print(f"IMAGE {image} id={metadata['Id']} digests={metadata.get('RepoDigests', [])}")
    failures = []
    checks = 0
    container = None
    with tempfile.TemporaryDirectory(prefix="website-serving-proof-") as temporary:
        fixture = Path(temporary)
        # The container uid must traverse the dedicated directory; every byte
        # here is synthetic and all mounts are read-only to the container.
        fixture.chmod(0o755)
        html = fixture / "html"
        write_fixture(html, "index.html", '<html lang="en"><div id="root">EN_SHELL</div></html>')
        for locale in ("es", "fr", "de", "ja"):
            write_fixture(html, f"{locale}/index.html", f'<html lang="{locale}"><div id="root">{locale}_SHELL</div></html>')
        manifest = write_fixture(html, "published/manifest.edn", '{:manifest/version 1 :manifest/routes []}\n')
        artifact = "/published/artifacts/demo/probe/en/rev-1.html"
        write_fixture(html, artifact.lstrip("/"), "<article>PUBLIC_ARTIFACT</article>")
        css = "/published/artifacts/demo/probe/en/rev-1.css"
        write_fixture(html, css.lstrip("/"), "/* PUBLIC_CSS */")
        private_paths = (
            "/published/.ημ/cms/demo/ledger.edn",
            "/published/.translations/output.edn",
            "/published/.idempotency/key.edn",
            "/published/cms/demo/source.md",
            "/published/cms/demo/metadata.json",
            "/published/arbitrary.edn",
            "/published/artifacts-private/source.md",
            "/published/artifacts/demo/probe/en/.private.edn",
            "/published/artifacts/demo/probe/.translations/output.edn",
            "/published/artifacts/demo/.ημ/cms/ledger.edn",
            "/published/artifacts/.idempotency/key.edn",
        )
        for path in private_paths:
            write_fixture(html, path.lstrip("/"), "SYNTHETIC_PRIVATE_STATE\n")
        symlink = html / "published/artifacts/demo/probe/en/escape.html"
        symlink.symlink_to("/usr/share/nginx/html/published/.translations/output.edn")
        write_fixture(html, "outside-content.txt", "SYNTHETIC_OUTSIDE_WRITER_ROOT\n")
        (symlink.parent / "outside.html").symlink_to("/usr/share/nginx/html/outside-content.txt")
        write_fixture(fixture, "nginx.conf", MAIN_CONFIG)
        write_fixture(fixture, "server.conf", config.read_text())
        try:
            # Name before launching so interrupted startup still knows which
            # disposable container it must remove in the finally block.
            container = fixture.name
            docker(
                "run", "--name", container, "--detach", "--rm", "--pull=never",
                "--user", "101:101", "--read-only", "--cap-drop=ALL",
                "--security-opt", "no-new-privileges",
                "--tmpfs", "/tmp:rw,noexec,nosuid,size=16m",
                "--publish", "127.0.0.1::8080",
                "--mount", f"type=bind,src={html},dst=/usr/share/nginx/html,readonly",
                "--mount", f"type=bind,src={fixture / 'nginx.conf'},dst=/etc/nginx/nginx.conf,readonly",
                "--mount", f"type=bind,src={fixture / 'server.conf'},dst=/etc/nginx/conf.d/default.conf,readonly",
                "--entrypoint", "nginx", image, "-g", "daemon off;",
            )
            port = int(docker("port", container, "8080/tcp").rsplit(":", 1)[1])
            deadline = time.monotonic() + 10
            while True:
                try:
                    request(port, "/")
                    break
                except (OSError, http.client.HTTPException):
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Nginx did not become ready: " + docker("logs", container))
                    time.sleep(0.1)

            def check(path, expected_status, expected_type=None, marker=None, cache=None):
                nonlocal checks
                checks += 1
                # Raw Unicode is not a legal HTTP request target; encode the
                # synthetic Greek ledger directory while retaining other escapes.
                encoded_path = path.replace("η", "%CE%B7").replace("μ", "%CE%BC")
                status, headers, body = request(port, encoded_path)
                problems = []
                if status not in expected_status:
                    problems.append(f"HTTP {status}; expected {sorted(expected_status)}")
                if expected_type and headers.get("Content-Type", "").split(";", 1)[0] != expected_type:
                    problems.append(f"Content-Type {headers.get('Content-Type')}; expected {expected_type}")
                if marker and marker not in body:
                    problems.append(f"missing public marker {marker}")
                if cache and headers.get("Cache-Control") != cache:
                    problems.append(f"Cache-Control {headers.get('Cache-Control')}; expected {cache}")
                if status != 200 and "_SHELL" in body:
                    problems.append("a denied or absent publication returned the app shell")
                if problems:
                    failures.append(f"{path}: {'; '.join(problems)}")
                    print("FAIL " + failures[-1])
                else:
                    print(f"PASS {path}: HTTP {status}")

            check("/published/manifest.edn", {200}, "application/edn", ":manifest/version", "no-cache")
            check(artifact, {200}, "text/html", "PUBLIC_ARTIFACT", "no-cache")
            check(artifact + "?note=../", {200}, "text/html", "PUBLIC_ARTIFACT")
            check(css, {200}, "text/css", "PUBLIC_CSS")
            for path in private_paths:
                check(path, {404})
            check("/published/artifacts/demo/probe/en/escape.html", {404})
            check("/published/artifacts/demo/probe/en/outside.html", {404})
            for path in (
                "/published/artifacts/../../index.html",
                "/published/artifacts/%2e%2e/%2e%2e/index.html",
                "/published/artifacts/%2E%2E%2F%2E%2E%2Findex.html",
                "/%70ublished/artifacts/%2e%2e/%2e%2e/index.html",
                "/published/artifacts/demo/probe/en/%2eprivate.edn",
                "/published/artifacts/demo/probe/%2etranslations/output.edn",
            ):
                check(path, {400, 404})
            for path in ("/published", "/published/", "/published/artifacts/missing.html", "/published/missing.html"):
                check(path, {404})
            manifest.unlink()
            check("/published/manifest.edn", {404})
            manifest.symlink_to("/usr/share/nginx/html/published/.translations/output.edn")
            check("/published/manifest.edn", {404})
            check("/notes/missing", {200}, "text/html", "EN_SHELL")
            for locale in ("es", "fr", "de", "ja"):
                for path in (f"/{locale}", f"/{locale}/", f"/{locale}/notes/missing"):
                    check(path, {200}, "text/html", f"{locale}_SHELL")
        finally:
            if container:
                cleanup = subprocess.run(
                    ["docker", "rm", "--force", container], text=True, capture_output=True
                )
                if cleanup.returncode and "No such container" not in cleanup.stderr:
                    raise RuntimeError("fixture container cleanup failed: " + cleanup.stderr.strip())
    print(f"{checks} HTTP assertions, {len(failures)} failures; synthetic fixtures and container removed")
    return 1 if failures else 0


def main():
    def interrupted(signum, _frame):
        # Raising unwinds run() so SIGINT/SIGTERM clean up the fixture/container.
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGINT, interrupted)
    signal.signal(signal.SIGTERM, interrupted)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--image", default=os.environ.get("WEBSITE_SERVING_TEST_IMAGE", DEFAULT_IMAGE))
    args = parser.parse_args()
    try:
        return run(args.config.resolve(strict=True), args.image)
    except (OSError, subprocess.CalledProcessError, RuntimeError) as error:
        diagnostic = error.stderr.strip() if isinstance(error, subprocess.CalledProcessError) else str(error)
        print(f"ERROR {diagnostic}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
