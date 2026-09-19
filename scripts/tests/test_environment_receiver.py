import importlib.util,io,json,tarfile,tempfile,unittest
from pathlib import Path
spec=importlib.util.spec_from_file_location('receiver',Path(__file__).parents[1]/'receive-service-environment.py')
receiver=importlib.util.module_from_spec(spec);spec.loader.exec_module(receiver)
class ReceiverTests(unittest.TestCase):
 def archive(self,tags,extra=None):
  tmp=tempfile.NamedTemporaryFile();self.addCleanup(tmp.close)
  with tarfile.open(tmp.name,'w') as out:
   payload=json.dumps([{'RepoTags':tags,'Config':'config.json','Layers':[]}]).encode();entry=tarfile.TarInfo('manifest.json');entry.size=len(payload);out.addfile(entry,io.BytesIO(payload))
   if extra:out.addfile(tarfile.TarInfo(extra),io.BytesIO())
  return tmp.name
 def test_only_admitted_image_can_be_loaded(self):
  sha='a'*40
  receiver.validate_archive(self.archive(['promethean-axxium:'+sha]),'axxium',sha)
  for tags in [['caddy:latest'],['promethean-axxium:'+sha,'caddy:latest'],['promethean-axxium:'+'b'*40]]:
   with self.assertRaises(ValueError):receiver.validate_archive(self.archive(tags),'axxium',sha)
 def test_archive_paths_cannot_escape(self):
  for path in ['../outside','/etc/passwd']:
   with self.assertRaises(ValueError):receiver.validate_archive(self.archive(['promethean-axxium:'+'a'*40],path),'axxium','a'*40)
if __name__=='__main__':unittest.main()
