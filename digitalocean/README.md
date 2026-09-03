# DigitalOcean deployment

This directory declares the DigitalOcean host lifecycle for the OpenHax service stack.

The initial deployment uses one Ubuntu Droplet for ingress and application runtimes. MongoDB remains an external dependency. Vector search is optional and disabled unless a separate backend is configured.

## Lifecycle

1. Provision the Droplet through the DigitalOcean control plane.
2. Record its address and Droplet ID in `hosts/production.yaml`.
3. Add the matching private key to the GitHub `production` environment as `DIGITALOCEAN_SSH_PRIVATE_KEY`.
4. Manually dispatch `Deploy Stack` for the initial host. Its first stage
   installs the pinned Ollama runtime, creates its backend-only `knoxx-ollama`
   bridge and firewall rule, binds Ollama only to that bridge, and pulls the two
   digest-pinned models required by Knoxx.
5. For later releases, merge a reviewed Services PR carrying `deploy`, or
   manually dispatch `Deploy Stack`. The chain repeats that idempotent host
   bootstrap under the same production-host lock, which remains held until the
   whole deployment finishes. Image builds may run alongside bootstrap, but no
   service deploy starts until it passes; Proxx, Knoxx, Caddy, and the website
   then deploy in dependency order.
6. Manually dispatch `DigitalOcean Host` to run its verify-only production
   check and retain the JSON artifact. Production bootstrap is deliberately
   available only through the locked `Deploy Stack` orchestrator.

## Secrets

No private keys, Requesty tokens, MongoDB credentials, Proxx credentials, or application `.env` files belong in this repository. Store them in GitHub Environments or on the host with root-only permissions.
