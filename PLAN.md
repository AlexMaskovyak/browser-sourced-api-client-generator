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

## Result

The complete builder now owns every output file. Gusto was generated from two
saved manifests through the build command, then rebuilt from its shipped project
file. All 34 files match byte for byte. Its untouched Poetry installation,
17 generated checks, wheel/source builds and endpoint help passed. Shared checks:
210 tests and 26 Docker scenarios. No live operation was sent by this build.
