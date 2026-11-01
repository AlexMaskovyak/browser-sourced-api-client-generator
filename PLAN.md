# Current work

Replace mutable normalization dictionaries with validated dataclasses.

1. Add immutable records for manifest documents, APIs and metadata. Preserve the
   version 1 JSON contract; reject invalid types, hashes and references on entry.
2. Normalize through records and explicit replacements. Keep JSON conversion at
   the command boundary and preserve unresolved evidence and producer history.
3. Test malformed data, mutation attempts, round trips and existing pipeline
   behavior. Run the full suite, package checks and installed Docker scenarios.

Complete when normalization uses the records, existing manifests still generate
both client formats, and the checks pass. Push the review fix to PR 7.
