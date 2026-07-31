# DigitalOcean deployment

This directory declares the DigitalOcean host lifecycle for the OpenHax service stack.

The initial deployment uses one Ubuntu Droplet for ingress and application runtimes. MongoDB remains an external dependency. Vector search is optional and disabled unless a separate backend is configured.

## Lifecycle

1. Provision the Droplet through the DigitalOcean control plane.
2. Record its address and Droplet ID in `hosts/production.yaml`.
3. Add the matching private key to the GitHub `production` environment as `DIGITALOCEAN_SSH_PRIVATE_KEY`.
4. Run `Bootstrap DigitalOcean Host`.
5. Deploy Proxx, OpenPlanner, Knoxx, and ingress.
6. Run `Verify DigitalOcean Host` and retain its JSON artifact.

## Secrets

No private keys, Requesty tokens, MongoDB credentials, Proxx credentials, or application `.env` files belong in this repository. Store them in GitHub Environments or on the host with root-only permissions.
