# Current work

Fix schema discovery after third-party requests fill the session-header budget.

1. Reproduce late GraphQL requests after headerless origins and after a full
   authenticated-origin budget. Keep endpoint discovery independent of storage.
2. Store only selected nonempty session headers; retain existing origin and byte
   bounds. Verify focused tests, full suite, builds and installed Docker flows.
3. Open a review PR and rerun private discovery with a fresh login.

Complete when the GraphQL endpoint is probed, the budget remains bounded and
client generation either succeeds or reports the actual missing definition.
