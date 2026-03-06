set shell := ["bash", "-eu", "-o", "pipefail", "-c"]

TAG_BASE:="ghcr.io/drcdev-gh/janus"

run-dev:
    docker compose -f docker-compose-build.yaml up -d --build

run-tag tag:
    docker run {{TAG_BASE}}::{{tag}}
