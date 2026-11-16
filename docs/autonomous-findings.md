# Integration findings

Source-inspection design findings are distinct from demonstrated execution failures.
Each material finding records impact, reproduction/basis, disposition and evidence.

| ID | Location and basis | Impact | Disposition | Verification |
| --- | --- | --- | --- | --- |
| D1 | self_discovery observer, source inspection | Headers can cross origin on capture. | Explicit selected origins before persistence. | Pending M1. |
| D2 | browser/session_http, source inspection | Persistent browser and one-call transport cannot run unattended jobs. | Non-persistent login and locked profile APIRequestContext. | Pending M1. |
| D3 | imports/definitions, supplied canaries and source | Sensitive structural values escape scalar-only filtering. | Privacy checks before every persistence route; exclude essential sensitive structures. | Pending M2. |
| D4 | normalization/pipeline, source inspection | Manual review, single service and operations-required blockers. | Automatic admission, all ready services and Ariadne builders. | Pending M2. |
| D5 | definitions, source inspection | Whole-document remote-ref failure loses usable methods. | Bounded same-origin discovery resolution, offline per-operation exclusion. | Pending M2. |

Design review and implementation evidence will be appended at fixed revisions.

## Design review of 2067028

- D6, `client_cli.enter_generated_client`: current injection is CLI-only. A saved
  profile/default endpoint must also be available through a documented generated
  Python context/factory, with native upstream return values. This is an integration
  requirement, not evidence of a completed runtime. Add installed public-API tests.
- D7, `generate_openapi_project`: upstream `--fail-on-warning` may discard usable
  methods along with one unsupported operation. Automatic admission must exclude
  unsupported operations, preserve diagnostics and generate remaining usable methods;
  do not suppress warnings and claim complete coverage. Test mixed supported and
  unsupported definitions against installed output.

Coordinator disposition: D1-D7 are required milestone criteria, not optional risks.
Public Python profile support is M1 and automatic mixed-operation admission is M2.
The architecture remains the supplied upstream architecture.

## Baseline verification

- Worktree base: `fb218f4`, clean main and preserved feature worktree at `682d28c`.
- Installed worktree-only Poetry 2.5.1 under ignored `work/poetry-env`; selected
  Python 3.12.14. Neither existing environment was changed.
- `work/poetry-env/bin/poetry install --with dev`: succeeded from existing lock.
- `work/poetry-env/bin/poetry run pytest -q`: **205 passed in 10.89 seconds**.
- No project-configured lint or type-check command exists at baseline. Ruff is an
  upstream dependency, which does not establish a project lint policy.
- Docker engine 27.4.0 is available. Existing cached images are not clean acceptance
  evidence for this branch.

## Independent design review closure

Adversary reviewed `2067028` and the coordinator recorded D6/D7 in `032072a`.
No additional material design blockers were reported. Planning/adversarial design
review is complete before application changes. D1-D7 remain implementation and test
obligations. Required probes include two-process profile rotation, no replay on
request/profile-save failure, public Python default-profile use and mixed-operation
partial coverage. No product acceptance has yet been claimed.

## Milestone 1 review at 35dc531

Implementation commits: `ba5d94a` (profiles/dispatch) and `35dc531` (native files and
standalone Python/CLI integration). Author focused checks: profile/browser/session
43 passed in 0.46s; native/generated checks 27 passed in 18.20s. Full
`work/poetry-env/bin/poetry run pytest -q`: 221 passed in 21.39s.
Independent reviewer reran the six focused files: 70 passed in 17.85s.

| ID | Location | Demonstrated impact and reproduction | Disposition | Verification |
| --- | --- | --- | --- | --- |
| F1 | `authenticated_http.AuthenticatedDispatch.send` | Independent local disconnect-after-commit probe exposed cookie and bearer canaries in raw Playwright exception string and formatted traceback. Journal still showed one request/effect, no replay. | Sanitize driver failures at shared dispatch boundary, raise bounded unknown-effect error without exception chaining. | Pending correction and canary probe. |
| F2 | `client_cli.show_failure` | `ProfileError('authentication_required')` before call enters ValueError branch and emits generic input error rather than reason JSON. Reviewer direct reproduction. | Handle bounded profile errors before generic input validation. | Pending missing-profile CLI regression. |
| F3 | `native_files.convert` | Ordinary generated JSON model named File is mistaken for native upload; convert of its valid name field fails. Same class-name hazard applies to Upload. Reviewer generated OpenAPI reproduction. | Match actual upstream file type identity, retain native JSON model handling. | Pending both collision regressions plus real uploads. |

Independent adversary verified full two-request job serialization across processes,
profile/origin rejection, single committed effect after disconnect, and no replay on
profile-save failure. A generated wheel installed into an isolated target executed
public `client_context()` twice through fresh closed-stdin `python -I` processes,
renewing cookies and importing no generator module. This shared dependency environment
is useful installed-wheel evidence, not milestone 4 clean-container acceptance.

Scratch probe results remain under the chat workspace's `work/adversary-m1`:
`results-35dc531.json` and `public-results-35dc531.json`. Required installed console
commands, autonomous URL wiring, workflow guards and clean-container acceptance
remain incomplete. Milestone 1 is not closed while F1-F3 remain unresolved.

## Milestone 1 correction closure at 7d90c49

F1-F3 resolved. Author focused five-file check: 52 passed in 20.34s; independent
reviewer rerun: 52 passed in 20.31s. Full suite: 227 passed in 24.11s.
`git diff --check` passed. Reviewer also independently injected credential-bearing
fetch errors and verified safe exception/traceback, one call and no replay, and
verified exact pre-call authentication reason JSON. All five independent adversary
cases passed against the corrected revision; credential canaries were absent from
disconnect exceptions/tracebacks while the server retained exactly one effect.

M1 complete within its runtime integration scope. M4 installed console/container
acceptance remains required. D1 automatic discovery capture, D3-D5 and D7 admission
remain M2 obligations. D2 private runtime and D6 public Python factory are implemented
and independently reviewed; complete autonomous URL integration is still pending.

## Milestone 2 intermediate review at 1ba7924

First slice implements HAR/admission seams and selectively applies `0775d8c` with
its regressions. Author and independent reviewer each ran
`poetry run pytest tests/test_admission.py tests/test_generation.py tests/test_self_discovery.py tests/test_discovery.py -q`:
55 passed in 5.65s. Reviewer used an isolated fixed-revision archive. This is not
acceptance of the still-incomplete autonomous orchestration or all privacy surfaces.

| ID | Location | Demonstrated impact and reproduction | Disposition | Verification |
| --- | --- | --- | --- | --- |
| F4 | `admission.prune_components` | A response references `#/components/schemas/Outer/properties/safe`, but unrelated `Outer.unrelated` has a remote ref. Retaining the whole component makes final validation reject the otherwise usable method. | Preserve only required reference dependencies, without silently changing executable wire semantics. | Pending correction. |
| F5 | `admission` security handling | Unused sensitive security-scheme name/header excludes an independent unauthenticated read. | Check actual effective security requirements per operation; remove unused sensitive structures from saved/executable definitions. | Pending correction. |
| F6 | `admission.admit_openapi` | A malformed `post: null` raises AttributeError and prevents usable independent reads from generating. | Bounded invalid-contract exclusion for malformed operation entries. | Pending correction. |

All findings reproduced locally without target fetches. Implementer notified; final
M2 schema-only, orchestration, privacy and route acceptance remain pending.

## Milestone 2 schema-only slice review at 7e5ac73

Independent reviewer ran admission/schema-only/pipeline/generation checks in an
isolated archive: 59 passed in 12.74s. F4-F6 corrections passed their reproductions.
Native wire capture also verified missing required arguments reject and omitted
optional arguments stay absent, preserving the server default. A custom GraphQL root
type correctly uses Ariadne's generated Query builder.

| ID | Location | Demonstrated impact and reproduction | Disposition | Verification |
| --- | --- | --- | --- | --- |
| F7 | `graphql_admission.schema_operations` | Roots or leaves named `class`/`from` become native `class_`/`from_`; snake-case-only metadata breaks eager CLI loading, including other commands and help. | Use upstream naming behavior and reject ambiguous generated symbols automatically. | Pending correction and actual generated command checks. |
| F8 | `graphql_admission.safe_schema` | Removing sensitive interface `CANARY_PRIVATE` leaves `Record implements CANARY_PRIVATE`, invalidating the schema and losing independent `Query.status`. | Close privacy exclusions across interface and inheritance references, preserving independent roots. | Pending correction. |

SDL/introspection happy cases and abstract/leafless exclusions passed the author's
slice checks. Final autonomous discovery, complete route and persistence checks are
still pending; no complete M2 delivery is claimed by these intermediate reviews.
