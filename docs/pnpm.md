# [pnpm][]

## Prerequisites

Ensure both package.json and pnpm-lock.yaml are present in the project directory.

The `lockfileVersion` field must be within the range of >= 9.0 and < 10.0. Earlier
versions are not supported. Newer versions could introduce breaking changes to the
lockfile format, so Hermeto might not be able to process them correctly.

## Usage

Using Hermeto locally with pnpm requires no special configuration or additional
software (including pnpm itself). Hermeto downloads dependencies only explicitly
declared in pnpm-lock.yaml without invoking the pnpm binary, which avoids arbitrary
code execution during prefetch and keeps the process predictable.

Make sure the file is up to date by running pnpm install. Otherwise, the hermetic
build will likely fail.

Each dependency is downloaded as a tar archive into the output directory under deps/pnpm/.
Packages from the npm and JSR registries are stored in separate subdirectories.

```text
hermeto-output/deps/pnpm/
├── npm/
│   ├── react-18.3.1.tgz
│   └── ...
├── jsr/
│   ├── ...
```

Hermeto modifies .npmrc and pnpm-lock.yaml to point registries and tarball URLs
to local files in the output directory. Other settings in .npmrc are preserved.
If the file does not exist, it is created.

These updates happen automatically when calling Hermeto's inject-files command.
See the [Example](#example) section below.

A snippet of .npmrc after modification:

```text
registry=file:///tmp/hermeto-output/deps/pnpm/npm/
@jsr:registry=file:///tmp/hermeto-output/deps/pnpm/jsr/
```

A snippet of pnpm-lock.yaml after modification:

```diff
packages:
  react@18.3.1:
    resolution:
      integrity: sha512-...
-     tarball: https://registry.npmjs.org/react/-/react-18.3.1.tgz
+     tarball: react-18.3.1.tgz
```

## SBOM

Hermeto stores the SBOM in the output directory (hermeto-output/bom.json) with
information about all dependencies. See the [SBOM](sbom.md) page for more details.

## Proxy

Configure the proxy via a config file. See the [Configuration](configuration.md)
page for supported config file locations.

```yaml
pnpm:
  proxy_url: https://my-npm-proxy.example.com
  proxy_login: user
  proxy_password: pass
```

Alternatively, configure the proxy via environment variables:

```shell
export HERMETO_PNPM__PROXY_URL=https://my-npm-proxy.example.com
export HERMETO_PNPM__PROXY_LOGIN=user
export HERMETO_PNPM__PROXY_PASSWORD=pass
```

> **NOTE**
>
> The proxy is used only for packages from the official npm registry. Other package
> sources do not work with the proxy.

## Example

### Fetch dependencies

Fetch all dependencies from pnpm-lock.yaml and generate an SBOM.

```shell
hermeto fetch-deps pnpm
tree hermeto-output/deps/pnpm
cat hermeto-output/bom.json
```

### Generate environment variables

> **NOTE**
>
> This step is only required when using pnpm v11.3 and later.

Starting with pnpm 11.3, the [trustLockfile][] setting controls whether `pnpm install`
re-applies supply-chain verification checks to lockfile entries. Hermeto injects
pnpm-lock.yaml to point tarball URLs to local files, so this verification must be
skipped during the hermetic build.

Hermeto sets `PNPM_CONFIG_TRUST_LOCKFILE=true` for this purpose.

```shell
hermeto generate-env ./hermeto-output --for-output-dir /tmp/hermeto-output --output hermeto.env
cat hermeto.env
```

### Inject project files

Modify pnpm-lock.yaml and .npmrc for the hermetic build.

```shell
hermeto inject-files ./hermeto-output --for-output-dir /tmp/hermeto-output
git diff
```

### Build the image offline

This example uses [generic](generic.md) backend artifact to install a specific
version of pnpm using `npm` binary. There is an [official pnpm base image][], but
without `node` runtime and no tags for pnpm v10. Unofficial images on Docker Hub
are also an option.

Dockerfile:

```dockerfile
FROM docker.io/library/node:24

# ...

RUN npm install -g /tmp/hermeto-output/deps/generic/pnpm-11.x.y.tgz

WORKDIR /app
COPY . .

RUN source /tmp/hermeto.env && pnpm install

# ...
```

> **NOTE**
> When using pnpm v10, you can omit sourcing hermeto.env and the volume mount in
> the command below.

```shell
podman build . \
  --volume "$(realpath ./hermeto-output)":/tmp/hermeto-output:Z \
  --volume "$(realpath ./hermeto.env)":/tmp/hermeto.env:Z \
  --network none \
  --tag pnpm-app
```

For more detailed instructions, see the [Usage](usage.md) page or run `hermeto --help`.

[pnpm]: https://pnpm.io
[trustLockfile]: https://pnpm.io/blog/releases/11.3#trustlockfile
[official pnpm base image]: https://pnpm.io/docker#official-pnpm-base-image
