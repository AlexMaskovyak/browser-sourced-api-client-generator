#!/bin/sh
# Run the full suite and HTTPS compatibility checks, then standalone distribution.
set -eu
script_dir=$(CDPATH= cd "$(dirname "$0")" && pwd)
cd "$script_dir/.."
if [ "${1:-}" = "--help" ]; then
    cat <<'EOF'
Usage: scripts/test-docker.sh
Requires Docker Engine, Python 3 on the host, and network access for image builds.
Builds local images, runs the complete tests and HTTPS browser compatibility checks,
then proves generated clients in a separate offline runtime image. Temporary fixture
and private-profile volumes are removed. The final line gives the JSON report path.
Any failed assertion returns a nonzero exit status.
EOF
    exit 0
fi
if [ "$#" -ne 0 ]; then
    echo 'Unexpected arguments. Run scripts/test-docker.sh --help.' >&2
    exit 2
fi
docker build --tag browser-sourced-api-client-generator-e2e --file docker/Dockerfile .
docker build --tag browser-sourced-api-client-generator-generation-e2e --file docker/Dockerfile.acceptance .
docker run --rm --init --network none --shm-size=1g --cap-drop=ALL --security-opt=no-new-privileges browser-sourced-api-client-generator-generation-e2e xvfb-run -a python -m pytest -q -p no:cacheprovider tests
docker run --rm --init --network none --shm-size=1g --cap-drop=ALL --security-opt=no-new-privileges browser-sourced-api-client-generator-e2e
WEBSITE_CLIENT_REQUIRE_FIXED_SOURCE=1 WEBSITE_CLIENT_COMPATIBILITY_PASSED=1 WEBSITE_CLIENT_ACCEPTANCE_COMMAND=scripts/test-docker.sh "$script_dir/test-standalone.sh"
