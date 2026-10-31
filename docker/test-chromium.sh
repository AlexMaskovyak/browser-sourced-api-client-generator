#!/bin/sh
# Test-only sandbox exception for the disposable, offline, non-root container.
# Production browser launch never adds this flag.
exec /usr/bin/chromium --no-sandbox "$@"
