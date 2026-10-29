import os
from pathlib import Path
import tempfile
import unittest

from identity_ingress_transaction import IngressTransactionError
from provision_ingress_owner import provision


class ProvisionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.selector = self.root / 'selector'
        self.lock = self.root / 'lock'
        self.selector.mkdir(mode=0o700)
        self.lock.mkdir(mode=0o700)

    def provision(self, validate=lambda: True):
        return provision(self.selector, self.lock, owner=os.getuid(), validate_incumbent=validate)

    def test_creates_once_and_never_replaces_lock_inode_or_existing_owner(self):
        self.assertEqual(self.provision(), {'created_lock': True, 'created_owner': True})
        inode = (self.lock / 'transaction.lock').stat().st_ino
        owner_before = (self.selector / 'owner.json').read_bytes()
        self.assertEqual(self.provision(lambda: False), {'created_lock': False, 'created_owner': False})
        self.assertEqual((self.lock / 'transaction.lock').stat().st_ino, inode)
        self.assertEqual((self.selector / 'owner.json').read_bytes(), owner_before)

    def test_unknown_incumbent_or_pending_guard_changes_nothing(self):
        with self.assertRaisesRegex(IngressTransactionError, 'not positively verified'):
            self.provision(lambda: False)
        self.assertEqual(list(self.lock.iterdir()), [])
        (self.selector / 'bootstrap-pending').write_text('pending')
        with self.assertRaisesRegex(IngressTransactionError, 'requires recovery'):
            self.provision()
        self.assertEqual(list(self.lock.iterdir()), [])

    def test_existing_symlink_or_untrusted_directory_refuses(self):
        (self.lock / 'transaction.lock').symlink_to('/dev/null')
        with self.assertRaises(OSError):
            self.provision()
        self.assertFalse((self.selector / 'owner.json').exists())
        (self.lock / 'transaction.lock').unlink()
        self.selector.chmod(0o777)
        with self.assertRaisesRegex(IngressTransactionError, 'unsafe'):
            self.provision()


if __name__ == '__main__':
    unittest.main()
