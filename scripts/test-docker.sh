#!/bin/sh
set -eu
docker build --tag browser-sourced-api-client-generator-e2e --file docker/Dockerfile .
docker run --rm --init --network none --shm-size=1g --cap-drop=ALL --security-opt=no-new-privileges browser-sourced-api-client-generator-e2e
