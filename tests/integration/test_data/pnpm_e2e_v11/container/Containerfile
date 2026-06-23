FROM mirror.gcr.io/node:24-alpine

# Test disabled network access
RUN if getent hosts www.google.com; then echo "Has network access!"; exit 1; fi

# Install pnpm from generic backend artifact
RUN npm install -g /tmp/hermeto-output/deps/generic/pnpm-11.9.0.tgz

WORKDIR /src
RUN . /tmp/hermeto.env && pnpm install
