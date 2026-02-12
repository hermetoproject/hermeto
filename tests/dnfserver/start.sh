#!/bin/bash
set -o errexit -o nounset -o pipefail

DEFAULT_IMAGE=mirror.gcr.io/library/nginx:alpine-slim
DEFAULT_SSL_PORT=8443
DEFAULT_HTTP_PORT=8081
TEST_RPM="https://cdn-ubi.redhat.com/content/public/ubi/dist/ubi9/9/x86_64/baseos/os/Packages/r/redhat-release-9.4-0.5.el9.x86_64.rpm"

HTTP_PORT=${DNFSERVER_HTTP_PORT:-$DEFAULT_HTTP_PORT}
SSL_PORT=${HERMETO_TEST_DNFSERVER_SSL_PORT:-$DEFAULT_SSL_PORT}
DIR=$(dirname "$(realpath "${BASH_SOURCE[0]}")")

cleanup() {
    rm -r "$WORKDIR" || true
}

WORKDIR=$(mktemp -d --tmpdir "cachi2-dnfserver.XXXXXX")
trap cleanup EXIT

setup() {
    echo "--- Creating DNF repository ---"
    mkdir -p "$WORKDIR/dnfrepo/pkg"
    curl --output-dir "${WORKDIR}/dnfrepo/pkg" -O ${TEST_RPM}

    createrepo_c "${WORKDIR}/dnfrepo/pkg"
    ls -lAR "${WORKDIR}/dnfrepo"
    echo "--- DNF RPM REPO SERVER ---" > "${WORKDIR}/dnfrepo/index.html"
    echo -e "\n---"
    echo "Starting DNF server at:"
    echo -e "\thttp://127.0.0.1:${HTTP_PORT}"
    echo -e "\thttps://127.0.0.1:${SSL_PORT}"
    echo -e "---\n"
}

setup >&2

podman run --rm --replace --name cachi2-dnfserver \
    -p "${HTTP_PORT}":81 \
    -p "${SSL_PORT}":443 \
    -v "${DIR}/nginx.conf":/etc/nginx/nginx.conf:ro,Z \
    -v "${DIR}/certificates/":/etc/nginx/ssl:ro,Z \
    -v "${WORKDIR}/dnfrepo":/dnfrepo:ro,Z \
    "${DNFSERVER_IMAGE:-$DEFAULT_IMAGE}"
