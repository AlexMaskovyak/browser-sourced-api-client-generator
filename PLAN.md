# Current work

Generate standalone clients from captured requests and named operations. Schemas
are optional enrichment, not a prerequisite for an operation-document client.

1. Parse and combine operation documents into validated immutable contracts.
   Preserve endpoints, variables, fragments, defaults and read/write classification.
2. Generate standalone Python methods and CLI help using the shared authentication
   runtime. Validate known variable constraints; report unknown input/output types.
3. Make manifest normalization and generation use this path automatically when a
   complete schema is unavailable. Retain schema-based generation when supplied.
4. Verify exact requests, omission versus null, malformed documents, write guards,
   file/pipe use and independently installed clients in Docker. Generate a private
   client from the saved live capture; keep live evidence out of the public repo.

Complete when the normal command generates from the capture without a schema,
client methods and help work, and tests/builds/container checks pass in a new PR.
