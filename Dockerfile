FROM registry.access.redhat.com/ubi10@sha256:be840bb76e74900d39d5e4620c184e89382dcfce09cb05c98b4e1246d55612a5 AS ubi
FROM mirror.gcr.io/library/golang:1.27.1-alpine AS golang
FROM mirror.gcr.io/library/node:24.18-bookworm-slim AS node
FROM mirror.gcr.io/library/rust:1.97.1-slim-bookworm AS rust

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
    && dnf clean all

# Install dependencies in a separate layer to maximize layer caching
COPY requirements.txt requirements-build.txt ./
RUN python3.12 -m venv /venv && \
    /venv/bin/pip install -r requirements-build.txt --no-deps --no-cache-dir --require-hashes && \
    /venv/bin/pip install -r requirements.txt --no-deps --no-cache-dir --require-hashes

COPY . .
RUN /venv/bin/pip install --no-cache-dir .

##########################
# ASSEMBLE THE FINAL IMAGE
##########################
FROM base AS production
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

#########
# TOOLBOX
#########
FROM production AS toolbox
LABEL maintainer="Hermeto project"
LABEL org.opencontainers.image.title="hermeto-toolbox"
LABEL org.opencontainers.image.description="Hermeto toolbox image"
LABEL org.opencontainers.image.url="https://github.com/hermetoproject/hermeto"

COPY .toolbox/host-runner.sh /usr/local/libexec/host-runner.sh

# - flatpak-spawn backs the host tools delegation hack below
# - git is needed for all interactive git actions inside a toolbox container
RUN dnf -y install \
    --setopt install_weak_deps=0 \
    --nodocs \
    flatpak-spawn \
    git \
    sudo && \
    dnf clean all

# - no need for the legacy cachi2 entrypoint here - drop it
# - enable host tools delegation (official hack): https://github.com/containers/toolbox/issues/145#issuecomment-582040463
RUN rm -f /usr/local/bin/cachi2 && \
    ln -s /usr/local/libexec/host-runner.sh /usr/local/bin/podman && \
    ln -s /usr/local/libexec/host-runner.sh /usr/local/bin/buildah

ENTRYPOINT []
CMD ["/usr/local/bin/hermeto"]

##############################################################################
# This is an explicit way of restoring 'production' as the default build target.
##############################################################################
FROM production
