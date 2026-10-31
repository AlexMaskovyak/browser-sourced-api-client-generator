# Browser-Sourced API Client Generator

Browser-Sourced API Client Generator creates standalone Python clients from
website endpoints. Browser login is the first implemented step. Endpoint discovery
and client generation are not implemented yet.

## Install and open a website

You need Python 3.11 or newer and installed Google Chrome or Chromium. From this
repository, install the Python command:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/website-client --help
```

Open Gusto, for example:

```sh
.venv/bin/website-client login --url https://app.gusto.com
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
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m pytest -q
sh scripts/test-docker.sh
```

Docker is needed only for the last command. It builds and installs the Python
wheel and tests a real terminal and headed Chromium against a local HTTPS login
and verification-code fixture. The container has no host mounts or external
network during execution. It has no Node installation or production credentials.
These tests do not establish production service compatibility.
