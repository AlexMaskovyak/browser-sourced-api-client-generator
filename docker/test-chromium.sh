#!/bin/sh
# Test-only sandbox exception for the disposable, offline, non-root container.
# Production browser launch never adds this flag.
# Expose a loopback connection only in tests so a second Playwright instance can
# act as the human in the command's separately owned browser.
exec /usr/bin/chromium --no-sandbox --remote-debugging-address=127.0.0.1 --remote-debugging-port=0 "$@"
