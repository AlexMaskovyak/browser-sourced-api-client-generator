# Architecture

Poetry owns package metadata, dependency resolution, the committed `poetry.lock`,
development dependency groups and builds. The project environment lives in
`.venv/` and is ignored by Git. Docker installs the locked dependencies into a
separate application environment and tests the wheel built by Poetry. Poetry's
own bootstrap environment is separate from application dependencies.

Python owns the command, browser lifetime, discovery and generation. Chrome or
Chromium is a separate native browser process. Python communicates with its
loopback Chrome DevTools Protocol connection using the Python websocket-client
library. No Node, Playwright or project JavaScript is needed. Websites still run
their own scripts normally in the browser.

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
