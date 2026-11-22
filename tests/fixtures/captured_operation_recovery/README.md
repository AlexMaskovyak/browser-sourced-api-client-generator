# Saved example generation regression

This fixture reproduces failed REST inference alongside usable captured GraphQL
operations. Run it with Node 20 or newer, npm, and the project's development
dependencies installed:

```sh
poetry run pytest -q tests/test_captured_operation_recovery.py
```

The tests generate real Python clients from the saved input. They do not contact
example, use credentials, or replace the pinned HAR converter with a mock.
`scripts/test-docker.sh` also runs these checks with external networking disabled.

## What was preserved

The source is the saved `example-capture` run. Its HTTP archive (HAR) recorded
GraphQL requests to a separate host with no response body and no available schema.
The fixture retains the syntax of its 11 unique operations, including fragments,
variables and one mutation. Generation may describe that mutation; these tests
never execute it.

One REST response is enough to reproduce the independent conversion failure. Its
URL contains the encoded placeholder `%7Bid_3%7D`. The converter emits a path with
`{id_3}` but no matching path parameter declaration. The normal definition validator
rejects it. This must produce a `har_inference_failed` exclusion while the captured
GraphQL service still produces a README and runnable client.

The historical run also reported `har_dependency_required`. That records a missing
converter prerequisite, not the same failure. A separate test simulates that missing
prerequisite and requires the GraphQL client to survive it too.

## What was removed or replaced

Hosts use reserved `example.test` names. GraphQL names are consistently replaced
with `CapturedQueryNN`, `CapturedMutationNN` and `fieldN`; scalar literals are neutral
examples. The REST response contains one synthetic state. Headers, cookies, actual
variables, account identifiers, timestamps, tracking traffic and repeated requests
are removed. No private profile or copied coverage report is included.

`expected_operations.json` lists the complete expected generated inventory. The
separate-host admission check replays these sanitized request bodies through the
normal observer. The browser test in `test_cdn_discovery.py` covers page loading,
unavailable introspection, credential isolation and a successful generated read
against local servers. This saved fixture does not establish live authentication
or explain the original generic probe failures.
