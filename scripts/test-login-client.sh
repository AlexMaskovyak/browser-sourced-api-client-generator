#!/bin/sh
# Exercise the login milestone with the installed wheel and a local HTTPS fixture.
set -eu
cd "$(dirname "$0")/.."
image=website-client-login-milestone
docker build --tag "$image" --file docker/Dockerfile .
docker run --rm --init --network none --shm-size=1g --cap-drop=ALL \
    --security-opt=no-new-privileges "$image" xvfb-run -a python -m pytest \
    -q -p no:cacheprovider tests/integration/test_browser_login_and_pages.py \
    tests/generation/test_login_client_output.py tests/discovery/test_page_link.py
