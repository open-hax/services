"""Independent hash vectors; these do not qualify the absent filesystem edge."""
import hashlib
import json
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[2]


def canonical(value):
    if value is None:
        return "N"
    if isinstance(value, bool):
        return "T" if value else "F"
    if isinstance(value, int) and 0 <= value <= 9007199254740991:
        return f"I{value};"
    if isinstance(value, str):
        return f"S{len(value.encode('utf-8'))}:{value}"
    if isinstance(value, list):
        return f"V{len(value)}:" + "".join(map(canonical, value))
    if isinstance(value, dict):
        return f"M{len(value)}:" + "".join(canonical(k) + canonical(value[k]) for k in sorted(value))
    raise ValueError("outside test vector domain")


class PortableLawParityTest(unittest.TestCase):
    def test_actual_native_and_adjacent_ns_hashes_across_bb_nbb_python(self):
        before = ROOT.joinpath("README.md").stat()
        values = [None, False, True, 0, 9007199254740991, "λ😀", [], {},
                  str(before.st_mtime_ns), str(before.st_ctime_ns),
                  str(before.st_ino), str(before.st_dev),
                  "1793735800379596064", "1793735800379596065", "-1", "0",
                  "-9223372036854775808", "9223372036854775807",
                  "18446744073709551615",
                  {"b": [None, 0, True], "a": "1793735800379596064"}]
        encoded = [canonical(v) for v in values]
        expected = [{"canonical": text, "sha256": hashlib.sha256(
            ("services.local/v1\n" + text).encode()).hexdigest()} for text in encoded]
        bb = ("(require '[cheshire.core :as j] '[services.local-generation :as l]) "
              "(import '[java.security MessageDigest] '[java.math BigInteger]) "
              "(println (j/generate-string (mapv (fn [v] "
              "{\"canonical\" (l/canonical v) \"sha256\" "
              "(format \"%064x\" (BigInteger. 1 (.digest (MessageDigest/getInstance \"SHA-256\") "
              "(.getBytes (l/preimage v) \"UTF-8\"))))}) (j/parse-string (slurp *in*)))))")
        nbb = ("(require '[services.local-generation :as l] '[\"node:fs\" :as fs] "
               "'[\"node:crypto\" :as crypto]) "
               "(println (js/JSON.stringify (clj->js (mapv (fn [v] "
               "{\"canonical\" (l/canonical v) \"sha256\" "
               "(-> (crypto/createHash \"sha256\") (.update (l/preimage v) \"utf8\") "
               "(.digest \"hex\"))}) "
               "(js->clj (js/JSON.parse (fs/readFileSync 0 \"utf8\")))))))")
        for runtime, script in (("bb", bb), ("nbb", nbb)):
            with self.subTest(runtime=runtime):
                result = subprocess.run([runtime, "-cp", "src", "-e", script],
                    cwd=ROOT, input=json.dumps(values), text=True, capture_output=True,
                    timeout=20, check=False)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stderr, "")
                self.assertEqual(json.loads(result.stdout), expected)
        after = ROOT.joinpath("README.md").stat()
        self.assertEqual(before.st_mtime_ns, after.st_mtime_ns)
        self.assertEqual(before.st_ctime_ns, after.st_ctime_ns)
        self.assertNotEqual(expected[12]["sha256"], expected[13]["sha256"])


if __name__ == "__main__":
    unittest.main()
