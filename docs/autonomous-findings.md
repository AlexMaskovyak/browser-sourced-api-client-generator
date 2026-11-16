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
