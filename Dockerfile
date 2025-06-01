FROM registry.access.redhat.com/ubi9/ubi@sha256:304b50df1ea4db9706d8a30f4bbf26f582936ebc80c7e075c72ff2af99292a54 as ubi
FROM docker.io/library/golang:1.20.0-bullseye as golang_120
FROM docker.io/library/golang:1.21.0-bullseye as golang_121
FROM docker.io/library/node:23.11.0-bullseye as node

########################
# PREPARE OUR BASE IMAGE
########################
FROM ubi as base
RUN dnf -y install \
    --setopt install_weak_deps=0 \
    --nodocs \
    git-core \
    jq \
    python3 \
    rubygem-bundler \
    rubygem-json \
    subscription-manager && \
    dnf clean all

###############
# BUILD/INSTALL
###############
FROM base as builder
WORKDIR /src
RUN dnf -y install \
    --setopt install_weak_deps=0 \
    --nodocs \
    gcc \
    # not a build dependency, but we copy the binary to the final image
    cargo \
    python3-devel \
    python3-pip \
    python3-setuptools \
    && dnf clean all

# Install dependencies in a separate layer to maximize layer caching
COPY requirements.txt .
RUN python3 -m venv /venv && \
    /venv/bin/pip install --upgrade pip && \
    /venv/bin/pip install -r requirements.txt --no-deps --no-cache-dir --require-hashes

COPY . .
RUN /venv/bin/pip install --no-cache-dir .

##########################
# ASSEMBLE THE FINAL IMAGE
##########################
FROM base
LABEL maintainer="Red Hat"

# copy Go SDKs and Node.js installation from official images
COPY --from=golang_120 /usr/local/go /usr/local/go/go1.20
COPY --from=golang_121 /usr/local/go /usr/local/go/go1.21
COPY --from=node /usr/local/lib/node_modules/corepack /usr/local/lib/corepack
COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=builder /usr/bin/cargo /usr/bin/cargo
COPY --from=builder /venv /venv

# link corepack, yarn, and go to standard PATH location
RUN ln -s /usr/local/lib/corepack/dist/corepack.js /usr/local/bin/corepack && \
    ln -s /usr/local/lib/corepack/dist/yarn.js /usr/local/bin/yarn && \
    ln -s /usr/local/go/go1.21/bin/go /usr/local/bin/go && \
    ln -s /venv/bin/createrepo_c /usr/local/bin/createrepo_c && \
    ln -s /venv/bin/cachi2 /usr/local/bin/cachi2 && \
    ln -s /venv/bin/hermeto /usr/local/bin/hermeto

ENTRYPOINT ["/usr/local/bin/hermeto"]
