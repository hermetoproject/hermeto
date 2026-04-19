# [npm][]

See also the [npm docs][]

- [Specifying packages to process](#specifying-packages-to-process)
- [Project files](#project-files)
  - [Dependencies](#dependencies)
- [Using fetched dependencies](#using-fetched-dependencies)
  - [Changes made by the inject-files command](#changes-made-by-the-inject-files-command)
- [Full example walkthrough](#example)

## Specifying packages to process

A package is a file or directory that is described by a package.json file.

- The project files for npm are package.json and one of package-lock.json or
  npm-shrinkwrap.json. See [Project files](#project-files) and npm
  documentation

  - See [package.json][]
  - See [package-lock.json][]

Notice that the package-lock.json version must be **higher than v1** (Node.js 15
or higher)! Package-lock.json v1 is not supported in Hermeto.

Hermeto can be run as follows

```shell
hermeto fetch-deps \
  --source ./my-repo \
  --output ./hermeto-output \
  '<JSON input>'
```

where 'JSON input' is

```js
{
  // "npm" tells Hermeto to process npm packages
  "type": "npm",
  // path to the package (relative to the --source directory)
  // defaults to "."
  "path": ".",
}
```

or more simply by just invoking `hermeto fetch-deps npm`.

## Project files

Hermeto downloads dependencies explicitly declared in project files -
package.json and package-lock.json. The npm CLI manages the package-lock.json
file automatically. To make sure the file is up to date, you can use
[npm install][].

Possible dependency types in the above-mentioned files are described in the
following section.

### Dependencies

The "npm package" formats that Hermeto can process are the following

1. A folder containing a program described by a 'package.json' file
2. A gzipped tarball containing the previous
3. A URL that resolves to the previous
4. A `<name>@<version>` that is published on the registry with the previous
5. A `<name>@<tag>` that points to the previous
6. A `<name>` that has a latest tag satisfying the previous
7. A git url that, when cloned, results in... the first item in this list

Examples of (package.json) dependency formats

(For the full list of dependency formats with explanation,
see the [npm documentation][])

<details>
  <summary>Dependencies from npm registries</summary>

```js
{
  "dependencies": {
    "foo": "1.0.0 - 2.9999.9999",
    "bar": ">=1.0.2 <2.1.2",
    "baz": ">1.0.2 <=2.3.4",
    "boo": "2.0.1",
    ...
  }
}
```

</details>

<details>
  <summary>URLs as dependencies</summary>

```js
{
  "dependencies": {
    "cli_bar": git+ssh://git@github.com:npm/cli.git#v1.0.27,
    "cli_foo": git://github.com/npm/cli.git#v1.0.1
  }
}
```

</details>

<details>
  <summary>GitHub URLs</summary>

```js
{
  "dependencies": {
    "express": "expressjs/express",
    "mocha": "mochajs/mocha#4727d357ea",
    "module": "user/repo#feature/branch"
  }
}
```

</details>

<details>
  <summary>Local paths</summary>

```js
{
  "name": "baz",
  "dependencies": {
    "bar": "file:../foo/bar"
  }
}
```

</details>

## Using fetched dependencies

See the [Example](#example) for a complete walkthrough of Hermeto usage.

Hermeto downloads npm dependencies as tar archives into the `deps/npm/`
subpath of the output directory. Registry dependencies are placed directly
there (e.g. `accepts-1.3.8.tgz`). Dependencies from Git or other HTTPS URLs
are placed in subdirectories as described in [Project files](#project-files).

```text
hermeto-output/deps/npm
├── accepts-1.3.8.tgz
├── array-flatten-1.1.1.tgz
├── ...
```

In order for the `npm install` command to use the fetched dependencies instead
of reaching for the npm registry, Hermeto needs to update
[project files](#project-files). These updates happen **automatically** when we
call Hermeto's [`inject-files`](#inject-project-files) command.

### Changes made by the inject-files command

The root 'package.json' file is updated together with 'package.json' files for
each [workspace][] with changes

- For git repositories and HTTPS URLs in dependencies update their value to an
  empty string

Hermeto command updates the following in the `package-lock.json` file

- Replace URLs found in resolved items with local paths to
  [fetched dependencies](#using-fetched-dependencies)
- Similarly to the above package.json changes, for git repositories and HTTPS
  URLs in package dependencies update their value to an empty string
- There is a corner case [bug][] which happens in older npm versions (spotted in
  8.12.1 version and lower) where npm mistakenly adds integrity checksum to git
  sources. To avoid errors while recreating git repository content as a tar
  archive and changing the integrity checksum, Hermeto deletes integrity items,
  which should not be there in the first place

After running `inject-files`, `resolved` URLs in `package-lock.json` point to
the output directory, for example:

```diff
-      "resolved": "https://registry.npmjs.org/accepts/-/accepts-1.3.8.tgz",
+      "resolved": "file:///tmp/hermeto-output/deps/npm/accepts-1.3.8.tgz",
```

### Example

Let's build a [basic npm project][].

Get the repo if you want to try for yourself:

```shell
git clone https://github.com/hermetoproject/doc-examples.git --branch=npm-basic && cd doc-examples
```

#### Pre-fetch dependencies

As mentioned above in
[Specifying packages to process](#specifying-packages-to-process),
Hermeto pre-fetches using the `fetch-deps` command.

Sources can be fetched with

```shell
hermeto fetch-deps npm
```

The shorthand `npm` defaults `path` to `.`. You can pass a full JSON
object if you need a custom source directory instead.

#### Generate environment variables

Next, we need to generate the environment file, so we can provide environment
variables to the `npm install` command.

```shell
hermeto generate-env ./hermeto-output -o ./hermeto.env --for-output-dir /tmp/hermeto-output
```

Currently, Hermeto does not require any environment variables for the npm
package manager, but this might change in the future.

#### Inject project files

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

#### Build the application image

The repo already contains a `Containerfile` for this example. Build it while
mounting the pre-fetched Hermeto data:

```shell
podman build . \
  --volume "$(realpath ./hermeto-output)":/tmp/hermeto-output:Z \
  --volume "$(realpath ./hermeto.env)":/tmp/hermeto.env:Z \
  --network none \
  --tag npm-example
```

[basic npm project]: https://github.com/hermetoproject/doc-examples/tree/npm-basic
[bug]: https://github.com/npm/cli/issues/2846
[npm]: https://www.npmjs.com
[npm docs]: https://docs.npmjs.com
[npm documentation]: https://docs.npmjs.com/cli/v9/configuring-npm/package-json#dependencies
[npm install]: https://docs.npmjs.com/cli/v9/commands/npm-install?v=true
[package-lock.json]: https://docs.npmjs.com/cli/v9/configuring-npm/package-lock-json
[package.json]: https://docs.npmjs.com/cli/v9/configuring-npm/package-json
[workspace]: https://docs.npmjs.com/cli/v9/using-npm/workspaces?v=true
