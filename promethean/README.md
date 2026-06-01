# Promethean service topology

The current production host for this topology is:

```text
error@proxx.promethean.rest
public address: 104.130.159.19
```

Canonical service split:

```text
nginx          ingress / TLS / public hostname routing
proxx          model proxy / federation / OpenAI OAuth lease broker
openplanner    memory / graph / planning API
knoxx          agent backend / frontend / policy runtime
```

The design target is one declared owner per public hostname and one declared runtime root per service. Avoid app repos mutating shared host nginx or shared compose state independently.
