"""Run the shipped verifier with an isolated curl process double; no network."""
import json
import os
import configparser
import errno
from pathlib import Path
import runpy
import socket
import subprocess
import tempfile
import unittest
from unittest.mock import patch

VERIFY = Path(__file__).resolve().parents[2] / "digitalocean/scripts/verify-knoxx-nested-https.sh"
CURL = r'''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
from urllib.parse import urlsplit
a=sys.argv[1:];u=urlsplit(a[-1]);host=u.hostname
with open(os.environ['CURL_CALLS'],'a') as f:f.write(json.dumps(a)+'\n')
s=json.loads(os.environ['CURL_SCENARIO']);row=s.get(host,{})
exit_code=row.get(u.scheme+'_exit',row.get('exit',0))
if exit_code:raise SystemExit(exit_code)
if u.scheme=='https':
 Path(a[a.index('-o')+1]).write_text(row.get('body','application'))
 print(row.get('status',200),end='')
else:print(row.get('redirect','308 https://'+host+'/'),end='')
'''


class EnvironmentHttpsVerifierTests(unittest.TestCase):
    def run_verifier(self, scenario):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binary = root / "bin"
            binary.mkdir()
            curl = binary / "curl"
            curl.write_text(CURL)
            curl.chmod(0o700)
            tmp = root / "tmp"
            tmp.mkdir()
            calls = root / "calls.jsonl"
            result = subprocess.run(["bash", str(VERIFY)], text=True, capture_output=True, timeout=15,
                                    env={"PATH": str(binary) + os.pathsep + os.defpath,
                                         "TMPDIR": str(tmp), "CURL_CALLS": str(calls),
                                         "CURL_SCENARIO": json.dumps(scenario)})
            self.assertEqual([], list(tmp.iterdir()), "the verifier must remove its owned body file")
            return result, [json.loads(line) for line in calls.read_text().splitlines()]

    def test_all_apps_and_exact_redirects_are_accepted(self):
        result, calls = self.run_verifier({})
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(8, len(calls))
        for label in ("testing", "stealth", "yoga", "staging"):
            host = label + ".knoxx.promethean.rest"
            self.assertEqual(1, sum(row[-1] == "https://" + host + "/" for row in calls))
            self.assertEqual(1, sum(row[-1] == "http://" + host + "/" for row in calls))
        for args in calls:
            self.assertIn("--max-time", args)
            self.assertNotIn("-k", args)

    def test_testing_and_staging_literal_placeholders_are_accepted(self):
        scenario = {label + ".knoxx.promethean.rest": {"status": 503, "body": "Service not configured or unavailable."}
                    for label in ("testing", "staging")}
        self.assertEqual(0, self.run_verifier(scenario)[0].returncode)

    def test_placeholder_body_failure_names_host_without_exposing_body(self):
        for label in ("testing", "staging"):
            host = label + ".knoxx.promethean.rest"
            with self.subTest(host=host):
                result, calls = self.run_verifier({host: {"status": 503, "body": "wrong placeholder"}})
                self.assertNotEqual(0, result.returncode)
                self.assertIn(host + ": unexpected 503 placeholder body", result.stderr)
                self.assertNotIn("wrong placeholder", result.stderr)
                self.assertEqual("https://" + host + "/", calls[-1][-1])

    def test_https_and_http_transport_failures_name_current_host(self):
        for label in ("testing", "stealth", "yoga", "staging"):
            host = label + ".knoxx.promethean.rest"
            for scheme, code in (("https", 60), ("http", 28)):
                with self.subTest(host=host, scheme=scheme):
                    result, calls = self.run_verifier({host: {scheme + "_exit": code}})
                    self.assertNotEqual(0, result.returncode)
                    self.assertIn(host + ": " + scheme.upper() + " curl failed", result.stderr)
                    self.assertEqual(scheme + "://" + host + "/", calls[-1][-1])

    def test_reject_wrong_placeholder_host_body_status_redirect_or_transport(self):
        cases = [(label, {"status": 503, "body": "Service not configured or unavailable."})
                 for label in ("stealth", "yoga")]
        cases += [("testing", {"status": 503, "body": "wrong placeholder"}),
                  ("staging", {"status": 502}),
                  ("testing", {"redirect": "301 https://testing.knoxx.promethean.rest/"}),
                  ("testing", {"redirect": "308 https://other.example/"}),
                  ("testing", {"exit": 60})]
        for label, row in cases:
            with self.subTest(label=label, response=row):
                self.assertNotEqual(0, self.run_verifier({label + ".knoxx.promethean.rest": row})[0].returncode)


class EnvironmentIngressSocketTests(unittest.TestCase):
    def test_all_generated_relays_bind_before_their_address_exists(self):
        """Exercise generated FreeBind through real Linux IP_FREEBIND, no listener.

        Filesystem writes and systemctl/ufw are isolated doubles. A nonlocal
        TEST-NET address models the absent bridge; no interface is configured.
        """
        installer = Path(__file__).resolve().parents[1] / "install-environment-ingress.py"
        slots = (("testing-axxium", 19071, 18871), ("staging-axxium", 19072, 18872),
                 ("testing-knoxx", 19082, 18881), ("staging-knoxx", 19083, 18882))
        nonlocal_address = "198.51.100.254"
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as baseline:
            with self.assertRaises(OSError) as refused:
                baseline.bind((nonlocal_address, 0))
            self.assertEqual(errno.EADDRNOTAVAIL, refused.exception.errno,
                             "The causal fixture needs an actually absent local address")
        with tempfile.TemporaryDirectory(prefix="environment ingress ") as directory:
            root = Path(directory)

            def isolated_path(path):
                self.assertEqual("/etc/systemd/system", path)
                return root

            with patch("pathlib.Path", side_effect=isolated_path), \
                    patch("subprocess.run") as commands, patch("builtins.print"):
                runpy.run_path(str(installer), run_name="__owned_ingress_fixture__")
            for name, listen, target in slots:
                with self.subTest(slot=name):
                    config = configparser.ConfigParser()
                    config.read(root / f"promethean-{name}.socket")
                    self.assertEqual(f"172.31.255.1:{listen}", config["Socket"]["ListenStream"])
                    free_bind = config.getboolean("Socket", "FreeBind", fallback=False)
                    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as actual:
                        if free_bind:
                            actual.setsockopt(socket.SOL_IP, getattr(socket, "IP_FREEBIND", 15), 1)
                        try:
                            actual.bind((nonlocal_address, 0))
                        except OSError as error:
                            self.fail(f"{name}: generated FreeBind={free_bind} cannot bind before bridge setup: {error}")
                    # systemd permits repeated IPAddressAllow= declarations.
                    service = configparser.ConfigParser(strict=False)
                    service.read(root / f"promethean-{name}.service")
                    self.assertEqual(f"/usr/lib/systemd/systemd-socket-proxyd 127.0.0.1:{target}",
                                     service["Service"]["ExecStart"])
                    self.assertIn("IPAddressDeny=any", (root / f"promethean-{name}.service").read_text())
            calls = [call.args[0] for call in commands.call_args_list]
            self.assertEqual(["systemctl", "daemon-reload"], calls[0])
            self.assertEqual(["systemctl", "enable", "--now"] +
                             [f"promethean-{name}.socket" for name, _, _ in slots], calls[1])
            self.assertEqual([["ufw", "allow", "from", "172.31.255.2", "to", "172.31.255.1",
                              "port", str(port), "proto", "tcp", "comment", "Caddy environment ingress"]
                             for _, port, _ in slots], calls[2:])


if __name__ == "__main__":
    unittest.main()
