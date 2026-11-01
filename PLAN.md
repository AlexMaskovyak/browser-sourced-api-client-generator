# Current work

Produce a complete installable Python client from one repeatable project command.

1. Add a versioned project file referencing portable input manifests, runtime
   defaults and an optional existing Poetry lock. A build command merges and
   normalizes the inputs, invokes generation and publishes one complete project.
2. Generate runtime defaults, source inputs, README, synthetic checks and Poetry
   development configuration. Keep capture lineage private and publish only hashes.
   Never execute live operations during a build or embed credential values.
3. Add a public Gusto project definition with its reviewed operation/request inputs
   and header names. Build twice, compare every output file, install/test/build the
   untouched result and document exact commands. Run full tests and Docker checks.

The project file is the reproducible source of configuration. The generator owns
all output files; no post-generation copying, source edits or metadata cleanup.
Coverage is the captured operation set, not an assertion of every service endpoint.
