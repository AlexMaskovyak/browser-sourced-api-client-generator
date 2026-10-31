# Browser-Sourced API Client Generator

Browser-Sourced API Client Generator creates standalone Python clients from
website endpoints. Browser login is the first implemented step. Endpoint discovery
and client generation are not implemented yet.

## Install and open a website

You need Python 3.11 or newer, Poetry 2.2 or newer, and installed Google Chrome or
Chromium. If Poetry is missing, install it using the [official instructions](https://python-poetry.org/docs/#installation).
From this repository, install the locked dependencies and Python command:

```sh
poetry sync --with dev
poetry run website-client --help
```

Open Gusto, for example:

```sh
poetry run website-client login --url https://app.gusto.com
```

1. A separate temporary Chrome window opens.
2. Sign in normally, including any verification code. Enter credentials only into
   the website, never the terminal.
3. Return to the requested website if sign-in redirected to another service.
4. Press Enter in the terminal. The command checks the current page, closes the
   temporary browser and reports user-confirmed readiness.

This command does not save a login, generate a client, or verify an authenticated
API request. It does not submit expenses or collect account records. Website
scripts run normally while you log in. A subsequent invocation starts logged out.
Ctrl+C cancels and closes the browser. Closing the browser ends the command with
an error. The command requires an interactive terminal, not piped input.

Chromium users select `--browser chromium`. If your installation is in an unusual
location, supply `--browser-path /absolute/path/to/browser`. Failure to find a
browser requires installing Chrome or Chromium, or correcting that path. For an
incomplete login, sign in fully and return to the requested website before
confirming on a fresh run.

The implementation connects to the native browser directly from Python. It needs
no Node, npm, Playwright or JavaScript driver. Cookies and authentication stay in
the browser or process memory. No recordings or saved browser state are exported.
Temporary profile files are removed on normal close; forced termination can leave
temporary browser files.

## Tests

```sh
poetry check
poetry run pytest -q
poetry build
sh scripts/test-docker.sh
```

Docker is needed only for the last command. It builds and installs the Python
wheel using Poetry and tests a real terminal and headed Chromium against a local HTTPS login
and verification-code fixture. The container has no host mounts or external
network during execution. It has no Node installation or production credentials.
These tests do not establish production service compatibility.

Poetry creates an ignored `.venv/` inside this repository. `poetry sync` reads
the committed `poetry.lock`; it installs the locked dependencies and the command,
and removes packages that are no longer in the selected dependency groups, without
opening a browser. `poetry run` uses that environment without requiring activation.
`poetry build` creates the wheel and source archive in `dist/`. If Poetry reports
that the lock is stale after an intentional dependency edit, run `poetry lock`
and review both `pyproject.toml` and `poetry.lock` before committing. To change
dependencies, use `poetry add PACKAGE` or `poetry add --group dev PACKAGE`.
