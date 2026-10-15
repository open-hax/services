"""Run the shipped verifier with an isolated curl process double; no network."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

VERIFY = Path(__file__).resolve().parents[2] / "digitalocean/scripts/verify-knoxx-nested-https.sh"
CURL = r'''#!/usr/bin/env python3
import json,os,sys
from pathlib import Path
from urllib.parse import urlsplit
a=sys.argv[1:];u=urlsplit(a[-1]);host=u.hostname
with open(os.environ['CURL_CALLS'],'a') as f:f.write(json.dumps(a)+'\n')
s=json.loads(os.environ['CURL_SCENARIO']);row=s.get(host,{})
if row.get('exit'):raise SystemExit(row['exit'])
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


if __name__ == "__main__":
    unittest.main()
