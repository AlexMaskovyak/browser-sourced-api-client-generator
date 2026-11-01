# Current work

Make generated Python clients understandable and verify repeatable generation.

1. Review all Python implementation files changed in the PR, documenting component
   contracts and non-obvious validation, inference, ownership and failure decisions.
   Generate class and method docstrings from operation contracts: read/write
   behavior, declared variable types, omission, return values and failure behavior.
   Explain shared runtime ownership and validation limits in its public API.
2. Test documentation on a freshly generated client, including required inputs,
   defaults and mutation permission. Generate twice from identical saved inputs
   and compare output bytes. Run the full suite and container checks.
3. Regenerate the Gusto package from its saved normalized manifest, compare it
   with the repository, and update both PRs. Keep private inputs out of Git.

The generator owns executable SDK files. Repository packaging, tests and prose
remain separate unless the comparison proves they are generated. Do not perform
live writes or claim whole-repository reproducibility from a package comparison.

## Verification

The implementation documentation review is complete. The generator emitted two
byte-identical 27-file Gusto projects from the same saved normalized manifest.
The SDK package is copied entirely from that output, with no hand-edited methods.
Repository prose, development tests and Poetry lock remain maintained separately.
Required custom session-header names remain explicit runtime flags.
