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
