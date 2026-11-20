# Knoxx staging embedding profile

[The staging profile](embeddinggemma2-profile.json) pins the OpenPlanner SDK
source and the five destination collections for `embeddinggemma-2` at 768
dimensions. The 1,024-dimensional legacy profile and the Gemma profile read
the same `events` collection.

Run the rehearsal from the Services checkout. Supply an OpenPlanner checkout
with the pinned source revision available, its SDK dependencies installed, and
the document hydration package built. The rehearsal uses the pinned Atlas Local
image and an existing local Proxx container with a loopback listener and
authentication.

```sh
python3 scripts/verify-knoxx-embeddinggemma-index-fixture.py /path/to/openplanner \
  --local-proxx-container proxx-local-proxx-1
```

The runner compiles the exact SDK in a temporary directory and owns the
disposable database. It creates two synthetic source events. The legacy
provider returns deterministic vectors and existing Proxx serves the Gemma
vectors. The receipt reports the source ID set and digest, coverage in both
hot and graph profiles, compact summary membership, ordinary SDK search, and
native graph search. A repeated Gemma replacement checks exact preservation of
the legacy vector rows, graph index, and compact summary. Both existing
production writer guard refusals are exercised against the fixture contract.

The deployed staging slot still uses stock MongoDB 7. A stage activation needs
a Search-capable staging database, private Proxx connectivity, and an admitted
Knoxx artifact carrying the reviewed SDK. Historical migration needs coverage
of the actual source snapshot and writes admitted after that snapshot.
