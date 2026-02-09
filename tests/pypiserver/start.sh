#!/bin/bash
set -o errexit -o nounset -o pipefail

DIR=$(dirname "$(realpath "${BASH_SOURCE[0]}")")

DEFAULT_IMAGE=mirror.gcr.io/pypiserver/pypiserver:v2.1.1@sha256:17198f668ef4f460ee81456f09fc65d352766fa9e49a81474304c5aa69b8be38
DEFAULT_PORT=8080

cleanup() {
    rm -r "$WORKDIR" || true
    podman volume rm -f --time 0 cachi2-pypiserver-packages >/dev/null || true
    podman volume rm -f --time 0 cachi2-pypiserver-auth >/dev/null || true
}

WORKDIR=$(mktemp -d --tmpdir "cachi2-pypiserver.XXXXXX")
trap cleanup EXIT

setup() {
    echo -e "\n--- Downloading $DIR/package-urls.txt ---\n"

    mkdir "$WORKDIR/packages"
    sed '/^#/d' "$DIR/package-urls.txt" |
        xargs curl --fail --remote-name-all --output-dir "$WORKDIR/packages"

    echo -e "\n--- Setting cachi2-user:cachi2-pass authentication ---\n"

    mkdir "$WORKDIR/auth"
    # Content based on htpasswd -b -c .htpasswd cachi2-user cachi2-pass; cat .htpasswd
    # shellcheck disable=SC2016
    echo 'cachi2-user:$apr1$ChHgbvcg$l1QSrRehMhD0XOjj9ruem/' > "$WORKDIR/auth/.htpasswd"

    echo -e "\n--- Creating podman volumes ---\n"

    # Note: it's not strictly necessary to create these volumes, we could mount the content
    # straight from the $WORKDIR. But pypiserver chowns the packages, making it impossible
    # for this script to fully clean up after itself. Using podman volumes avoids that.

    echo "Importing content of $WORKDIR/packages: cachi2-pypiserver-packages"
    podman volume create --ignore cachi2-pypiserver-packages >/dev/null
    ls -lA "$WORKDIR/packages"
    tar cf - -C "$WORKDIR/packages" . | podman volume import cachi2-pypiserver-packages -

    echo "Importing content of $WORKDIR/auth: cachi2-pypiserver-auth"
    podman volume create --ignore cachi2-pypiserver-auth >/dev/null
    ls -lA "$WORKDIR/auth"
    tar cf - -C "$WORKDIR/auth" . | podman volume import cachi2-pypiserver-auth -

    echo -e "\n--- Starting pypiserver on http://127.0.0.1:${HERMETO_TEST_PYPISERVER_PORT:-8080} ---\n"
}

setup >&2

podman run --rm --replace --name cachi2-pypiserver \
    -v cachi2-pypiserver-packages:/data/packages \
    -v cachi2-pypiserver-auth:/data/auth \
    -p "${HERMETO_TEST_PYPISERVER_PORT:-$DEFAULT_PORT}":8080 \
    "${PYPISERVER_IMAGE:-$DEFAULT_IMAGE}" \
        run \
        --passwords /data/auth/.htpasswd \
        --authenticate update,download,list \
        --disable-fallback \
        /data/packages
