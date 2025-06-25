# Usage

- [General process](#general-process)
  - [Pre-fetch dependencies](#pre-fetch-dependencies)
  - [Generate environment variables](#generate-environment-variables)
  - [Inject project files](#inject-project-files)
  - [Merge SBOMs](#merge-sboms)
  - [Building the artifact](#building-the-artifact-with-the-pre-fetched-dependencies)
    - [Set the environment variables](#write-the-dockerfile)
    - [Container build example](#build-the-container)

## General Process

A hermetic build environment is one that is fully encapsulated and isolated from
outside influences. When a build is run on a build platform, this encapsulation
can guarantee that the platform has a complete picture of all dependencies
needed for the build. One class of hermetic build implementations is to restrict
external network access during the build itself, requiring that all dependencies
are declared and pre-fetched before the build occurs.

In order to support this class of hermetic builds, not only does Hermeto need to
pre-fetch the dependencies, but some build flows will need additional changes
(i.e. leveraging defined [environment variables](#generate-environment-variables)
or using Hermeto to [inject project files](#inject-project-files)).

Hermeto relies on git metadata when processing sources, it expects sources to
be a valid git repository with "origin" remote defined. This is paramount
for successful execution. If for some reason you don't have a git repository,
e.g. you're trying to use Hermeto on an unpacked tarball, you may also get
acceptable results by forcefully creating a git repository from it first.
Make sure to set the remote as well.

<details>
  <summary><em>Workaround to create a local-only git repository</em></summary>

<!-- markdownlint-disable-next-line no-inline-html -->
<pre>
git init && git add -A && git commit -m "initial commit" && \
git remote add origin https://github.com/someorg/somerepo
</pre>

<strong>WARNING!</strong>
</br>This is not a substitute for having a proper repository and should be used
only for testing!
</details>

Note however, that this is only good for smoke testing a scenario and there are
no guarantees for any results without proper and correct git metadata, e.g. git tags.

### Pre-fetch dependencies

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

- `--source` the path to a *git repository* on the local disk `[default: .]`
- `--output` the path to the directory where Hermeto will write all output
  `[default: ./hermeto-output]`
- `--sbom-output-type` the format of generated SBOM, supported values are
  `cyclonedx` (outputs [CycloneDX v1.4][]) and `spdx` (outputs [SPDX v2.3][])
  `[default: cyclonedx]`
- `{JSON}` specifies a *package* (a directory) within the repository to process

Note that Hermeto does not auto-detect which package managers your project uses.
You need to tell Hermeto what to process when calling fetch-deps. In the example
above, the package path is located at the root of the foo repo, hence the
relative path is `.`.

The main parameter (PKG) can handle different types of definitions

- simple: `<package manager>`, same as `{"path": ".", "type": "<package manager>"}`
- JSON object: `{"path": "subpath/to/other/module", "type": "<package manager>"}`
- JSON array: `[{"path": ".", "type": "<package manager>"}, {"path":
  "subpath/to/other/module", "type": "<package manager>"}]`
- JSON object with flags: `{"packages": [{"path": ".", "type": "<package
  manager>"}], "flags": ["cgo-disable"]}`

See also `hermeto fetch-deps --help`.

Using the JSON array object, multiple package managers can be used to resolve
dependencies in the same repository.

*⚠ While Hermeto does not intentionally modify the source repository unless the
output and source paths are the same, some package managers may add missing data
like checksums as dependency data is resolved. If this occurs from a clean git
tree then the tree has the possibility to become dirty.*

### Generate environment variables

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

- `-o` the output path for the generated environment file

Don't worry about the `--for-output-dir` option yet - and about the fact that
the directory does not exist - it has to do with the target path where we will
mount the output directory [during the build](#build-the-container).

See also `hermeto generate-env --help`.

### Inject project files

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

### Merge SBOMs

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

### Building the Artifact with the Pre-fetched dependencies

After the pre-fetch and the above steps to inform the package manager(s) of the
cache have been completed, it all needs to be wired up into a build. The primary
use case for building these is within a Dockerfile or Dockerfile but the same
principles can be applied to other build strategies.

#### Write the Dockerfile

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

# or, if preferable
RUN source /tmp/hermeto.env && go build -o /foo cmd/foo
RUN source /tmp/hermeto.env && go build -o /bar cmd/bar
```

#### Build the container

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
[generating the env file](#generate-environment-variables)
and [injecting the project files](#inject-project-files)?
The absolute path to ./hermeto-output on your machine is (probably) not
/tmp/hermeto-output. That is why we had to tell the generate-env command what
the path inside the container is eventually going to be.

In order to run the build with network isolation, use the `--network=none`
option. Note that this option only works if your podman/buildah version contains
the fix for [buildah#4227][] (buildah >= 1.28). In older versions, a workaround
could be to manually create an internal network (but you'll need root
privileges): `sudo podman network create --internal isolated-network; sudo
podman build --network isolated-network ...`.

[buildah#4227]: https://github.com/containers/buildah/issues/4227
[CycloneDX v1.4]: https://cyclonedx.org/docs/1.4/json
[limited set]: https://github.com/hermetoproject/hermeto/blob/main/hermeto/core/models/sbom.py#L7-L13
[properties]: https://cyclonedx.org/docs/1.4/json/#components_items_properties
[SPDX v2.3]: https://spdx.github.io/spdx-spec/v2.3/
