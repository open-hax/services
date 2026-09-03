# Knoxx Git event bridge

Services now owns one small, executable Git-to-Knoxx path:

```text
GitHub push / merged pull request / manual verification
  -> .github/workflows/knoxx-git-events.yml
  -> authenticated POST /api/admin/config/events/dispatch
  -> github_repository_events generator provenance
  -> git_change_knowledge_review trigger (merged PR or manual verification)
  -> existing :actions/start-agent-session runtime action
  -> git_event_knowledge_worker session
```

The workflow runs pull-request dispatch only after a same-repository branch has
merged, and it always checks helper code out from the default branch before
exposing the protected production API key. Manual dispatch is restricted to
`main`. The workflow sends metadata only. Pull-request titles, descriptions,
comments, commit messages, patches, credentials, and repository contents are
excluded from the event payload. The knowledge worker also treats every
payload field as untrusted data and is instructed not to mutate or deploy
anything.

## Production boundary

`scripts/dispatch-knoxx-git-event.sh` is the external adapter. It authenticates
with the protected production `KNOXX_API_KEY`, and Knoxx resolves that key to
the `knoxx_dev_automation` system actor before permitting
`org.events.control`. The event itself names that actor so the trigger's emitter
agreement is explicit.

The generator resource declares provenance and emitted event types. It does not
pretend to be a long-lived source driver. The trigger invokes the already
registered `:actions/start-agent-session` behavior; the action resource merely
advertises that existing registry entry.

## Deliberate blocker: no `:driver/git` source yet

The Knoxx source pinned by `.github/workflows/deploy-stack-chain.yml` registers
Discord, eta-mu ingestion, agent, Knoxx, user, organization, session, and
translation drivers. It does not register `:driver/git`. A source resource that
claimed that driver would pass permissive EDN parsing but fail runtime
admissibility with a missing driver. Services therefore does not ship one.

Continuous GitHub App/webhook ingestion needs an application change in Knoxx:

1. implement and register a Git driver with code-owned normalized event shapes;
2. test signature verification, replay/deduplication, actor ownership, and
   delivery failure behavior in Knoxx;
3. only then add a Services `:source/type :event-generator` resource referencing
   that registered driver.

The same rule applies to new actions. Knoxx currently has no registered
`:actions/http`, `:actions/git`, or webhook-response action, so Services does not
declare any of them. New executable behavior must land and be tested in Knoxx
before a production EDN resource may advertise it here.
