# Delivery plan

## Poetry packaging

- [x] Replace setuptools metadata and the requirements lock with Poetry metadata,
  a development dependency group, `poetry.lock` and an in-project environment.
- [x] Update installation, login, test and build instructions. Make Docker install
  from the Poetry lock and test the wheel built by Poetry.
- [x] Run Poetry validation, unit tests, wheel inspection and Docker login scenarios.
  Commit the change and open a separate PR against merged main.

Poetry owns dependency resolution and package builds. This change does not alter
the browser session or endpoint-generation milestones. Completion requires the
installed Poetry-built command to pass the existing real-browser tests.

Verified with Poetry 2.5.1 on October 31, 2026: strict metadata/lock validation,
26 Python unit tests, source and wheel builds, installed command help, and wheel
inspection passed. The Docker run passed all 26 tests and five browser-login
scenarios using the Poetry-built wheel. Runtime dependency remains websocket-client;
pytest belongs only to the development group. No browser implementation changed.

1. [x] Browser login: install the Python command, open a temporary Chrome or Chromium
   window, let the human sign in and finish verification codes, then accept terminal
   confirmation. Test login, unfinished login, browser closure, cancellation and
   termination in a clean Docker container. No Node or JavaScript implementation.
2. Discovery: collect bounded endpoint evidence through the same in-memory browser
   session. Separate GraphQL operations from ordinary HTTP endpoints. Never save
   credentials or infer a complete schema from examples.
3. Generation: emit standalone Python packages with named endpoint methods, CLI
   commands and operation help. Use a real GraphQL schema with Ariadne Codegen
   when available; otherwise preserve reviewed explicit operations. Use OpenAPI
   tooling only for actual compatible specifications. Treat har2sdk as a reference.
4. Service integration: generate Gusto's endpoint layer through this generator.
   Keep domain workflows in the service repository and real records in private
   orchestration. Verify authenticated reads before claiming live compatibility.

Each milestone requires a focused commit and relevant tests. The first milestone
is implemented; the others are not implemented. Full relevant tests must pass before
push or handoff. The existing browser-session-rest-client repository is independent
and is not changed by this work.

Verification on October 31, 2026: the installed Python wheel passed 26 unit tests
and five headed-Chromium scenarios in a non-root Linux/arm64 Docker container:
HTTPS login with a verification code, unfinished login, Ctrl+C, browser closure
and SIGTERM. The container has no Node or npm executable, host mounts, production
credentials or external network during execution. The test-only browser wrapper
disables the browser sandbox inside the disposable container; production launch
does not. No project JavaScript or JavaScript driver is used.
