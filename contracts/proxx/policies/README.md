# Proxx relay contracts (Promethean)

This tree is the policy contract set for the **Promethean relay node** — the
central relay of a federated, community-pooled Proxx system. It is configured
separately from:

- `open-hax/proxx` `resources/policies` — the repo **defaults**: single node,
  zero federation/sharing, one user's dashboard + provider routing. Most users
  run the compose files in that repo and edit those policies in place.
- each operator's **deployment** policy tree (e.g. a workstation's
  `services/proxx/resources/policies`) — that node's own contracts, which are
  expected to diverge from both the defaults and this relay set.

Do not point a local/peer deployment at this tree. The relay is its own
deployment with its own grants.

## What the relay is

Community members lease provider credentials to the relay — gemini and ollama
API keys, and leases on openai oauth credentials. The relay grants access back
out to the community according to `runtime/62-relay-access.edn`:

- All **ussyverse** discord server members get `gemma4:31b` with a generous
  average limit (60 requests per 10-minute window), served via the pooled
  gemini + ollama accounts.
- Members with the **Trust** role get unlimited `gemma4:31b`, all models at
  30 requests per 10-minute window when available, and may connect their nodes
  to the relay as peers.
- Everyone else: default deny.

Member nodes that lease credentials may apply **stricter** policies on their
own keys (e.g. whitelists). Stricter member policies are more-specific cases
that remain admissible under the relay policy and must be honored.

## Identity (axxium forward-spec)

Identity and contract ownership migrate to `open-hax/axxium`. Until then the
identity clauses in `62-relay-access.edn` are a target contract
(`:policy.dsl/status :target-contract`), not yet enforced by the runtime:

- Accounts are created from discord and/or github oauth; each subject is an
  axxium DID. Bearer tokens may be minted for API access after account
  creation.
- Node registration generates an SSH keypair node-side; the relay holds
  **public keys only** and SSH tunnels are established with the public key.
  Private keys never reach the relay.
- Peer nodes are always associated with a user. Policy decisions about peer
  actions may use only facts derivable from the owning user's DID-linked
  auth sources (discord username, server membership, server roles, github org
  membership/roles, anything those APIs can derive).
- Policy never sees, and must never decide on, a subject's IP address.

## Layout

`runtime/00-manifest.edn` lists the load order. Files are append/override in
order: facts first, then derived rules, then the root router. More-specific
clauses must precede catch-all clauses. The runtime base mirrors the
`open-hax/proxx` defaults (including `65-federation-routing.edn` — the relay is
the federation hub); `62-relay-access.edn` is the relay-specific layer.

Provider topology (`05-provider-seed.edn` base URLs, llamacpp sidecars, etc.)
is deployment-tuned at relay deploy time; treat the seeds here as the schema,
not the live wiring.
