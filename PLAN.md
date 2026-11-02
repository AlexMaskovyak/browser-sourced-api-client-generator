# Current work

Discover the operations used by the requested application page and validate the
result through an independently generated client.

1. Trace the actual expense page's network and loaded-source paths. Inspect scripts
   loaded for that page across asset origins, inline declarations and hydration
   timing. Preserve contracts and safe diagnostics, never credential values.
2. Fix the generic discovery paths supported by that evidence. Add regressions
   where a page's business operations live outside startup requests. Keep writes
   unexecuted and preserve explicit source/endpoint ownership.
3. Run discovery against Gusto, build only from its output, and verify a generated
   expense-list read with statuses and pagination. Run shared and container tests.

Complete requires a discovered expense retrieval operation and a successful read
through generated code. Startup-only capture or successful packaging is incomplete.
No service-specific operation documents may be supplied to satisfy this check.

## Verified result

The ordinary installed CLI was run against Gusto from a fresh browser login.
Its first-page discovery manifest contains the captured MemberExpenses document.
The ordinary build command generated a client from that manifest, with MemberExpenses
required before publication. An exact rebuild matched, and that newly generated
client returned 100 live expense records with status and pagination metadata.
No older operation documents or generated-file edits were supplied. The existing
authenticated session was retained for subsequent read verification.

Shared checks: 225 tests and 29 clean-container scenarios pass. The first-page
scenario exercises a rendered listing, tab state, asset-origin code, search-driven
API traffic, HAR capture, generation and an independently installed client read.
