"""Safety checks for the production Axxium Events profile transition."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import axxium_events_profile as profile


IMAGE = "sha256:" + "a" * 64
OTHER_IMAGE = "sha256:" + "b" * 64
RECIPIENT = "did:plc:aaaaaaaaaaaaaaaaaaaaaaaa"
OTHER_RECIPIENT = "did:plc:bbbbbbbbbbbbbbbbbbbbbbbb"
STREAM = "AXXIUM_MAILBOX_STREAM_ENABLED"
SUBSCRIPTIONS = "AXXIUM_MAILBOX_SUBSCRIPTIONS_ENABLED"
COEXISTENCE = "AXXIUM_MAILBOX_TRANSPORT_COEXISTENCE"
WEBHOOK_ACTOR = "AXXIUM_MAILBOX_WEBHOOK_ACTOR_DID"
WEBHOOK_KEY = "AXXIUM_MAILBOX_WEBHOOK_KEY_FILE"
CLEAR_CENSUS = {
    "retained-subscriptions": 0,
    "active-subscriptions": 0,
    "eligible-pending": 0,
    "retryable-callbacks": 0,
    "unsettled-attempts": 0,
    "due-callbacks": 0,
}


class EventsProjectionTests(unittest.TestCase):
    def setUp(self):
        self.incumbent = {
            "name": "axxium",
            "services": {
                "axxium": {
                    "image": IMAGE,
                    "environment": {
                        STREAM: "true",
                        SUBSCRIPTIONS: "false",
                        WEBHOOK_ACTOR: RECIPIENT,
                        WEBHOOK_KEY: "/run/axxium/oidc/mailbox-webhook.key",
                        "AXXIUM_IDENTITY_ROOT_DID": "did:web:fixture.example:actors:owner",
                    },
                    "volumes": [{
                        "type": "bind",
                        "source": "/retained/mailbox.edn",
                        "target": "/var/lib/axxium/mailbox/ledger.edn",
                        "read_only": False,
                        "bind": {"create_host_path": False},
                    }],
                    "depends_on": {"database": {"condition": "service_healthy"}},
                },
                "database": {"image": "postgres:retained"},
                "pds": {"image": "pds:retained"},
            },
            "networks": {"default": {"name": "axxium"}},
        }
        self.candidate = deepcopy(self.incumbent)
        self.candidate["services"]["axxium"]["environment"].update({
            SUBSCRIPTIONS: "true",
            COEXISTENCE: "true",
        })

    def test_exact_two_flag_change_preserves_both_inputs(self):
        old = deepcopy(self.incumbent)
        new = deepcopy(self.candidate)

        self.assertIsNone(profile.validate_projection(
            self.incumbent, self.candidate, image_id=IMAGE, recipient_did=RECIPIENT,
        ))
        self.assertEqual(self.incumbent, old)
        self.assertEqual(self.candidate, new)

    def test_candidate_must_enable_both_flags_and_preserve_stream_and_binding(self):
        for key, value in (
            (SUBSCRIPTIONS, "false"),
            (COEXISTENCE, "false"),
            (COEXISTENCE, True),
            (STREAM, "false"),
            (WEBHOOK_ACTOR, OTHER_RECIPIENT),
            (WEBHOOK_KEY, "/another/mailbox-webhook.key"),
            ("AXXIUM_IDENTITY_ROOT_DID", "did:web:other.example:actors:owner"),
            ("EXTRA_ENV", "unexpected"),
        ):
            with self.subTest(key=key, value=value):
                candidate = deepcopy(self.candidate)
                candidate["services"]["axxium"]["environment"][key] = value
                with self.assertRaises(ValueError):
                    profile.validate_projection(
                        self.incumbent, candidate, image_id=IMAGE, recipient_did=RECIPIENT,
                    )

    def test_candidate_must_preserve_image_mount_dependencies_and_other_services(self):
        for path, value in (
            (("services", "axxium", "image"), OTHER_IMAGE),
            (("services", "axxium", "volumes", 0, "source"), "/other/mailbox.edn"),
            (("services", "axxium", "volumes", 0, "read_only"), True),
            (("services", "axxium", "depends_on", "database", "condition"), "service_started"),
            (("services", "database", "image"), "postgres:other"),
            (("services", "pds", "image"), "pds:other"),
            (("networks", "default", "name"), "axxium-other"),
        ):
            with self.subTest(path=path):
                candidate = deepcopy(self.candidate)
                container = candidate
                for part in path[:-1]:
                    container = container[part]
                container[path[-1]] = value
                with self.assertRaises(ValueError):
                    profile.validate_projection(
                        self.incumbent, candidate, image_id=IMAGE, recipient_did=RECIPIENT,
                    )

    def test_incumbent_must_have_expected_stream_only_flags_and_webhook_binding(self):
        for key, value in (
            (STREAM, "false"),
            (STREAM, True),
            (SUBSCRIPTIONS, "true"),
            (COEXISTENCE, "false"),
            (COEXISTENCE, None),
            (WEBHOOK_ACTOR, OTHER_RECIPIENT),
            (WEBHOOK_KEY, ""),
        ):
            with self.subTest(key=key, value=value):
                incumbent = deepcopy(self.incumbent)
                incumbent["services"]["axxium"]["environment"][key] = value
                candidate = deepcopy(incumbent)
                candidate["services"]["axxium"]["environment"].update({
                    SUBSCRIPTIONS: "true",
                    COEXISTENCE: "true",
                })
                with self.assertRaises(ValueError):
                    profile.validate_projection(
                        incumbent, candidate, image_id=IMAGE, recipient_did=RECIPIENT,
                    )

    def test_matching_images_must_equal_the_reviewed_image_id(self):
        with self.assertRaises(ValueError):
            profile.validate_projection(
                self.incumbent, self.candidate, image_id=OTHER_IMAGE, recipient_did=RECIPIENT,
            )


class EventsCensusTests(unittest.TestCase):
    def test_accepts_exactly_six_integer_zeros(self):
        counts = dict(CLEAR_CENSUS)
        profile.validate_census(counts)
        self.assertEqual(counts, CLEAR_CENSUS)

    def test_rejects_each_missing_count_and_an_extra_count(self):
        for key in CLEAR_CENSUS:
            with self.subTest(key=key), self.assertRaises(ValueError):
                profile.validate_census({
                    name: value for name, value in CLEAR_CENSUS.items() if name != key
                })
        with self.assertRaises(ValueError):
            profile.validate_census({**CLEAR_CENSUS, "unknown-count": 0})

    def test_rejects_any_callback_work_and_zero_lookalikes(self):
        for key in CLEAR_CENSUS:
            for value in (1, -1, False, True, 0.0, "0", None):
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    profile.validate_census({**CLEAR_CENSUS, key: value})


if __name__ == "__main__":
    unittest.main()
