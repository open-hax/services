# Proxx public model metadata consistency check

- time: 2026-06-01T23:47Z and 2026-06-01T23:50Z
- target: `https://proxx.promethean.rest/v1/models`
- auth: production Proxx bearer token read from the production container environment; value not recorded.
- pre-fix symptom: repeated public probes alternated between `200` and `401`.
- root cause: production and staging Proxx containers shared a Docker network and both registered bare aliases such as `federation-proxx-a1`; production federation nginx could resolve either environment.
- fix: production and staging federation nginx configs now use project-specific container DNS names.
- initial result: 20 repeated probes returned 20 HTTP `200` responses.
- central workflow validation:
  - nginx deploy: https://github.com/open-hax/services/actions/runs/26789263926
  - Proxx federation nginx deploy: https://github.com/open-hax/services/actions/runs/26789264724
- post-workflow result: 20 repeated probes returned 20 HTTP `200` responses.

Conclusion: public model metadata is stable after decoupling federation nginx DNS targets by compose project and making the service repo own ingress redeploys.
