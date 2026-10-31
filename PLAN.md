# Delivery plan

1. Browser login: install the Python command, open a temporary Chrome or Chromium
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
is current; the others are not implemented. Full relevant tests must pass before
push or handoff. The existing browser-session-rest-client repository is independent
and is not changed by this work.
