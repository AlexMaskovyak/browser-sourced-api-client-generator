# Current work

Verify generated-client authentication against the real browser session.

1. Preserve bounded custom API session headers at their exact origin, and expose
   safe HTTP status/browser failure diagnostics without response bodies or secrets.
2. Test header scope, limits and failure diagnostics, then run the full suite and
   installed container scenarios. Refresh the generated service client.
3. Use one private read-verification session to identify any remaining failure.
   Completed: the service's own requests succeeded while calls without its client
   identification headers received 403. The generated read returned 200 after
   explicitly selecting those headers. No browser transport replacement is needed.
   Custom header selection remains an explicit command input, not inferred metadata.

Complete when the read succeeds or the actual external rejection is identified.
No automatic retries or production mutations are part of this work.
