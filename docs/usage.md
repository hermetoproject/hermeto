# Usage

This document is split into two general sections. The first goes through the
general process of pre-fetching dependencies and injecting relevant
configuration and content for building an application in a hermetic environment.
The second section goes through each of these steps for the supported package
managers.

* [General process](#general-process)
  * [Pre-fetch dependencies](#pre-fetch-dependencies-general)
  * [Generate environment variables](#generate-environment-variables-general)
  * [Inject project files](#inject-project-files-general)
  * [Merge SBOMs](#merge-sboms-general)
  * [Building the artifact](#building-the-artifact-with-the-pre-fetched-dependencies-general)
    * [Set the environment variables](#write-the-dockerfile-general)
    * [Container build example](#build-the-container-general)
* [Usage Examples](#usage-examples)
  * [Example with Go modules](#example-go-modules)
  * [Example with pip](#example-pip)
  * [Example with npm](#example-npm)
  * [Example with yarn](#example-yarn)
  * [Example with generic fetcher](#example-generic-fetcher)

## General Process

A hermetic build environment is one that is fully encapsulated and isolated from
outside influences. When a build is run on a build platform, this encapsulation
can guarantee that the platform has a complete picture of all dependencies
needed for the build. One class of hermetic build implementations is to restrict
external network access during the build itself, requiring that all dependencies
are declared and pre-fetched before the build occurs.

In order to support this class of hermetic builds, not only does Hermeto need to
pre-fetch the dependencies, but some build flows will need additional changes
(i.e. leveraging defined [environment variables](#generate-environment-variables-general)
or using Hermeto to [inject project files](#inject-project-files-general)).

Hermeto relies on git metadata when processing sources, it expects sources to
be a valid git repository with "origin" remote defined. This is paramount
for successful execution. If for some reason you don't have a git repository,
e.g. you're trying to use Hermeto on an unpacked tarball, you may also get
acceptable results by forcefully creating a git repository from it first.
Make sure to set the remote as well.

<details>
  <summary><em>Workaround to create a local-only git repository</em></summary>

```shell
git init && git add -A && git commit -m "initial commit" && \
git remote add origin https://github.com/someorg/somerepo
```

<strong>WARNING!</strong>
</br>This is not a substitute for having a proper repository and should be used
only for testing!
</details>

Note however, that this is only good for smoke testing a scenario and there are
no guarantees for any results without proper and correct git metadata, e.g. git tags.

### Pre-fetch dependencies (general)

The first step in creating hermetic builds is to fetch the dependencies for one
of the supported package managers.

Hermeto can be run as follows

```shell
hermeto fetch-deps \
  --source ./foo \
  --output ./hermeto-output \
  --sbom-output-type cyclonedx \
  '{"path": ".", "type": "<supported package manager>"}'
```

* `--source` the path to a *git repository* on the local disk `[default: .]`
* `--output` the path to the directory where Hermeto will write all output
  `[default: ./hermeto-output]`
* `--sbom-output-type` the format of generated SBOM, supported values are
  `cyclonedx` (outputs [CycloneDX v1.4][]) and `spdx` (outputs [SPDX v2.3][])
  `[default: cyclonedx]`
* `{JSON}` specifies a *package* (a directory) within the repository to process

Note that Hermeto does not auto-detect which package managers your project uses.
You need to tell Hermeto what to process when calling fetch-deps. In the example
above, the package path is located at the root of the foo repo, hence the
relative path is `.`.

The main parameter (PKG) can handle different types of definitions

* simple: `<package manager>`, same as `{"path": ".", "type": "<package manager>"}`
* JSON object: `{"path": "subpath/to/other/module", "type": "<package manager>"}`
* JSON array: `[{"path": ".", "type": "<package manager>"}, {"path":
  "subpath/to/other/module", "type": "<package manager>"}]`
* JSON object with flags: `{"packages": [{"path": ".", "type": "<package
  manager>"}], "flags": ["cgo-disable"]}`

See also `hermeto fetch-deps --help`.

Using the JSON array object, multiple package managers can be used to resolve
dependencies in the same repository.

*⚠ While Hermeto does not intentionally modify the source repository unless the
output and source paths are the same, some package managers may add missing data
like checksums as dependency data is resolved. If this occurs from a clean git
tree then the tree has the possibility to become dirty.*

### Generate environment variables (general)

Once the dependencies have been cached, the build process needs to be made aware
of the dependencies. Some package managers need to be informed of cache
customizations by environment variables.

In order to simplify this process, Hermeto provides a helper command to generate
the environment variables in an easy-to-use format. The example above uses the
"env" format which generates a simple shell script that `export`s the required
variables (properly shell quoted when necessary). You can `source` this file to
set the variables.

```shell
hermeto generate-env ./hermeto-output -o ./hermeto.env --for-output-dir /tmp/hermeto-output
```

* `-o` the output path for the generated environment file

Don't worry about the `--for-output-dir` option yet - and about the fact that
the directory does not exist - it has to do with the target path where we will
mount the output directory [during the build](#build-the-container-general).

See also `hermeto generate-env --help`.

### Inject project files (general)

While some package managers only need an environment file to be informed of the
cache locations, others may need to create a configuration file or edit aBuild
the lockfile (or some other file in your project directory).

Before starting your build, call `hermeto inject-files` to automatically make
the necessary changes in your repository (based on data in the fetch-deps output
directory). Please do not change the absolute path to the repo between the calls
to fetch-deps and inject-files; if it's not at the same path, the inject-files
command won't find it.

```shell
hermeto inject-files ./hermeto-output --for-output-dir /tmp/hermeto-output
```

The `--for-output-dir` option has the same meaning as the one used when
generating environment variables.

*⚠ Hermeto may overwrite existing files. Please make sure you have no
un-committed changes (that you are not prepared to lose) when calling
inject-files.*

*⚠ Hermeto may change files if required by the package manager. This means that
the git status will become dirty if it was previously clean. If any scripting
depends on the cleanliness of a git repository and you do not want to commit the
changes, the scripting should either be changed to handle the dirty status or
the changes should be temporarily stashed by wrapping in `git stash && <command>
&& git stash pop` according to the suitability of the context.*

### Merge SBOMs (general)

Sometimes it might be necessary to merge two or more SBOMs. This could be done
with `hermeto merge-sboms`

```shell
hermeto merge-sboms <hermeto_sbom_1.json> ... <hermeto_sbom_n.json>
```

The subcommand expects at least two SBOMs, all produced by Hermeto, and will
exit with error otherwise. The reason for this is that Hermeto supports a
[limited set][] of component [properties][], and it validates that no other
properties exist in the SBOM. By default the result of a merge will be printed
to stdout. To save it to a file use `-o` option

```shell
hermeto merge-sboms <hermeto_sbom_1.json> ... <hermeto_sbom_n.json> -o <merged_sbom.json>
```

### Building the Artifact with the Pre-fetched dependencies (general)

After the pre-fetch and the above steps to inform the package manager(s) of the
cache have been completed, it all needs to be wired up into a build. The primary
use case for building these is within a Dockerfile or Dockerfile but the same
principles can be applied to other build strategies.

#### Write the Dockerfile (general)

Now that we have pre-fetched our dependencies and enabled package manager
configuration to point to them, we now need to ensure that the build process
(i.e. a Dockerfile or Dockerfile for a container build) is properly written
to build in a network isolated mode. All injected files are changed in the
source itself, so they will be present in the build context for the
Dockerfile. The environment variables added to the `hermeto.env` file,
however, will not be pulled into the build process without a specific action to
`source` the generated file.

Outside of this additional `source` directive in any relevant `RUN` command, the
rest of a container build can remain unchanged.

```dockerfile
FROM golang:1.19.2-alpine3.16 AS build

COPY ./foo /src/foo
WORKDIR /src/foo

RUN source /tmp/hermeto.env && \
    make build

FROM registry.access.redhat.com/ubi9/ubi-minimal:9.0.0

COPY --from=build /foo /usr/bin/foo
```

*⚠ The `source`d environment variables do not persist to the next RUN
instruction. The sourcing of the file and the package manager command(s) need to
be in the same instruction. If the build needs more than one command and you
would like to split them into separate RUN instructions, `source` the
environment file in each one.*

```dockerfile
RUN source /tmp/hermeto.env && \
    go build -o /foo cmd/foo && \
    go build -o /bar cmd/bar

# or, if preferrable
RUN source /tmp/hermeto.env && go build -o /foo cmd/foo
RUN source /tmp/hermeto.env && go build -o /bar cmd/bar
```

#### Build the container (general)

Now that the Dockerfile or Container file is configured, the next step is to
build the container itself. Since more than just the source code context is
needed to build the container, we also need to make sure that there are
appropriate volumes mounted for the Hermeto output as well as the Hermeto
environment variable that is being `source`d within the build. Since all
dependencies are cached, we can confidently restrict the network from the
container build as well!

```shell
podman build . \
  --volume "$(realpath ./hermeto-output)":/tmp/hermeto-output:Z \
  --volume "$(realpath ./hermeto.env)":/tmp/hermeto.env:Z \
  --network none \
  --tag foo

# test that it worked
podman run --rm -ti foo
```

We use the `--volume` option to mount Hermeto resources into the container build
— the output directory at /tmp/hermeto-output and the environment file at
/tmp/hermeto.env.

The path where the output directory gets mounted is important. Some environment
variables or project files may use absolute paths to content in the output
directory; if the directory is not at the expected path, the paths will be
wrong. Remember the `--for-output-dir` option used when
[generating the env file](#generate-environment-variables-general) and
[injecting the project files](#inject-project-files-general)? The absolute path
to ./hermeto-output on your machine is (probably) not /tmp/hermeto-output. That
is why we had to tell the generate-env command what the path inside the
container is eventually going to be.

In order to run the build with network isolation, use the `--network=none`
option. Note that this option only works if your podman/buildah version contains
the fix for [buildah#4227][] (buildah >= 1.28). In older versions, a workaround
could be to manually create an internal network (but you'll need root
privileges): `sudo podman network create --internal isolated-network; sudo
podman build --network isolated-network ...`.

## Usage Examples

Now that we are familiar with the overall process, we will go through an example
for each of the supported package managers.

### Example: Go modules

Let's show Hermeto usage by building the glorious [fzf][] CLI tool hermetically.
To follow along, clone the repository to your local disk.

```shell
git clone https://github.com/junegunn/fzf --branch=0.34.0
```

#### Pre-fetch dependencies (Go)

In order to pre-fetch the dependencies, we will pass the source and output
directories as well as the path for the `gomod` package manager to be able to
find the `go.mod` file.

See [the gomod documentation][] for more details about running Hermeto for
pre-fetching gomod dependencies.

```shell
hermeto fetch-deps \
  --source ./fzf \
  --output ./hermeto-output \
  '{"path": ".", "type": "gomod"}'
```

#### Generate environment variables (Go)

Next, we need to generate the environment file so that the `go build` command
can find the cached dependencies

```shell
hermeto generate-env ./hermeto-output -o ./hermeto.env --for-output-dir /tmp/hermeto-output
```

We can see the variables needed by the compiler

```shell
$ cat hermeto.env
export GOCACHE=/tmp/hermeto-output/deps/gomod
export GOMODCACHE=/tmp/hermeto-output/deps/gomod/pkg/mod
export GOPATH=/tmp/hermeto-output/deps/gomod
```

#### Inject project files (Go)

While the `gomod` package manager does not *currently* need to modify any
content in the source directory to inject the dependencies, the `inject-files`
command should be run to ensure that the operation is performed if this step
becomes a requirement in the future.

```shell
hermeto inject-files ./hermeto-output --for-output-dir /tmp/hermeto-output
```

#### Write the Dockerfile (or Dockerfile) (Go)

As mentioned in the steps above, the only change that needs to be made in the
Dockerfile or Dockerfile is to source the environment file before building
the binary.

```dockerfile
FROM golang:1.19.2-alpine3.16 AS build

COPY ./fzf /src/fzf
WORKDIR /src/fzf

RUN source /tmp/hermeto.env && \
    go build -o /fzf

FROM registry.access.redhat.com/ubi9/ubi-minimal:9.0.0

COPY --from=build /fzf /usr/bin/fzf

CMD ls | fzf
```

#### Build the container (Go)

Finally, we can build and test the container to ensure that we have successfully
built the binary.

```shell
podman build . \
  --volume "$(realpath ./hermeto-output)":/tmp/hermeto-output:Z \
  --volume "$(realpath ./hermeto.env)":/tmp/hermeto.env:Z \
  --network none \
  --tag fzf

# test that it worked
podman run --rm -ti fzf
```

### Example: pip

Let's build
[atomic-reactor][].
Atomic-reactor already builds with Cachito (Hermeto's spiritual ancestor), which
makes it a rare example of a Python project that meets Hermeto's requirements
out of the box (see [the pip documentation][]).

Get the repo if you want to try for yourself

```shell
git clone https://github.com/containerbuildsystem/atomic-reactor --branch=4.4.0
```

#### Pre-fetch dependencies (pip)

The steps for pre-fetching the dependencies is similar to before, but this time
we will use the `pip` package manager type. The default behavior path of `.` is
assumed. Additional parameters are also configured to point Hermeto at the
various requirements files that are needed to fully resolve dependencies.

See [the pip documentation][] for more details about running Hermeto for
pre-fetching pip dependencies.

```shell
hermeto fetch-deps --source ./atomic-reactor '{
  "type": "pip",
  "requirements_files": ["requirements.txt"],
  "requirements_build_files": ["requirements-build.txt", "requirements-pip.txt"]
}'
```

#### Generate environment variables (pip)

Next, we need to generate the environment file so that the `pip install` command
can find the cached dependencies

```shell
hermeto generate-env ./hermeto-output -o ./hermeto.env --for-output-dir /tmp/hermeto-output
```

We can see the variables needed by the package manager

```shell
$ cat hermeto.env
export PIP_FIND_LINKS=/tmp/hermeto-output/deps/pip
export PIP_NO_INDEX=true
```

#### Inject project files (pip)

In order to be able to install pip dependencies in a hermetic environment, we
need to perform the injection to change the remote dependencies to instead point
to the local file system.

```shell
$ hermeto inject-files ./hermeto-output --for-output-dir /tmp/hermeto-output
2023-01-26 16:41:09,990 INFO Overwriting /tmp/test/atomic-reactor/requirements.txt
```

We can look at the `git diff` to see what the package remapping looks like. As
an example,

```diff
diff --git a/requirements.txt b/requirements.txt
-osbs-client @ git+https://github.com/containerbuildsystem/osbs-client@8d7d7fadff38c8367796e6ac0b3516b65483db24
-    # via -r requirements.in
+osbs-client @ file:///tmp/hermeto-output/deps/pip/github.com/containerbuildsystem/osbs-client/osbs-client-external-gitcommit-8d7d7fadff38c8367796e6ac0b3516b65483db24.tar.gz
```

*⚠ This is only needed for [external dependencies][].
If all dependencies come from PyPi, Hermeto will not replace anything.*

#### Build the base image (pip)

For this example, we will split the build into two parts - a base image and the
final application image. Since there is no way to install RPMs in a hermetic
environment, we will create the base image with its required "devel" libraries
from RPMs in one image and then use that image for our hermetic python build.

If your project doesn't need to compile as many C packages as atomic-reactor,
you may be able to find a base image that already contains everything you need.

Dockerfile.baseimage

```dockerfile
FROM quay.io/centos/centos:stream8

# python3.8 runtime, C build dependencies
RUN dnf -y install \
        python38 \
        python38-pip \
        python38-devel \
        gcc \
        make \
        libffi-devel \
        krb5-devel \
        cairo-devel \
        cairo-gobject-devel \
        gobject-introspection-devel \
        openssl-devel && \
    dnf clean all
```

This container build might be what we are familiar with already as we are not
using Hermeto or enforcing network isolation.

```shell
podman build . -f Dockerfile.baseimage --tag atomic-reactor-base-image:latest
```

#### Build the application image (pip)

We will base the final application image on our custom base image. The base
image build installed all the RPMs we will need, so the final phase can use
network isolation again 🎉. In order to support the network isolated build, we
need to remember to `source` the environment file in the step that executes `pip
install`. Because `osbs-client` comes from GitHub, the source code in
`/src/atomic-reactor` has also been changed so that the dependencies are
pointing to the cached versions.

Dockerfile

```dockerfile
FROM atomic-reactor-base-image:latest

COPY atomic-reactor/ /src/atomic-reactor
WORKDIR /src/atomic-reactor

# Need to source the hermeto.env file to set the environment variables
# (in the same RUN instruction as the pip commands)
RUN source /tmp/hermeto.env && \
    # We're using network isolation => cannot build the cryptography package with Rust
    # (it downloads Rust crates)
    export CRYPTOGRAPHY_DONT_BUILD_RUST=1 && \
    python3.8 -m pip install -U pip && \
    python3.8 -m pip install --use-pep517 -r requirements.txt && \
    python3.8 -m pip install --use-pep517 .

CMD ["python3.8", "-m", "atomic_reactor.cli.main", "--help"]
```

We can then build the image as before while mounting the required Hermeto data!

```shell
podman build . \
  --volume "$(realpath ./hermeto-output)":/tmp/hermeto-output:Z \
  --volume "$(realpath ./hermeto.env)":/tmp/hermeto.env:Z \
  --network none \
  --tag atomic-reactor
```

### Example: npm

Let's build simple npm project [sample-nodejs-app][]. Get
the repo if you want to try for yourself

```shell
git clone https://github.com/cachito-testing/sample-nodejs-app.git
```

#### Pre-fetch dependencies (npm)

The steps for pre-fetching the dependencies is similar to before, but this time
we will use the `npm` package manager type. The default behavior path of `.` is
assumed.

See [the npm documentation][] for more details about running Hermeto for
pre-fetching npm dependencies.

```shell
hermeto fetch-deps --source ./sample-nodejs-app --output ./hermeto-output '{"type": "npm"}'
```

#### Generate environment variables (npm)

Next, we need to generate the environment file, so we can provide environment
variables to the `npm install` command.

```shell
hermeto generate-env ./hermeto-output -o ./hermeto.env --for-output-dir /tmp/hermeto-output
```

Currently, Hermeto does not require any environment variables for the npm
package manager, but this might change in the future.

#### Inject project files (npm)

In order to be able to install npm dependencies in a hermetic environment, we
need to perform the injection to change the remote dependencies to instead point
to the local file system.

```shell
hermeto inject-files ./hermeto-output --for-output-dir /tmp/hermeto-output
```

We can look at the `git diff` to see what the package remapping looks like. As
an example,

```diff
diff --git a/package-lock.json b/package-lock.json
-      "resolved": "https://registry.npmjs.org/accepts/-/accepts-1.3.8.tgz",
+      "resolved": "file:///tmp/hermeto-output/deps/npm/accepts-1.3.8.tgz",
```

#### Build the application image (npm)

We will base the final application image on `node:18` base image. The base image
build has `npm` pre-installed, so the final phase can use network isolation 🎉.

```dockerfile
FROM node:18

COPY sample-nodejs-app/ /src/sample-nodejs-app
WORKDIR /src/sample-nodejs-app

# Run npm install command and list installed packages
RUN . /tmp/hermeto.env && npm i && npm ls

EXPOSE 9000

CMD ["node", "index.js"]
```

We can then build the image as before while mounting the required Hermeto data!

```shell
podman build . \
  --volume "$(realpath ./hermeto-output)":/tmp/hermeto-output:Z \
  --volume "$(realpath ./hermeto.env)":/tmp/hermeto.env:Z \
  --network none \
  --tag sample-nodejs-app
```

### Example: yarn

For the Yarn example let's use the same sample Node.js [project][], but
this time modified to use Yarn as the package manager. Get the repo if you want
to try for yourself

```shell
git clone -b yarn https://github.com/cachito-testing/sample-nodejs-app.git
```

#### Pre-fetch dependencies (yarn)

The steps for pre-fetching the dependencies are very similar to the previous
examples, this time using the Yarn package manager. Like with the previous
examples the default path for the package we assume is `.`.

See [the Yarn documentation][] for more details about running Hermeto for
pre-fetching yarn dependencies.

```shell
hermeto fetch-deps --source ./sample-yarn-app --output ./hermeto-output '{"type": "yarn"}'
```

OR more simply (without the need of a JSON formatted argument) just

```shell
hermeto fetch-deps --source ./sample-yarn-app --output ./hermeto-output yarn
```

#### Generate environment variables (yarn)

There are a few environment variables we'll have to set for Yarn during the
hermetic build, so we need to generate an environment file.

```shell
$ hermeto generate-env ./hermeto-output -o ./hermeto.env --for-output-dir /tmp/hermeto-output
$ cat ./hermeto.env
export YARN_ENABLE_GLOBAL_CACHE=false
export YARN_ENABLE_IMMUTABLE_CACHE=false
export YARN_ENABLE_MIRROR=true
export YARN_GLOBAL_FOLDER=/tmp/hermeto-output/deps/yarn
```

#### Inject project files (yarn)

Like the `gomod` package manager Yarn does not *currently* need to modify any
content in the source directory for the cached dependencies to be used in a
hermetic build, however that might change in the future.

#### Build the application image (yarn)

Yarn is installed using a Node.js tool called
[Corepack][] which has been shipped
by Node.js by default since v16.9.0 and v14.19.0. Therefore, we'll use the
`node:18` base image in our example which definitely has Corepack and we can
start using Yarn right away.

```dockerfile
FROM node:18

COPY sample-yarn-app/ /src/sample-yarn-app
WORKDIR /src/sample-yarn-app

# Run yarn install command and list installed packages
RUN . /tmp/hermeto.env && yarn install

EXPOSE 9000

CMD ["yarn", "run", "start"]
```

We can then build the image as before while mounting the required Hermeto data!

```shell
podman build . \
  --volume "$(realpath ./hermeto-output)":/tmp/hermeto-output:Z \
  --volume "$(realpath ./hermeto.env)":/tmp/hermeto.env:Z \
  --network none \
  --tag sample-nodejs-app
```

### Example: generic fetcher

Generic fetcher is a package manager that can fetch arbitrary files. Let's build
a sample container image that would be inconvenient to build hermetically
otherwise. This image will provide [OWASP Dependency check][] tool, which is available
to install from GitHub releases page. Get the repo if you want to try for
yourself

```shell
git clone -b sample-app https://github.com/cachito-testing/cachi2-generic.git
```

#### Pre-fetch dependencies (generic fetcher)

In order to retrieve the archive with the tool, either a `artifacts.lock.yaml`
needs to be in the repository, or an absolute path needs to be supplied in the
JSON input, pointing to a lockfile. You can find a sample lockfile below. It is
identical to the one found in the [sample repository][].
A lockfile for the generic fetcher must contain a `metadata` header and a list
of artifacts, where each artifact is represented as a pair of URL and a checksum
string in the format of `"algorithm:checksum"`. Optionally, you can also specify
an output `filename` for the artifact. If not specified, it will be derived from
the url.

```yaml
---
metadata:
  version: "1.0"
artifacts:
  - download_url: "https://github.com/jeremylong/DependencyCheck/releases/download/v11.1.0/dependency-check-11.1.0-release.zip"
    checksum: "sha256:c5b5b9e592682b700e17c28f489fe50644ef54370edeb2c53d18b70824de1e22"
    filename: "dependency-check.zip"
```

As with other examples, the command to fetch dependencies is very similar. The
default path is assumed to be `.`.

```shell
hermeto fetch-deps --source ./hermeto-generic --output ./hermeto-output generic
```

#### Build the application image (generic fetcher)

We'll use the `ibmjava:11-jdk` as base image because it already has java
pre-installed. During the build, the downloaded release will be extracted and
modified to have execute rights.

```dockerfile
FROM ibmjava:11-jdk

WORKDIR /tmp


# use jar to unzip file in order to avoid having to install more dependencies
RUN jar -xvf hermeto-output/deps/generic/dependency-check.zip

RUN chmod +x dependency-check/bin/dependency-check.sh

ENTRYPOINT ["/tmp/dependency-check/bin/dependency-check.sh", "--version"]
```

We can then build the image as before while mounting the required Hermeto data.

```shell
podman build . \
  --volume "$(realpath ./hermeto-output)":/tmp/hermeto-output:Z \
  --network none \
  --tag sample-generic-app
```

[atomic-reactor]: https://github.com/containerbuildsystem/atomic-reactor
[buildah#4227]: https://github.com/containers/buildah/issues/4227
[Corepack]: https://nodejs.org/api/corepack.html#corepack
[CycloneDX v1.4]: https://cyclonedx.org/docs/1.4/json
[external dependencies]: pip.md#external-dependencies
[fzf]: https://github.com/junegunn/fzf
[limited set]: https://github.com/hermetoproject/hermeto/blob/main/hermeto/core/models/sbom.py#L7-L13
[OWASP Dependency check]: https://github.com/jeremylong/DependencyCheck
[project]: https://github.com/cachito-testing/sample-nodejs-app/tree/yarn
[properties]: https://cyclonedx.org/docs/1.4/json/#components_items_properties
[sample repository]: https://github.com/cachito-testing/cachi2-generic/tree/sample-app
[sample-nodejs-app]: https://github.com/cachito-testing/sample-nodejs-app
[SPDX v2.3]: https://spdx.github.io/spdx-spec/v2.3/
[the gomod documentation]: gomod.md
[the npm documentation]: npm.md
[the pip documentation]: pip.md
[the Yarn documentation]: yarn.md
