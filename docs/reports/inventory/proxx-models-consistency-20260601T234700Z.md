# Proxx public model metadata consistency check

- time: 2026-06-01T23:47Z
- target: `https://proxx.promethean.rest/v1/models`
- auth: production Proxx bearer token read from the production container environment; value not recorded.
- pre-fix symptom: repeated public probes alternated between `200` and `401`.
- root cause: production and staging Proxx containers shared a Docker network and both registered bare aliases such as `federation-proxx-a1`; production federation nginx could resolve either environment.
- fix: production and staging federation nginx configs now use project-specific container DNS names.
- result: 20 repeated probes returned 20 HTTP `200` responses.

Conclusion: public model metadata is stable after decoupling federation nginx DNS targets by compose project.
