# Translation event scope

The `translation-needed` trigger in `namespaces/publication.edn`, which listens
for `publication/translation-needed`, explicitly opts into the tenant scope
carried by Knoxx's admitted translation event. This mirrors the application-owned
resource exactly. Without the opt-in, the generic agent action uses ambient
configuration and loses the requesting organization and membership before
session hydration and durable run projection.

The companion application change in open-hax/knoxx#305 validates a closed scope and requires
a trusted dispatch bound to the named emitter before using it. Services owns only
the deployment declaration. It does not infer authority or duplicate that runtime
logic. This PR is held until that runtime change is published, reviewed, and
merged; the initial Services review preceded its publication. Deploy this
declaration with the reviewed runtime that implements it.

Verification: parsed EDN equals the application-owned resource, its
`:trigger/with` contains the explicit opt-in, scoped EDN lint reports zero errors
and warnings, and the deployment-boundary scanner and classifier self-test pass.
The first ad-hoc verification expression had a delimiter typo and then used the
runtime action key instead of the resource's trigger key; both harness mistakes
were corrected before these successful checks. These checks do not claim a live
deployment; the complete browser publishing workflow remains the runtime gate.

All three reviewers found that the deployment verifier could accept a resolved
trigger without the new opt-in. The verifier now reads `scopeFromEvent` from the
runtime response and requires it alongside resource policy and execution snapshot
pinning. The static environment contract check also requires this inspection and
refusal. A missing field, explicit false, or stale runtime therefore fails closed.
The regression executes the production shell block: the original version failed
both scope refusal cases, and the repaired version passes all seven cases with
no skips. CI runs this test with the environment contract check. Shell syntax,
environment contracts, and both deployment-boundary checks pass locally.
