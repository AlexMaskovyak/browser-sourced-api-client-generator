# One dashboard link

1. On the normal `website-client login` command, publish the initial login client.
   After interactive login, inspect the dashboard once and select its first eligible
   same-origin page link in document order. Discovery must send zero link requests.
2. Give its literal GET path, or an empty paths object, to the existing OpenAPI
   generator. Upgrade the initial client in its original directory after generation succeeds.
3. On the local HTTPS fixture, run the generated method after fresh login and compare
   the response body with the protected page. With no links, run the emitted login
   command and confirm the dashboard visit. Run the full local suite before opening the PR.

# Current work

## Linked-page instructions repair

1. Keep the OpenAPI definition and generated request builder authoritative for the
   page request. Render linked-login instructions in private staging before
   publication, using only the website origin and selected literal path.
2. Give the reader isolated installation steps and one exact operation command,
   with a newly entered login URL and raw HTML JSON output expectations. Preserve
   zero-link and other generation instructions; update the repository entry point.
3. Add a synthetic generation regression for the exact origin, GET path, install
   command, secret exclusion and absence of competing examples. Run affected
   generation tests; coordinator runs the full suite before handoff.

Make generated Python clients understandable and verify repeatable generation.

1. Review all Python implementation files changed in the PR, documenting component
   contracts and non-obvious validation, inference, ownership and failure decisions.
   Generate class and method docstrings from operation contracts: read/write
   behavior, declared variable types, omission, return values and failure behavior.
   Explain shared runtime ownership and validation limits in its public API.
2. Test documentation on a freshly generated client, including required inputs,
   defaults and mutation permission. Generate twice from identical saved inputs
   and compare output bytes. Run the full suite and container checks.
3. Regenerate the example package from its saved normalized manifest, compare it
   with the repository, and update both PRs. Keep private inputs out of Git.

The generator owns executable SDK files. Repository packaging, tests and prose
remain separate unless the comparison proves they are generated. Do not perform
live writes or claim whole-repository reproducibility from a package comparison.

## Verification

The implementation documentation review is complete. The generator emitted two
byte-identical 27-file example projects from the same saved normalized manifest.
The SDK package is copied entirely from that output, with no hand-edited methods.
Repository prose, development tests and Poetry lock remain maintained separately.
Required custom session-header names remain explicit runtime flags.
