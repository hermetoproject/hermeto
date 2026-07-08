FROM mirror.gcr.io/node:24-alpine

# Test disabled network access
RUN if getent hosts www.google.com; then echo "Has network access!"; exit 1; fi

WORKDIR /src

# The test fixture has lifecycle scripts that exit 1 (to verify Hermeto
# strips them during prefetch). Remove them here so the build can proceed.
RUN find packages -name package.json -exec \
    node -e "const f=process.argv[1]; const p=JSON.parse(require('fs').readFileSync(f)); delete p.scripts; require('fs').writeFileSync(f, JSON.stringify(p, null, 2)+'\n')" {} \;

RUN . /tmp/hermeto.env && yarn workspaces focus app

# During the build, the source code is only mounted, not copied
RUN cp -r /src /app
WORKDIR /app
