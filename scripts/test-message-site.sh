#!/bin/sh
# Run the disposable message application with the installed generator and Chromium.
set -eu
cd "$(dirname "$0")/.."
case "$#:${1-}" in
    0:) require_message_get=0 ;;
    1:--require-message-get) require_message_get=1 ;;
    *) echo "Usage: $0 [--require-message-get]" >&2; exit 2 ;;
esac
image=website-client-message-site
docker build --tag "$image" --file docker/Dockerfile .
docker run --rm --init --network none --shm-size=1g --cap-drop=ALL \
    --security-opt=no-new-privileges -e REQUIRE_MESSAGE_GET="$require_message_get" "$image" xvfb-run -a python -m pytest \
    -q -s -p no:cacheprovider tests/browser/test_message_site.py
