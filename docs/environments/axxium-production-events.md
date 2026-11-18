# Qualify the production Axxium Events profile

Axxium's active mailbox release already contains native MCP Events. Qualify a
configuration-only transition on that release so ChatGPT's Dot can register
`message.available` while Stealth's Codex receiver continues using SSE.

## Verify the two projections

Append [the Events overlay](../../digitalocean/services/axxium/mailbox-events-production.overlay.yaml)
to the observed active Compose chain for the candidate render. The incumbent
render uses that same chain without the Events overlay. Use identical project
directory, environment files, and pinned image inputs for both renders. Store
the rendered JSON on the audited host in an owner-only directory with mode
0600 files. Compose output can contain sensitive configuration.

Run [the projection and census checker](../../scripts/axxium_events_profile.py)
on both renders and the counts-only receipt for the selected mailbox:

```sh
python3 -B scripts/axxium_events_profile.py \
  --incumbent incumbent.json --candidate candidate.json --census census.json \
  --image-id "$axxium_reviewed_image_id" --recipient-did "$axxium_reviewed_recipient_did"
```

The checker requires the same image and verified recipient on both renders.
SSE stays enabled. The only allowed differences are subscriptions `false` to
`true` and coexistence absent to `true`. The recipient census must match the
selected mailbox and have all six counters at zero. This check validates the
projections and census contents. The release transaction must obtain the count
from the held incumbent immediately before starting the Events worker.

## Protected restart and rollback

Bind the transition to the current healthy active-mailbox receipt, its plan,
the complete live Compose chain including the active image overlay, and fresh
container and retained-state readback. Keep the existing Work identity bundle
and mailbox/identity ledger mounts. Give the transition its own operation ID so
it can hold and reconcile independently of the prior active image upgrade.

Use the protected Deploy Stack host queue and the existing Axxium transaction
for the maintenance fence, stop, retained-state backup, and restart. Observe the
new container ID and candidate environment even when both profiles have the
same image. Verify native SSE and protected mailbox access before recording
the transition as healthy.

The Events worker can make outbound deliveries during startup. The ingress
fence protects inbound routes. Check the retained subscription, eligible
message, retry and attempt counts after the incumbent is stopped under the
hold, then immediately before enabling Events. Use private isolated ledger
copies and synthetic callback data in the rehearsal. Verify a known failure
restores the exact incumbent Compose profile and retains any accepted messages
and acknowledgments, and that an uncertain effect leaves the hold and journal
in place.

The comparison tool has no deployment entry point. A passing local render
supplies projection evidence. Protected admission still needs the exact
reviewed successor plan and a real restart and rollback rehearsal on the
admitted image.

## Verify the exact Dot

After the protected profile is healthy, refresh the installed Axxium Work
Production plugin and read back `message.available` using the existing
owner-delegated OAuth grant. Request the native monitor from the signed-in
target Dot. This event accepts empty arguments and delivers a message ID for
the authorized recipient. A fresh subscription starts at the current sequence.
A live callback to a different target blocks enrollment.

Wait for the provider's signed challenge to succeed. Read back the active
subscription bound to the exact Dot and record its generation. Send one fresh
synthetic message only after both checks succeed. Record the HTTPS delivery
receipt and verify a distinct turn in the exact Dot with the same message ID.
Unsubscribe after the test and verify zero active subscriptions. [OpenAI MCP Events](https://developers.openai.com/plugins/build/mcp-events)
