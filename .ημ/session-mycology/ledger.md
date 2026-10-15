- ts: 2026-08-29T15:12:13Z
  session: services-pr58-and-pr44-completion
  task: Finish authenticated dev ingress and disposition the stale lifecycle proposal
  p-efficiency: 0.82
  p-friction: 0.61
  p-skill-candidate: 0.82
  spore: 20260829-151213-superseded-pr-disposition.md
  receipt-refs: 2026-08-29T14:56:11Z, 2026-08-29T14:59:02Z, 2026-08-29T15:05:43Z
  note: Mapping every still-valid review finding into acceptance-grade successor cards made a stale architecture PR safely closable without discarding its evidence.
- ts: 2026-10-15T09:07:51.189014195Z
  session: 01a12257-fc69-7720-888c-dd2595ff7dc7
  task: Services inherited-Caddy-filecap source repair and honest native private UI qualification
  p-efficiency: 0.58
  p-friction: 0.82
  p-skill-candidate: 0
  spore: none
  receipt-refs: receipts.edn#93dfb30a-df9b-4f3a-a9e3-287278c56e5c
  note: Bind immutable source bytes and Unix modes before Docker COPY; public Caddyfile mode must remain readable by the final nonroot UID. Preserve effective filecap mismatch RED, remove capability during build before USER, and retain cap-drop ALL/NNP at runtime. On internal-only Docker bridges, distinguish requested HostConfig mapping from actual allocation; attach only frontend to an owned normal bridge for loopback UI access, preserving DB/model isolation. Protected401/no_session proves auth boundary, not dependency health. Actual normalUI login200 and CMSshell screenshot passed; Spanishrow tour failed and remains unqualified. New diagnostic is proposed, no false fullpass, candidate0/no spore.
