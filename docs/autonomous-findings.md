# Autonomous integration verification and findings

The authenticated runtime, autonomous discovery/generation, restricted workflow
distribution and clean-container acceptance are complete and independently reviewed.
The original acceptance findings below are resolved. [PR #12](https://github.com/AlexMaskovyak/browser-sourced-api-client-generator/pull/12)
contains the implementation; it has not been merged or applied to a real service.

## November 22 follow-up: example discovery and missing client assets

The user's run at `6896b63` produced `unsupported/no_executable_operations`, zero
services and workflows, and only JSON reports. Its saved report records 411 blocked
cross-origin GET requests, including dashboard JavaScript, one page, no controls,
and six failed definition probes. Those facts do not establish every cause of the
example failure. No successful authenticated example rerun is claimed.

- The strict origin check blocked browser-classified CDN assets before the asset
  rule could apply. A neutral browser reproduction failed with zero services before
  the change. At `73066a1`, it generates and calls a client; the 24 focused checks
  also cover redirects, asset types, undeclared API calls, writes and private profiles.
- Unsuccessful generation lacked developer-facing output. Follow-up checks require
  a README and runnable offline stub for empty discovery, malformed input, missing
  dependencies, unexpected failure and cancellation after output acceptance. The
  stub preserves unsupported coverage and never counts as a usable API service.
- Local acceptance now requires a CDN script for GraphQL discovery and exercises
  unsuccessful output as well as installed working clients. Final verification of
  the follow-up is recorded separately from the original container evidence below.

## Current verification

Tests used Python 3.12.14 and isolated Poetry 2.5.1. Commands below show `poetry` on
PATH; the execution environment used `work/poetry-env/bin/poetry`. That private tool
path is not a shipped helper. Another developer can use the supported Poetry
installation described in the README.

| Revision | Check | Result |
| --- | --- | --- |
| `fb218f4` | `poetry run pytest -q` | Baseline: 205 passed in 10.89s. |
| `7d90c49` | Full pytest | Runtime milestone: 227 passed in 24.11s; independent corrections and five failure probes passed. |
| `d9d4d07` | Full pytest | Discovery milestone: 270 passed in 58.80s; independent browser, offline, origin and renewal checks passed. |
| `d4d9bd1` | Fixed-archive `PYTHONPATH=src .venv/bin/python -m pytest -q` | Pushed guard checkpoint: 303 passed in 62.47s. |
| `7f0984f` | Same fixed-archive full check | Native dependency/guard checkpoint: 315 passed in 69.10s. |
| `ac92e7a` | Full pytest | First quality checkpoint: 318 passed in 68.23s; independent 48-test check and 18 workflow probes passed. |
| `32ad5ea` | `poetry run pytest -q` | Latest quality checkpoint: 323 passed in 68.93s. |
| `32ad5ea` | `poetry check`; `poetry run ruff check`; `poetry run ruff format --check` | Passed. Ruff is a direct development dependency and checks 16 specified integration modules. |
| `32ad5ea` | Independent CLI/browser/discovery/pipeline and failure checks | 87 passed; actual autonomous generation, schema-only generation and help passed. |
| `85019f9` | Fixed-archive full pytest | Explicit source associations: 330 passed in 68.08s. |
| `f6072a1` | Fixed-archive full pytest | Standalone workflow integration: 342 passed in 81.83s. |
| `f6072a1` | Author package tests; Poetry check; Ruff lint/format | 12 package tests passed, including installed Python/CLI and separate origins. Poetry and Ruff checks passed for 23 integration files. |
| `a2037ab` | Fixed-archive full pytest | Corrected workflow integration: 347 passed in 87.57s. |
| `a2037ab` | Independent correction checks | Reviewer: 30 passed in 24.36s; adversary: 5 passed in 3.07s; Ruff lint/format passed for 24 files. |
| `d27827c` | Fixed-archive host full pytest | 351 passed in 93.98s. |
| `d27827c` | Independent package support review | Two installed-wheel tests and six generated checks passed; actual input help, notices and portable metadata verified; Ruff passed for 27 files. |
| `2b9c062` | `scripts/test-docker.sh` | Exit 0: 351 tests in 114.73s, original HTTPS compatibility and standalone acceptance. Clean working tree; source unchanged throughout. |
| `2b9c062` | Independent final container evidence/image inspection | Accepted: 29 authorized requests plus one expired attempt; 14 file instances all match the 274-byte fixture/hash; 44 wheel hashes and nine generated checks verified. |

The independent review accepted the implemented code's reusable components,
validated dataclasses, private-safe representations, comments, spacing and function
responsibilities. No static type checker is configured or claimed. No GitHub checks
are reported for this branch. Installed clean-container acceptance passed. Its JSON
records exact commands, versions, image IDs, wheel hashes and a credential-free
request journal. The standalone fixture uses explicit loopback HTTP; HTTPS trust
is tested separately by the preserved compatibility program.
The exact [standalone acceptance JSON](verification/standalone-acceptance.json)
is checked in with the tested revision, image identifiers and credential-free
execution evidence. Later delivery commits change documentation only.

## Resolved findings

All entries below were reproduced or identified by source inspection, corrected,
and verified. Git history retains the earlier investigation and correction records.
The referenced normal tests are self-contained; research checkouts are not test
prerequisites.

| ID | Location and reproduction | Impact | Correction and verification |
| --- | --- | --- | --- |
| F1 | `authenticated_http`: disconnect after commit exposed cookie/bearer values in Playwright exception text and traceback. | Private credentials escaped diagnostics. | Bounded transport exception without upstream chain; one request/effect and no retry. Corrected at `7d90c49`; authenticated-runtime tests and independent failure probes passed. |
| F2 | `client_cli.show_failure`: pre-call ProfileError entered the generic ValueError branch. | Missing authentication lost its machine-readable reason. | Handle profile errors first. Missing/mismatched-profile CLI tests and independent direct reproduction passed. |
| F3 | `native_files`: class-name matching treated ordinary JSON models named File/Upload as upload objects. | Valid model inputs failed. | Match actual upstream native types; model-name collision, alias, omission and real-upload regressions passed. |
| F4 | `admission`: a nested local component reference retained an unrelated remote sibling. | Usable methods were lost. | Preserve only required reference dependencies. Nested-reference and mixed-operation tests passed. |
| F5 | `admission`: an unused sensitive security scheme excluded an unauthenticated read. | Independent methods were lost. | Follow effective security requirements and remove unused sensitive structure. Admission regressions passed. |
| F6 | `admission`: `post: null` raised AttributeError. | A malformed method aborted usable siblings. | Bounded invalid-contract exclusion. Mixed valid/malformed operation regression passed. |
| F7 | `graphql_admission`: snake-case-only names missed native class_/from_ symbols and collisions. | One field broke commands/help for the whole client. | Use Ariadne's naming behavior and exclude ambiguous symbols. Actual generated builder/CLI regressions passed. |
| F8 | `graphql_admission`: removing a sensitive interface left a dangling implements reference. | Independent safe queries were lost. | Close type/interface exclusions while retaining independent roots. Schema-only regression passed. |
| F9 | `admission`/`privacy`: metadata filtering rewrote wire fields named description/default and workflow JSON literals. | Request contracts changed silently. | Context-aware filtering with named per-kind helpers. Wire-property, literal-payload and legacy-output regressions passed. |
| F10 | `autonomous`: merge recovery ignored endpoint/protocol conflicts or colliding artifact content. | An arbitrary contract/endpoint could be selected. | Quarantine affected services and preserve independent ones. Endpoint/artifact conflict tests and independent correction probes passed. |
| F11 | Named GraphQL admission dropped supported queries alongside subscriptions, fell back to schema-only generation, retained orphan fragments, or omitted privacy-loss reasons. | Wrong generation route or silent coverage loss. | Admit named operations independently, prune unreachable fragments with native GraphQL helpers and report exclusions. Typed/schema-less/fragment/privacy regressions passed. |
| F12 | Privacy filtering removed safe captured GraphQL GET and operationName query conventions. | Two full-suite regressions changed actual requests. | Preserve only the owning compiler's safe protocol metadata. Actual captured GET regression passed; full suite is green. |
| F13 | Initial profile seeding copied cookies across same-host ports; an initial correction then discarded the last response's renewed cookie. | Credential isolation or unattended handoff failed. | Attribute native cookie issuance to its origin, exclude ambiguity, retain renewed native state. Exact two-port and OLD-to-NEW browser-closure/CLI probes passed at `d9d4d07`. |
| F14 | Workflow admission required declarations absent from the supplied working workflow. | Normal input-schema repair would have been required. | Restore the original fixture, JSON Schema permissive defaults and runtime reference-presence validation. Admission and engine tests passed. |
| F15 | Native Arazzo 0.9.6 sent an empty body for schema-valid whole JSON null. | Required literal-null semantics failed. | Versioned native companion fix, preserving omitted/nested bodies. OpenAPI 3.0/3.1 wire probes, schema rejection and portable generator metadata checks passed. See dependency provenance below. |
| F16 | Workflow source validation rejected an entire source for unused remote components. | Independent workflows were lost. | Validate selected dependency closure and prevent external retrieval. Workflow admission regressions passed. |
| F17 | Overlapping quiet-runner scopes restored logging early and exposed a credential canary. | Concurrent jobs could leak diagnostics. | Shared interpreter-wide reentrant suppression lock with correct restoration. Independent threaded/copy-runtime regressions passed. |
| F18 | Native multipart construction stringified object values before wire validation. | Invalid typed input reached the server. | Validate native resolved values before coercion using native evaluator/parameter processing. Literal, input and prior-step-output regressions reject before upload. |

Design review also required non-persistent login, one authenticated dispatch owner,
all-ready-service generation, schema-only native builders, private profiles,
public Python defaults and usable-operation survival after upstream warnings.
Those constraints remain in the architecture and passing milestone tests.

## Resolved workflow packaging findings

Independent review of `f6072a1` passed 19 focused checks and Ruff's 23 integration
files. Bounded adversarial probes then established the following defects. All three
are corrected and independently rechecked at `a2037ab`.

| ID | Location and reproduction | Impact | Correction and verification |
| --- | --- | --- | --- |
| F19 | `workflow_cli._write_result`: close an installed command's stdout pipe, then execute a successful workflow. Three requests and one commit finish; interpreter shutdown raises BrokenPipeError and exits 120. | The local output stage loses its machine-readable remote outcome and promised exit code. | Shared typed `result_output` flushes within the guarded stage and prevents a second shutdown failure. Installed SDK/workflow probes exit 1, preserve the completed remote outcome and make no retry. Corrected at `2926d63`. |
| F20 | `workflow_cli._exit_code`: invoke an installed workflow without its required file input. It reports missing_input with zero requests, but exits 1. | Caller input error disagrees with exit-2 help and the required convention. | Predispatch caller-input rejections return 2; workflow/remote failures retain 1. Installed missing-file probe verifies zero requests/effects. Corrected at `2926d63`. |
| F21 | `workflow_binding._resolve_sources`: supply a workflow's exact declared local OpenAPI file and a saved report pointing that source name at a different OpenAPI file. The saved report wins without an exclusion. | Contradictory contract evidence selects the wrong destination. | Combined candidate conflicts are quarantined and preserved through sanitization/offline regeneration; compatible same-document endpoint overrides remain usable. Independent probes pass. Corrected at `a2037ab`. |

The wrong-origin private-profile adversarial probe passed with no dispatch. These
findings concern the new packaging integration, not a change to the selected native
upstream owners or the architecture.

## Resolved native generation failure

F22: The first standalone container run reached initial login, then the ordinary
installed `website-client run URL` command returned its bounded unsupported-input
error. The public API traceback identified a valid optional nullable JSON request
body in the fixture definition: OpenAPI Python Client emitted an API module with
an empty `else` block, and `generation.operation_metadata` raised SyntaxError while
parsing it. One unsupported native SDK operation aborted its entire service.

The correction at `8999e1d` feeds a bounded syntax diagnostic into the existing
per-operation native backend checks, excludes the unsupported SDK operation and
regenerates the final project. No invalid Python module remains in the SDK. No
serializer repair or upstream port was added.

Independent verification passed two native-generation regressions and the native
null/omitted/nested/schema-rejection workflow case. A separate integrated offline
probe confirmed that the SDK keeps its usable read, the workflow retains its nullable
operation/source, and supplied source bytes remain unchanged. Diagnostics contain
only bounded reason/type/level fields. Ruff passed for 24 integration files. The
subsequent standalone container iteration completed ordinary generator CLI and
persistent-EOF public API generation. Final complete acceptance passed at `2b9c062`.

## Acceptance environment correction

F23: The first fixed full `scripts/test-docker.sh` run at `4842da7` stopped with
339 passing and 12 failing tests in 91.42s. Three failures required the real
har2sdk Node/npm dependency; nine real-browser cases requested the default Chrome
executable name. The command had run generator tests in the older compatibility
image, which intentionally had neither Node nor that executable name. No application
failure appeared in these tracebacks. Host full verification at `d27827c` passed
351 tests in 93.98s.

The correction moves full generator tests into the prepared generation image and
adds a test-only Chrome-name wrapper for its installed disposable Chromium. The
HTTPS compatibility program keeps its separate base image. The minimal installed
runtime must still contain neither a browser executable nor separate Node/npm.
All 12 focused rechecks passed in 33.87s. The corrected full command at `2b9c062`
then passed all 351 tests, HTTPS compatibility and standalone acceptance with clean,
unchanged source. Independent adversarial review accepted the correction; no checks
were skipped and the runtime image is unchanged by this test-environment fix.

## Quality review

The user's explicit quality instruction applies to all owned integration code.
The accepted refactor uses validated integration records such as OperationBinding,
WorkflowPlan, ExecutionSummary, RunRequest, RuntimeConfig and FileDescriptor.
Native API documents remain standard mappings and their upstream libraries retain
request construction and serialization ownership.

Autonomous collection, merge/quarantine, generation, coverage and persistence now
have focused helpers. SDK/workflow profile lifecycle code is shared. Native model
adapters, privacy handlers, admission checks and CLI parsing/handlers are separated
by responsibility. Sensitive fields are excluded from repr. The independent review
accepted the initial scope at `32ad5ea`, corrected binding/package components at
`a2037ab`, and distribution support at `d27827c`. Container helpers were separately
formatted, checked and independently reviewed. Generated tests also use conventional
spacing and comments; no formatter is required at client runtime.

## Native dependency provenance

The only upstream choice adjustment is justified by the reproduced F15 wire defect.
It is a separately versioned native build, with no runtime source patching or custom
serializer in the dispatch adapter.

| Item | Value |
| --- | --- |
| Distribution | `arazzo-runner==0.9.6+websiteclient.1` |
| Published base | `arazzo-runner==0.9.6` |
| Source SHA-256 | `5491c9facc1a552ad278657315d527889a63353185f6c93456d8af01180f9102` |
| Companion wheel SHA-256 | `8a04b4df1593ea7ae549b1f526fe94ec070cb991c3501432bdacba19bd1bf78c` |
| Rebuild backend | `pdm-backend==2.5.0` |
| Resources | `src/website_client/native_dependencies/`: source URL/hash, patch, rebuild help, provenance, LICENSE and NOTICE. |

The independent reviewer built the generator from a fixed archive and inspected
actual METADATA: exact version dependency, no workstation file URL, companion
resource present. The rebuild reproduced the wheel hash. Clean sibling-wheel
installation and runtime-container verification passed in milestone 4 at `2b9c062`.

## Completed standalone acceptance

The checked-in command exercises ordinary installed generator CLI and public API
URL generation with only initial login/MFA. The API driver physically closes stdin
after login. Both finish without subsequent driver navigation or contract selection.
Saved regeneration makes no target requests. The browser closes before installation
and execution in a separate non-root runtime image.

Four native upload forms run through public Python and actual console commands.
The service independently verifies exact file bytes, filename/media metadata and
cookie/bearer authentication. Workflows exercise happy sequences, immediate required
output rejection, literal null/omitted/nested bodies and schema-disallowed null.
Rotation succeeds; expired authentication terminates with stdin closed, one attempt
and no write. Excluded query encoding cannot dispatch.

The runtime image contains no generator, code generators, browser executable/cache,
separate Node/npm, checkout or research PYTHONPATH. Private profiles are separately
mounted and writable for atomic renewal. Nine generated installed-package tests pass.
Privacy scans inspect uncompressed wheel members and final reports; 75 source/config
files compare identically apart from declared report/build/cache exclusions. Privacy
heuristics still cannot guarantee universal anonymization of arbitrary input.

Only local neutral services have received mutation probes. Existing reviewed
worktrees remain preserved. The user authorized this implementation and later
explicitly authorized branch pushes and the draft PR; no merge or real external
service write is authorized.
