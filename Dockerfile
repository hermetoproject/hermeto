FROM registry.access.redhat.com/ubi10@sha256:9bd3aebdfdf6eebb6a18541d838cac9e9a35d2f807aa8e36d9518978cc86371f AS ubi
FROM mirror.gcr.io/library/golang:1.25.6-alpine AS golang
FROM mirror.gcr.io/library/node:24.13-bookworm-slim AS node
FROM mirror.gcr.io/library/rust:1.92.0-slim-bookworm AS rust

########################
# PREPARE OUR BASE IMAGE
########################
FROM ubi AS base
RUN dnf -y install \
    --setopt install_weak_deps=0 \
    --nodocs \
    git-core \
    jq \
    python3.12 \
    rubygem-bundler \
    rubygem-json \
    subscription-manager && \
    dnf clean all

###############
# BUILD/INSTALL
###############
FROM base AS builder
WORKDIR /src
RUN dnf -y install \
    --setopt install_weak_deps=0 \
    --nodocs \
    gcc \
    python3.12-devel \
    python3.12-pip \
    python3.12-setuptools \
    && dnf clean all

# Install dependencies in a separate layer to maximize layer caching
COPY requirements.txt .
RUN python3.12 -m venv /venv && \
    /venv/bin/pip install --upgrade pip && \
    /venv/bin/pip install -r requirements.txt --no-deps --no-cache-dir --require-hashes

COPY . .
RUN /venv/bin/pip install --no-cache-dir .

##########################
# ASSEMBLE THE FINAL IMAGE
##########################
FROM base
LABEL maintainer="Red Hat"

# copy Go SDK and Node.js installation from official images
COPY --from=golang /usr/local/go /usr/local/go
COPY --from=node /usr/local/lib/node_modules/corepack /usr/local/lib/corepack
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=rust /usr/local/rustup/toolchains/*/bin/cargo /usr/bin/cargo
COPY --from=builder /venv /venv

# link corepack, yarn, and go to standard PATH location
RUN ln -s /usr/local/lib/corepack/dist/corepack.js /usr/local/bin/corepack && \
    ln -s /usr/local/lib/corepack/dist/yarn.js /usr/local/bin/yarn && \
    ln -s /usr/local/go/bin/go /usr/local/bin/go && \
    ln -s /venv/bin/createrepo_c /usr/local/bin/createrepo_c && \
    ln -s /venv/bin/cachi2 /usr/local/bin/cachi2 && \
    ln -s /venv/bin/hermeto /usr/local/bin/hermeto

ENTRYPOINT ["/usr/local/bin/hermeto"]
