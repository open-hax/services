import os, subprocess, tempfile, unittest
from pathlib import Path

WORKFLOW=Path(__file__).resolve().parents[2]/'.github/workflows/legacy-deployment-key-match.yml'
EXPECTED='SHA256:EydElJ66W2i1d+XTgpX92zQwv9F9I7+A5vFHfcj1wQs'

class FingerprintWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.key=Path(self.temp.name)/'synthetic-key'
        subprocess.run(['ssh-keygen','-q','-t','ed25519','-N','','-f',str(self.key)],check=True)
        self.private=self.key.read_text();self.public=self.key.with_suffix('.pub').read_text()
        self.synthetic_fp=subprocess.check_output(['ssh-keygen','-lf',str(self.key.with_suffix('.pub'))],text=True).split()[1]
        definition=WORKFLOW.read_text()
        self.assertIn('    environment: production\n',definition)
        self.assertIn("    if: github.ref == 'refs/heads/main'\n",definition)
        self.assertIn('permissions: {}\n',definition)
        self.assertEqual(definition.count('      - name:'),1)
        script=definition.split('        run: |\n',1)[1]
        self.script='\n'.join(line[10:] for line in script.splitlines())+'\n'
    def run_script(self, script, value):
        env=os.environ.copy();env.pop('SSH_PRIVATE_KEY',None)
        if value is not None:env['SSH_PRIVATE_KEY']=value
        result=subprocess.run(['bash','-c',script],env=env,capture_output=True,text=True,timeout=15)
        output=result.stdout+result.stderr
        self.assertNotIn(self.private,output);self.assertNotIn(self.public,output)
        self.assertNotIn(self.synthetic_fp,output);self.assertNotIn(EXPECTED,output)
        return result
    def test_match_and_mismatch_do_not_output_key_or_fingerprint(self):
        matched=self.run_script(self.script.replace(EXPECTED,self.synthetic_fp),self.private)
        self.assertEqual((matched.returncode,matched.stdout),(0,'MATCH\n'))
        mismatch=self.run_script(self.script,self.private)
        self.assertEqual((mismatch.returncode,mismatch.stdout),(1,'MISMATCH\n'))
    def test_missing_and_malformed_secret_fail_closed(self):
        self.assertEqual(self.run_script(self.script,None).returncode,2)
        self.assertEqual(self.run_script(self.script,'invalid synthetic key').returncode,2)

if __name__=='__main__':unittest.main()
