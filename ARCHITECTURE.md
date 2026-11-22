# Autonomous client integration architecture

The supplied specification selects the architecture. This map assigns its seams to
existing owners and describes the state and failure boundaries for implementation.

## Components and data flow

| Owner | Integration responsibility |
| --- | --- |
| `cli`, `pipeline` | One URL/input-to-output command; import/normalize/generate compatibility; all unambiguous services and machine-readable complete/partial/unsupported coverage. |
| `browser`, `exploration`, `self_discovery` | Non-persistent initial login; guarded deterministic existing traversal; advertised descriptions and observed traffic; explicit bounded origins. |
| `capture`, `discovery`, manifests | Safe structure and provenance only; privacy before persistence; no raw credential HAR intermediates. |
| har2sdk adapter | Python subprocess plus minimal Node generateFromHar call; structured OpenAPI/warnings/leak findings. Node generation-only; terminal affected-route dependency errors. |
| `normalization`, `definitions`, capability checks | Standard validation and backend admission; preserve sources and exclude affected unsupported operations; saved inputs never fetch references. |
| `generation`, existing templates/packaging | OpenAPI Python Client 0.29.1, Ariadne 0.19.0 multipart/custom builders, existing schema-less operation runtime; defaults, profile ID, tests/notices. |
| private profile runtime | Protected platform directory outside outputs; origin-associated cookies/known headers; full-job file lock; atomic owner-only persistence. |
| authenticated dispatch | Shared exact-byte APIRequestContext.fetch boundary; small HTTPX/prepared-requests adapters; upstream serializers remain authoritative. |
| `workflow_binding`, `workflow_generation` | Exact supplied/advertised source associations, conflict quarantine, standalone workflow project and companion dependency. Saved inputs never fetch missing sources. |
| `workflow_admission`, `workflow_schema`, `workflow_runtime` | Versioned native Arazzo public start_workflow/execute_next_step; narrow syntax and dialect/schema guards, no custom scheduler/expression evaluator. |
| `workflow_api`, `workflow_cli` | One installed Python function and console command; ordinary inputs, bounded file bytes, noninteractive results and distinct local output failures. |
| `workflow_inputs`, `project_metadata` | Input documentation from declared schemas/explicit references, installed offline contract tests and source/wheel notices. No inferred workflow connections. |
| `result_output` | Shared flushed output boundary for SDK/workflow commands; local failure preserves the completed remote outcome without retry. |
| `unsupported_output` | Root README and offline Python stub for unsuccessful runs. Validated counts and fixed reason strings describe the outcome without copying credentials, URLs or exception text. |
| `client_cli` | Command-scoped native files, default endpoint/profile; ordinary noninteractive invocation; native return values and bounded diagnostics. |

URL -> initial login -> guarded existing exploration -> safe manifest/definitions ->
automatic capability filtering -> upstream generation -> existing standalone
packaging -> installed Python/CLI execution via private profile and request driver.

The ordinary run claims a fresh output directory after option/profile validation.
Every completed run writes client assets: usable packages contain their own README
and Python code; unsupported or failed runs receive a root README and runnable local
stub alongside coverage. Cancellation retains exit 130; other failures retain their
existing exception/CLI behavior. The stub never sends requests or counts as a usable
service. Existing directories are rejected before writing. A process kill or an
unwritable filesystem can prevent output and cannot be reported as successful.

The discovery policy distinguishes page assets from API traffic using the browser's
resource type, not a URL suffix. Cross-origin GET/HEAD asset loads are allowed;
navigation, writes, API-origin declarations and credential ownership keep their
existing rules. Native browser routing still checks redirected asset requests.
Frontend source extraction remains limited to same-origin modules; permitting a CDN
script to render the page does not make its declarations verified API contracts.

A parsed GraphQL read initiated by the selected website's Fetch/XHR traffic can
admit its exact API origin, including a separate host without a published schema.
Admission is bounded by the existing origin limit. Mutation-only evidence, opaque
POSTs, assets and foreign-page traffic do not admit origins. Credentials come only
from requests observed at that API origin; website headers are never copied there.
Unavailable introspection leaves captured named operations eligible for schema-less
generation. Failed REST inference is recorded separately and cannot discard an
independently usable GraphQL service or published definition.
Supplied definitions/manifests enter offline normalization directly. HAR enters
har2sdk; captured schema-less GraphQL keeps its separate existing compiler route.

Workflow documents reference sources by their declared URL or exact local file
reference. The input registry records those facts in memory; saved report entries
retain only manifest document IDs and optional public endpoint overrides. A source
must be an admitted OpenAPI document. Conflicting reports or destructive loss of
essential schema information exclude the affected workflow. No component infers a
connection from filenames, field values, source proximity or request order.

## Systems of record and credentials

Website is authoritative for authentication and application data. Standard documents
and source hashes record executable contract provenance, not live verification.
Coverage records exclusions/conflicts without claiming API completeness. Private
profile is the local authentication record; generated config contains only endpoint,
origin and profile ID. Another machine requires its own login/profile.

Profile lock covers load, entire call/job, cookie renewal save and cleanup, including
error paths. Separate origins use separate contexts/profiles. Restrict observed
headers to known or declared authentication headers for configured origins. Reject
URL credentials, profile/origin mismatch, insecure profile permissions and unsafe
filesystem links. Persist atomically with owner-only modes. Never bundle profile
files, raw secrets, target response samples or sensitive structural identifiers.

## Mutation, failure and recovery

Discovery never invokes discovered writes/uploads. Initial operator-selected website
and deterministic read conventions are not proof an untrusted server has no effects.
Install guards before automatic actions, constrain background channels/navigation,
report unknown actions. Do not infer connections from proximity/order/matching values.

Runtime sends already-built requests to fixed configured origins, no redirects or
automatic retries; bound time/body/files at 8 MiB, dispose responses and owned streams.
Disable ambient proxy/netrc; TLS verification stays enabled. Authentication rejection
ends with authentication_required without prompting. Cookie renewal persists; custom
refresh/MFA is not inferred. Unknown effects after disconnect/output failure remain
unknown; no retry or rollback claim. Later explicit login is recovery.

Workflow admission implements only the supplied linear 1.0.0 profile and explicit
whole-value references. Standard pointer/schema libraries guard producing responses
before dependent dispatch. Required null outputs, empty pointer tokens, integer
booleans, unsupported encodings/path values and unknown file objects are rejected.
Use dialect-specific validators. Exclude non-exploded form query arrays in both
SDK/workflow paths, preserving reasons and independent usable operations. No remote
fetches offline; discovery resolves references only within explicit origin/bounds.

## Verification boundaries

Promote supplied local fixtures/probes into ordinary tests. Public Python and installed
CLI must both load default endpoint/profile without browser or agent at runtime.
Clean container acceptance must mount credentials separately and use built wheels,
not generator imports. Privacy heuristics cannot guarantee universal anonymization;
destructive filtering reports missing information and excludes essential ambiguity.

## Required native JSON-null correction

The selected Arazzo 0.9.6 executor conflates an explicit nullable JSON body with an
absent body. The implementation reproduced an empty request body and HTTP 422 from
a neutral service that requires the JSON bytes `null`. Nested JSON null succeeds.
The upstream choice changes only for this reproduced incompatibility, as permitted
by the specification.

The correction belongs in a separately versioned native dependency wheel,
`arazzo-runner==0.9.6+websiteclient.1`. The project records a minimal upstream patch,
source hash, build procedure, wheel hash and Apache modification notices. Arazzo
continues to build requests; the prepared-request adapter sends its exact bytes.
Runtime code neither patches installed modules nor installs dependencies.

The companion wheel must be a sibling install artifact, not only a resource inside
a wheel pip has not installed. Poetry can resolve the repository-local dependency,
but built METADATA must require the exact version and contain no workstation file
URL. The documented pip command supplies both product and companion wheels. The
generator also carries the companion as data for packaging later workflow clients.
SDK-only clients retain their existing runtime dependencies. Clean acceptance must
verify the actual installed dependency and all affected native wire cases.
