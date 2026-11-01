# Current work

Make generated Python clients understandable and verify repeatable generation.

1. Generate class and method docstrings from operation contracts: read/write
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
