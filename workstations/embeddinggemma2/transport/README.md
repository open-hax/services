# Inactive private embedding transport fixture

These native Caddy configurations are fixed transport adapters, independent of
the worker profile. They do not install or activate a listener, system unit,
tailnet rule, certificate or production network. The existing Proxx remains the
model router. The two intended production aliases remain
`embeddinggemma-yoga:8081` and `embeddinggemma-stealth:8081`.

The provider validates a private client certificate and exact consumer source
before forwarding. The consumer validates its approved local source, provider
CA and exact TLS server name. Both limit method/path, request bytes and upstream
deadlines, and strip ambient authorization/cookie headers before the fixed
native origin. Every address must be supplied explicitly; there is no public
bind default. Caddy admin and automatic HTTPS are off.

Run `python3 scripts/verify_embeddinggemma_transport_fixture.py` with Docker,
OpenSSL and the two immutable cached image IDs declared in the script. It
does not pull images. Optimized Python invocation is refused before creating
resources because the fixture's admission and ownership guards use assertions.

Only the existing Proxx may choose the requested model. Caddy does not parse
JSON or make a model-routing decision. Each provider must target one separately
verified immutable model worker. Native-worker rejection of a wrong model must
be qualified before activation if it is part of the worker contract; a synthetic
fixture rejection must never be presented as proof of llama.cpp behavior.

The fixture creates an internal Docker network without host ports, synthetic
test-only CA/server/client keys, a synthetic origin, two native transport peers
and a synthetic approved consumer. It exercises actual TLS/source denial before
upstream, allowed paths, blocked paths/methods, header stripping, body limits,
upstream timeout and removal/restoration of only the owned provider. It never
uses a real model, provider credential, subscription, tailnet listener or shared
router. Its result qualifies the transport mechanics only.

Wrong-server-name and wrong-server-CA negatives include labeled direct Python
TLS controls and actual Caddy-consumer configuration probes. Only the owned
consumer is stopped, given the wrong name or CA in its task-owned config copy,
and restarted. Each request must fail before the synthetic origin is reached.
The exact valid consumer config is restored and its successful path rechecked.
These native transport checks still do not qualify a real deployment or model.

The live installation still needs a reviewed immutable image pin, exact host
and service identities, host-local private credentials, boot lifecycle owner,
worker freshness and mount checks, effective tailnet permission, independent
revocation/rollback, resource qualification and an exact existing-Proxx
preference/fallback/restoration proof. No harness SSH key may become a daemon
credential. Existing Serve/Funnel443 endpoints remain untouched.
