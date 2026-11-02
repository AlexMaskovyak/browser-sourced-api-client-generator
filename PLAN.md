# Current work

Generate callable write operations discovered from a page's creation forms without
submitting those forms during discovery.

1. Inspect discovery limits, control ordering, source collection and the handoff
   from frontend declarations to client generation. Keep the logged-in browser.
2. Prioritize form-opening controls, retain bounded lazy-loaded source contracts,
   and make their API association explicit rather than dropping unexecuted writes.
   Add narrow regressions for the actual failures found.
3. Run shared and container checks, repeat discovery in the retained browser, and
   build from its manifest. Verify generated write commands and confirmation
   requirements without submitting a real expense.

Browser traffic and loaded source own the discovered contracts. The manifest owns
replayable generation input. Generated projects remain unedited. Submission and
receipt upload are distinct contracts; a mutation alone does not prove the whole
workflow. Missing or ambiguous contracts must remain visible. Complete requires
write contracts to survive discovery, normalization, build and command help, with
no real business write during verification.

## Verification

- Creation controls are prioritized; hidden controls are filtered before the
  element limit. Creation links and explicitly non-submitting form buttons are
  inspected, and per-page action budgets leave room for queued graph edges.
- Source inspection accepts up to 4096 sources, 4 MiB each and 32 MiB total, with
  limits and omissions reported. Separate literal fragment definitions complete
  operations only when their dependencies are unambiguous and acyclic.
- A single explicit GraphQL endpoint assigns frontend declarations. Observed
  operation names retain their captured definitions over source variants.
- All 233 tests pass locally and in Docker. All 29 container scenarios pass.
  The submission scenario loads seventy shared chunks and hidden controls,
  opens a creation screen, discovers its mutation and response fragment, builds
  and independently installs the client, rejects an unconfirmed write, then
  receives the expected created record from the local test service.
- Retained-session Gusto discovery exported CreateMemberExpense with its response
  fragments. Standard import/build commands consumed that discovered operation
  and the expense-list capture without generated-file edits. The generated
  project passes 15 tests and reproduces 32 byte-identical files.

Live Gusto writes and receipt-upload orchestration remain unverified. The cached
retained-session controller does not reload the capture-manifest module, so the
new automatic endpoint assignment was verified through the actual CLI in Docker,
not represented as a new complete URL-to-client Gusto run.
