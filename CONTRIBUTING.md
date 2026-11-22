# Contributing

Start with the component you are changing, run its tests, then run the complete
suite before submitting a pull request. Tests are part of the public project:
another developer should be able to understand a failure without this chat or its
author's help.

## Setup and test groups

From the repository root, install Python 3.11 or newer and Poetry 2.2 or newer,
then run `poetry sync --with dev`. HAR conversion tests also need Node 20 or newer
and npm. Browser tests need Chrome or Chromium. These commands install dependencies;
they do not contact a target website or use your personal browser profile.

Run the groups in pipeline order when diagnosing generation:

| Component | Command | What it verifies |
| --- | --- | --- |
| Browser capture | `poetry run pytest -q tests/browser` | Browser lifecycle, HTTP sessions and captured network events. |
| Discovery | `poetry run pytest -q tests/discovery` | Traffic extraction, schema discovery, read-only navigation and origin rules. |
| Contract validation | `poetry run pytest -q tests/contracts` | Standard definitions, manifests, operation variables and workflow source associations. |
| Generation | `poetry run pytest -q tests/generation` | Public generation commands, client projects, installed wheels and saved-input recovery. |
| Runtime execution | `poetry run pytest -q tests/runtime` | Authentication, private profiles, exact request bytes, workflow failures and cancellation. |

Each group runs independently. `tests/support` holds reusable fixture setup;
`tests/helpers` holds programs launched in child processes. Neither contains
collected tests. `tests/fixtures` contains public, synthetic or sanitized input data.

A failure in capture or validation should be resolved before interpreting downstream
generation results. Tests may use neutral local HTTP servers. They must not require
real credentials or contact production services. The first HAR test may download the
locked converter through npm if its cache is empty.

Before submitting, run:

```sh
poetry run ruff check
poetry run ruff format --check
poetry run pytest -q
scripts/test-docker.sh
```

The container command builds local images, runs the full suite and HTTPS checks,
then installs generated clients in a separate runtime image with external networking
disabled. It prints the location of its report. Docker and build-time registry access
are required. Keep execution logs and machine-specific reports outside Git; summarize
the tested commit, commands, results and remaining limitations in the pull request.
If a command fails, fix the reported check and rerun it before advancing.

## Code and test readability

Use descriptive module and test names. Begin each test file with a short description
of its purpose and boundary. Keep setup, the action under test and expected results
visually separate. Prefer named expected values and individual assertions to dense
expressions; add a failure message when a value alone would not explain the failure.
Give parameterized scenarios meaningful names.

Keep functions focused. Extract repeated setup into helpers with explicit inputs and
outputs. Do not import fixtures or helpers from another test file. Store child-process
programs in ordinary source files, not executable strings. Keep substantial browser
scripts in JavaScript fixtures that can be read and checked directly.

Use dataclasses or the existing validation libraries for structured integration
inputs. A dataclass annotation alone is not runtime validation. Validate at component
boundaries, preserve upstream ownership of standard formats, and avoid creating a
parallel model for an existing library's types. Comments should explain constraints
or surprising behavior, rather than narrate the next line.

Regression tests must fail for the actual defect. Use the real parser, converter or
serializer at the relevant boundary. A mock is appropriate for a missing dependency
or transport fault, but does not prove that a real captured input converts correctly.
Preserve independent operations when one service fails, and distinguish a generated
client from successful authenticated execution.

## Patched workflow dependency

Poetry already installs the bundled `arazzo-runner` wheel through the dependency
entry in `pyproject.toml` and `poetry.lock`. Do not install it separately when working
from this checkout.

The pinned upstream version sends an empty body for explicit JSON `null`. The local
version `0.9.6+websiteclient.1` contains only the documented fix for that behavior.
Removing it would break nullable-body execution. The patch, source and wheel hashes,
license and rebuild program live in `src/website_client/native_dependencies`.
The runtime tests verify explicit null, omitted bodies and nested null separately.

Distributable generator and workflow wheels require this exact version. Because it
is not published in a package index, their installation instructions supply the
companion wheel to pip. Runtime code never downloads or patches a dependency. Keep
this exception until a tested upstream release provides the same behavior; then
remove the local patch and use the ordinary locked dependency.
