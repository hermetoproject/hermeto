FROM registry.access.redhat.com/ubi9/ubi@sha256:304b50df1ea4db9706d8a30f4bbf26f582936ebc80c7e075c72ff2af99292a54

# Test disabled network access
RUN if curl -IsS www.google.com; then echo "Has network access!"; exit 1; fi

# Install dependencies from RPM lockfile
RUN dnf -y install golang npm

WORKDIR /src/gomod-package
RUN . /tmp/hermeto.env && go build

WORKDIR /src/npm-package
RUN . /tmp/hermeto.env && npm install
