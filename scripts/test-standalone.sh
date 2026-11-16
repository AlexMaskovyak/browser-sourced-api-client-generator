#!/usr/bin/env bash
# Build images with registry access, then run all assertions without external networking.
set -euo pipefail
cd "$(dirname "$0")/.."
run_id="website-client-acceptance-$$"
work=$(mktemp -d "${TMPDIR:-/tmp}/website-client-acceptance.XXXXXX")
python3 docker/acceptance_summary.py --source "$work/source-state.json"
fixture="$run_id-fixture"
profile="$run_id-profile"
artifacts="$run_id-artifacts"
cleanup() {
    docker rm -f "$fixture" >/dev/null 2>&1 || true
    docker volume rm "$profile" "$artifacts" >/dev/null 2>&1 || true
}
trap cleanup EXIT INT TERM
docker build -t browser-sourced-api-client-generator-e2e -f docker/Dockerfile .
docker build -t "$run_id-generation" -f docker/Dockerfile.acceptance .
docker volume create "$profile" >/dev/null
docker volume create "$artifacts" >/dev/null
docker run --rm --network none --user root -v "$profile:/profiles" -v "$artifacts:/artifacts" "$run_id-generation" sh -c 'chown 1000:1000 /profiles /artifacts && chmod 700 /profiles /artifacts'
docker run -d --name "$fixture" --network none "$run_id-generation" python /work/docker/acceptance_fixture.py >/dev/null
sleep 2
docker run --rm --init --network "container:$fixture" --shm-size=1g --cap-drop=ALL --security-opt=no-new-privileges -e WEBSITE_CLIENT_PROFILE_DIR=/profiles -v "$profile:/profiles" -v "$artifacts:/artifacts" "$run_id-generation"
mkdir -p "$work/runtime/driver"
docker cp "$fixture:/work/docker/acceptance_runtime.py" "$work/runtime/driver/"
docker run --rm --network none -v "$artifacts:/artifacts:ro" "$run_id-generation" tar -C /artifacts -cf - . | tar -C "$work" -xf -
cp -R "$work/wheels" "$work/runtime/wheels"
cp "$work/catalog.json" "$work/payload.bin" "$work/runtime/driver/"
cp docker/Dockerfile.runtime "$work/runtime/Dockerfile"
docker build -t "$run_id-runtime" "$work/runtime"
docker run --rm --init --network "container:$fixture" --cap-drop=ALL --security-opt=no-new-privileges -e WEBSITE_CLIENT_PROFILE_DIR=/profiles -v "$profile:/profiles" -v "$artifacts:/artifacts" "$run_id-runtime"
docker run --rm --network none -v "$artifacts:/artifacts:ro" "$run_id-generation" tar -C /artifacts -cf - . | tar -C "$work" -xf -
python3 docker/acceptance_summary.py "$work" "$run_id-generation" "$run_id-runtime"
