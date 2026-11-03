"""Native wire and independent inert validator RED; never evaluates EDN."""
import unittest

from scripts import local_generation_wire as wire
from scripts import local_generation_edn as edn


class WireBoundaryTest(unittest.TestCase):
    def test_strict_wire(self):
        for raw in (b'{"a":0,"a":1}', b'1.0', b'NaN', b'1 2', b'"\\ud800"',
                    b'"\\u0000"', b'9007199254740992', b'-1', b'\xff'):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                wire.decode(raw)
        self.assertEqual(wire.decode(b'{"a":[null,0,true,"-1"]}'),
                         {"a": [None, 0, True, "-1"]})

    def test_wide_stat_strings_and_native_hash(self):
        for value in ('1793735800379596064', '1793735800379596065', '-1',
                      '-9223372036854775808', '9223372036854775807'):
            self.assertTrue(wire.ns64(value))
            self.assertEqual(wire.decode(wire.encode(value)), value)
        self.assertNotEqual(wire.digest('1793735800379596064'),
                            wire.digest('1793735800379596065'))
        for value in ('-0', '+1', '01', ' 1', '9223372036854775808', 1):
            self.assertFalse(wire.ns64(value))
        self.assertTrue(wire.u64('18446744073709551615'))
        self.assertFalse(wire.u64('18446744073709551616'))

    def test_independent_inert_descriptor_to_edn_positive(self):
        descriptor = b'{"schema":"services.inert-edn/v1","value":"lambda"}'
        self.assertTrue(edn.validate(descriptor, b'{:fixture/value "lambda"}\n'))
        for payload in (b'#=(evil)', b'{:fixture/value "other"}',
                        b'{:fixture/value "lambda", :extra true}',
                        b'{:fixture/value "lambda"} (evil)', b'"lambda"'):
            self.assertFalse(edn.validate(descriptor, payload))
        self.assertFalse(edn.validate(b'{"schema":"services.inert-edn/v1",'
                                     b'"value":"a","value":"b"}', b'{}'))


if __name__ == '__main__':
    unittest.main()
