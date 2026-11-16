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
