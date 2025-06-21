# [Bundler][]

## Prerequisites

To use Hermeto with Bundler locally, ensure you have Ruby and Bundler installed
on your system.

```bash
sudo dnf install rubygem-bundler
```

Then ensure you have both, **Gemfile** and **Gemfile.lock** in your project
directory. We parse the **Gemfile.lock** to pre-fetch all dependencies
specified in that file.

## Basic usage

Run the following command in your terminal to pre-fetch your project's
dependencies. The command will download all dependencies specified in the
**Gemfile.lock** to the specified output directory.

```bash
cd path-to-your-ruby-project
hermeto fetch-deps bundler
```

In addition, it will prepare the necessary environment variables and
configuration files for the build phase. See the following sections for more
information.

### Gems

Each gem has a name, version, and platform. If the platform is "ruby", it means
that it should work on any platform Ruby runs on. Using the ruby platform means
ignoring the current machine's platform and installing only ruby platform gems.
As a result, gems with native extensions will be compiled from the source.

However, occasionally some gems do not have a version for the ruby platform and
are only available as pre-compiled binaries. In this case, you may need to
enable the pre-fetching of gems for specific platforms to avoid potential
failures with the `allow_binary` option set to `true` when running the
`fetch-deps` command.

```bash
cd path-to-your-ruby-project
hermeto fetch-deps '{"type": "bundler", "allow_binary": "true"}'
```

By default, the `allow_binary` option is disabled.

## Configuration

[Bundler][] uses an unorthodox system when dealing with
[configuration options][]. The highest precedence is given to the config file,
and then to the environment variables. This is a current limitation of Bundler,
that we had to work around. We may drop the workaround if this ends up being
addressed in future Bundler releases.

The order of precedence for Bundler configuration options is as follows

1. Local config (`<project_root>/.bundle/config or $BUNDLE_APP_CONFIG/config`)
1. Environment variables (ENV)
1. Global config (`~/.bundle/config`)
1. Bundler default config

We set the following configuration options to ensure that the build process
works correctly

- BUNDLE_CACHE_PATH: "${output_dir}/deps/bundler"
- BUNDLE_DEPLOYMENT: "true"
- BUNDLE_NO_PRUNE: "true"
- BUNDLE_VERSION: "system"
- BUNDLE_ALLOW_OFFLINE_INSTALL: "true"
- BUNDLE_DISABLE_VERSION_CHECK: "true"

### BUNDLE_CACHE_PATH

The directory that Bundler will look into when installing gems.

### BUNDLE_DEPLOYMENT

Disallow changes to the **Gemfile**. When the **Gemfile** is changed and the
lockfile has not been updated, running Bundler commands will be blocked. More
importantly though, this makes Bundler comply with network isolated builds.
However, this setting has a user-side implication regarding their build recipes,
e.g. Dockerfiles[^1] and you may want to consider enforcing the installation
path for your app explicitly with [`BUNDLE_PATH`][]

### BUNDLE_NO_PRUNE

Leave outdated gems unpruned.

### BUNDLE_VERSION

The version of Bundler to use when running under the Bundler environment.

### BUNDLE_ALLOW_OFFLINE_INSTALL

Allow Bundler to use cached data when installing without network access.

### BUNDLE_DISABLE_VERSION_CHECK

Stop Bundler from checking if a newer Bundler version is available on rubygems.org.

> **NOTE**
>
> A prefetch could fail when Bundler versions differ between the build system
> and lockfile and when the former is outdated. Therefore we do not recommend
> using mismatching or outdated versions of Bundler in build systems as this
> might result in unexpected failures.*

To create the configuration file, run the following command.

```bash
hermeto inject-files --for-output-dir /tmp/hermeto-output hermeto-output
```

You should see a log message that the file was created successfully.
Lastly, you need to set the `BUNDLE_APP_CONFIG` environment variable to point
to the copied configuration file.

```bash
hermeto generate-env --output ./hermeto.env --for-output-dir /tmp/hermeto-output ./hermeto-output
```

```bash
# cat hermeto.env
export BUNDLE_APP_CONFIG=/tmp/hermeto-output/bundler/config_override
```

The generated environment file should be sourced before running any Bundler command.

### Limitations

Since the local configuration takes higher precedence than the environment
variables (except `BUNDLE_APP_CONFIG`), we copy the configuration file and
overwrite the environment variables above. Then, we change the
`BUNDLE_APP_CONFIG` environment variable to point to the new configuration file.

It should not affect the build process unless you have multiple packages in
your repository with different configuration settings. In that case, you may
have to adjust the build phase accordingly.

## Hermetic build

After using the `fetch-deps`, `inject-files`, and `generate-env` commands
to set up the directory, building the Dockerfile will produce a container with
the application fully compiled without any network access. The build will be
hermetic and reproducible.

```dockerfile
FROM docker.io/library/ruby:latest

WORKDIR /app

COPY Gemfile .
COPY Gemfile.lock .

...

RUN . /tmp/hermeto.env && bundle install

...
```

Assuming `hermeto-output` and `hermeto.env` are in the same directory as the
Dockerfile, build the image with the following command

```bash
podman build . \
  --volume "$(realpath ./hermeto-output)":/tmp/hermeto-output:Z \
  --volume "$(realpath ./hermeto.env)":/tmp/hermeto.env:Z \
  --network none \
  --tag my-ruby-app
```

## Unsupported features

- checksum validation (blocked by pending official support)
- downloading the Bundler version specified in the **Gemfile.lock**
- reporting development dependencies
- plugins

[^1] `BUNDLE_DEPLOYMENT` enforces [deployment mode][] which is essentially
vendoring your application and its dependencies. In other words, deployment will
install your application to a local `vendor/bundle` directory instead of using
the standard system-wide location. This is currently the only way of forcing
bundler to respect and use the offline package cache during hermetic builds.
Note that the deployment mode doesn't play nicely with other installation flags
and so trying to use `--local` with your `bundle install` command in your
Dockerfile won't take effect, consider `BUNDLE_PATH` instead.

[`BUNDLE_PATH`]: https://bundler.io/v2.5/man/bundle-config.1.html#LIST-OF-AVAILABLE-KEYS
[Bundler]: https://bundler.io
[configuration options]: https://bundler.io/v2.5/man/bundle-config.1.html#DESCRIPTION
[deployment mode]: https://www.bundler.cn/man/bundle-install.1.html#DEPLOYMENT-MODE
