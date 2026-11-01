# Architecture

Poetry owns package metadata, dependency resolution, the committed `poetry.lock`,
development dependency groups and builds. The project environment lives in
`.venv/` and is ignored by Git. Docker installs the locked dependencies into a
separate application environment and tests the wheel built by Poetry. Poetry's
own bootstrap environment is separate from application dependencies.

Python owns the command, discovery and generation. Playwright's Python API owns
Chrome or Chromium startup, tabs, event delivery and shutdown. Its bundled Node
driver is a runtime dependency; project code remains Python. Browser-native
network capture and request interception use Playwright CDP sessions, its interface
to Chrome DevTools Protocol. There is no project WebSocket implementation.
Websites still run their own scripts normally in the browser.

The human owns sign-in and verification codes. The website owns account records
and authentication. A temporary browser profile holds the live session. The
browser-session context manager retains that session in memory for later discovery
and generation; the initial login command closes it after terminal confirmation.
User-confirmed readiness and basic page checks do not prove an authenticated API
request. No credentials, headers, recordings or storage state are exported.

Discovery will own endpoint evidence and coverage. Generation will own standalone
Python source and operation help. Service repositories own domain validation and
multi-request workflows. Private orchestration owns real files and ledgers.

Login navigates the website but never replays discovered business operations.
Production writes require explicit authorization. Generated clients must remain
independent of the generator.

Failures expose bounded messages. Success, cancellation, termination and browser
closure close owned connections and processes and remove the temporary profile.
A fresh command requires a fresh login. Forced process termination can leave
temporary browser files. Existing browser profiles and repositories are untouched.

Unit tests cover parsing and failure handling. Docker tests install the Python
wheel, use a real terminal and headed Chromium, and complete a local HTTPS login
with a verification code. They contain no production data and prove neither
production compatibility nor endpoint completeness.

## Discovery sources and definitions

Optional algorithmic exploration uses deterministic page and control traversal.
Python owns the queue, action classification and network policy. Playwright owns
DOM inspection and interactions. The site remains the source of observed API
traffic. Form actions/fields and frontend GraphQL documents are declarations,
not proof that a write was executed or that its backend contract is complete.

Exploration starts after human login in a fresh tab with service workers blocked.
The login tab closes so its existing WebSockets cannot bypass exploration rules.
Requests are intercepted before transmission: navigation stays on the website,
GET/HEAD and parsed GraphQL queries are permitted, and unknown writes/mutations
are blocked and reported. WebSockets are closed. Only navigation controls are
clicked; forms and submit controls are inventoried rather than executed. Page
content is untrusted data and is never executed as Python or model instructions.

Exploration records bounded, sanitized page/action coverage, declared forms and
blocked requests. Loaded same-origin scripts are read as text to extract GraphQL
documents without executing additional source. Limits and parse omissions remain
explicit. Before schema probes the exploration page is navigated to a blank page
and its HTTP interception is removed. Failures restore interception and close the
temporary context through the existing session owner; reruns use a new output.

Automatic discovery adds bounded schema reads after human login. The website is
the source of complete definitions; captured shapes remain inferred evidence.
Python owns candidate selection, credential scoping and probe reporting. A page
reload observes the application's own traffic. Only same-origin schema links and
common definition paths are fetched automatically. GraphQL introspection uses
observed endpoints or the explicitly selected endpoint, never captured business
operations. Redirects are blocked. Failure to obtain a schema remains unknown
coverage, not evidence that the API has no other operations. Retry starts a fresh
command and output directory; the existing output is never replaced.

Browser capture starts after login confirmation. Python receives native network
events from the owned tab, including requests initiated by that page to API origins.
An existing HAR is a second, offline source feeding the same analysis. Neither
source replays captured requests. Request headers, cookies and raw payload values
are removed before durable output. Captured GraphQL documents exclude literal
values and comments; variables are not exported. Bounds and omissions are reported.

The outputs distinguish a sanitized HAR, endpoint evidence, GraphQL operation
documents, inferred OpenAPI, and actual downloaded schemas. Inferred OpenAPI
describes only observed routes, methods and sample shapes. It is not a complete
service specification. GraphQL operation documents are not a full GraphQL schema.
Actual introspection data is validated and converted to schema definition language.
Actual OpenAPI documents are validated separately. External references require
bundling before generation rather than implicit network resolution.

Standalone Python source generation delegates to openapi-python-client for OpenAPI
and Ariadne Codegen for GraphQL schemas plus operation documents. Service workflows
remain outside these generated layers. Inputs and output directories are bounded
and validated before generation; no credentials are embedded in generated clients.

## Generated client authentication

Generated packages include their own Python browser support and Playwright
dependency. An operation's --login option opens a fresh browser, waits for manual
sign-in and verification codes, and then calls the upstream typed method. A custom
login URL handles services whose application and API use different origins.

An HTTPX transport sends that method's exact request through a separate tab
in the same native browser. Playwright creates and closes the tab. Request interception supplies its method and
body. Response interception reads the bytes and stops navigation before response
scripts run. Redirects are returned without following them. Only the chosen API
origin and operation method are permitted, and each transport sends one request.

Browser cookies stay in the browser. Observed authorization and CSRF headers are
kept transiently for the exact API origin. Additional session-header names can be
explicitly selected; their values are read from that session, not copied by the
user. No authentication state enters generated source, HAR files or command output.
Failure and cancellation close owned targets, browser processes and profiles.

The adapter for CDP sessions retains only the network events needed by capture
and request policy. It does not implement WebSocket framing or response matching.
Completed response bodies are read during their completion callback so a later
navigation cannot discard them before the next terminal poll. Production uses
Playwright's private control pipe. Only the container test wrapper exposes a
loopback debugging port for a second Playwright instance to act as the human.
