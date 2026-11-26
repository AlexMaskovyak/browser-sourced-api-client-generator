#!/bin/sh
# Run the public client contract against a disposable site with no external network.
set -eu
cd "$(dirname "$0")/.."

case "$#:${1-}" in
    0:|1:--require-message-get) ;;
    *) echo "Usage: $0 [--require-message-get]" >&2; exit 2 ;;
esac

image=website-client-message-site
docker build --tag "$image" --file docker/Dockerfile .
docker run --rm --init --network none --shm-size=1g --cap-drop=ALL \
    --security-opt=no-new-privileges "$image" xvfb-run -a python -m pytest \
    -v --tb=short -p no:cacheprovider tests/acceptance/message_site
