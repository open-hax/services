import unittest
from unittest.mock import patch

import fixture_netns


def read_routes(path):
    if str(path) == '/proc/net/route':
        return 'Iface Destination Gateway Flags\n'
    return ('00000000000000000000000000000000 00 00000000000000000000000000000000 00 '
            '00000000000000000000000000000000 ffffffff 00000001 00000000 00200200 lo\n'
            '00000000000000000000000000000001 80 00000000000000000000000000000000 00 '
            '00000000000000000000000000000000 00000000 00000002 00000000 80200001 lo\n')


class FixtureNamespaceTests(unittest.TestCase):
    def test_rejects_external_interface_and_routes(self):
        with patch.object(fixture_netns.socket, 'if_nameindex', return_value=[(1, 'lo')]), \
             patch.object(fixture_netns.Path, 'read_text', read_routes):
            fixture_netns.require_isolated()
        with patch.object(fixture_netns.socket, 'if_nameindex', return_value=[(1, 'lo'), (2, 'eth0')]):
            with self.assertRaises(RuntimeError):
                fixture_netns.require_isolated()
        def unsafe_route(path):
            if str(path) == '/proc/net/route':
                return 'Iface Destination Gateway Flags\nlo 00000000 00000000 0001\n'
            return read_routes(path)
        with patch.object(fixture_netns.socket, 'if_nameindex', return_value=[(1, 'lo')]), \
             patch.object(fixture_netns.Path, 'read_text', unsafe_route):
            with self.assertRaises(RuntimeError):
                fixture_netns.require_isolated()
        def unsafe_ipv6(path):
            if str(path) == '/proc/net/route':
                return 'Iface Destination Gateway Flags\n'
            return ('0' * 32 + ' 00 ' + '0' * 32 + ' 00 ' + '0' * 32 +
                    ' 00000000 00000000 00000000 00000001 lo\n')
        with patch.object(fixture_netns.socket, 'if_nameindex', return_value=[(1, 'lo')]), \
             patch.object(fixture_netns.Path, 'read_text', unsafe_ipv6):
            with self.assertRaises(RuntimeError):
                fixture_netns.require_isolated()


if __name__ == '__main__':
    unittest.main()
