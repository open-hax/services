# Pinned Knoxx controller maintenance

Knoxx currently calls the Services environment controller at
`f9bfe1724a662930fc10fcb9ba0f46eac78fbc52`. This maintenance line starts at that
exact commit. Its intended target is `codex/knoxx-controller-maintenance`; it does
not merge Services PR #83, its later policy changes, or Services main into the
caller. Services owns this workflow/toolchain repair; Knoxx owns its subsequent
reviewed caller-pin change and observed staging result.

The only runtime change installs clj-kondo `2025.07.28` in the existing
credential-free build job. Knoxx's backend tests execute real clj-kondo hooks.
At Knoxx `51d196bdf2440b758798552385ab415ade2226f1`, Node `24.14.1` and pnpm
`10.20.0`, the same 72 JavaScript tests produced 67 passes / 5 failures when
clj-kondo was absent and 72 passes / 0 failures with that pinned binary present.
The workflow's test stages and deployment separation remain intact.

Evidence: [failed staging build](https://github.com/open-hax/knoxx/actions/runs/35498820448).
This is a missing-tool repair, not a successful whole-image or staging deployment
qualification. Observe the actual resulting run before claiming recovery.

Before merging a maintenance PR, require current-head successful
`Validate service definitions` and `coderabbit-review-gate` checks, completed
CodeRabbit and Codex reviews, and no actionable findings or unresolved
conversations. Confirm the exact head and base, disable any queued auto-merge,
and use a normal guarded merge. The maintenance branch does not inherit the
repository's `main`/`staging` protections; the maintainer must enforce these
conditions explicitly without changing global settings.

After that merge is confirmed, a separate reviewed Knoxx change can replace all
four matching workflow/controller pins with the exact maintenance merge SHA.
Keep both `uses` references and both `controller_sha` values identical. A reusable
workflow SHA need not descend from main; preserve this maintenance branch as the
reviewed pin's ownership record.

Services issues [#85](https://github.com/open-hax/services/issues/85),
[#86](https://github.com/open-hax/services/issues/86), [#87](https://github.com/open-hax/services/issues/87),
[#88](https://github.com/open-hax/services/issues/88), [#89](https://github.com/open-hax/services/issues/89),
and [#90](https://github.com/open-hax/services/issues/90) retain their existing
admission, deployment and policy ownership. This patch neither
changes those behaviors nor resolves those findings. Future maintenance should
remain a minimal diff from the pinned line; importing other branches requires its
own explicit scope and qualification.
