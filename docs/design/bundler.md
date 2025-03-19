# Design document for Bundler package manager

Contents:

1. [Ruby ecosystem overview](#i-ruby-ecosystem-overview)
2. [Design for the Hermeto implementation](#ii-design-for-the-implementation-in-hermeto)

## I. Ruby ecosystem overview

### Development prerequisites
In order to execute the commands in the examples below, make sure you have the following packages installed in your
environment:

```bash
sudo dnf install rubygems rubygems-bundler
```

Or use the official Ruby image from Docker hub:
```bash
podman run --rm -it docker.io/library/ruby:3.3.3 bash
```

### Project structure
```bash
bundle init # creates Gemfile in the current directory
bundle lock # creates Gemfile.lock in the current directory
```

```bash
├── .bundle
│   └── config
├── Gemfile
├── Gemfile.lock
├── vendor/cache
```

### Glossary
- **Gemfile**: A file that specifies the gems that your project depends on and their versions. Bundler uses this file
to install the correct versions of gems for your project.

  ```ruby
  source "https://rubygems.org"

  gem "rails", "= 6.1.7"
  ```

- **Gemfile.lock**: A file that locks the versions of gems that are installed for your project. Bundler uses this file
to ensure that the correct versions of gems are installed consistently across different environments. The native
parsing of this file is done by the
[LockfileParser class](https://github.com/rubygems/rubygems/blob/master/bundler/lib/bundler/lockfile_parser.rb).

- **RubyGems**: General package manager for Ruby. Manages installation, updating, and removal of gems globally on your
system.

  ```bash
  gem --help
  ```

- **Bundler**: Dependency management tool for Ruby projects.
Ensures that the correct versions of gems are installed for your project and maintains consistency with `Gemfile.lock`.

  ```bash
  bundler --help
  ```

- **Gem**: A package that can be installed and managed by Rubygems. A gem is a self-contained format that includes Ruby
code, documentation, and a gemspec file that describes the gem's metadata.

- **{gem}.gemspec**: A file that contains metadata about a gem, such as its name, version, description, authors, etc.
RubyGems uses it to install, update, and uninstall gems.

  ```ruby
  Gem::Specification.new do |spec|
    spec.name        = "example"
    spec.version     = "0.1.0"
    spec.authors     = ["Nobody"]
    spec.email       = ["ruby@example.com"]
    spec.summary     = "Write a short summary, because RubyGems requires one."
  end
  ```

### Dependency types
There are four types of
[sources](https://github.com/rubygems/rubygems/blob/master/bundler/lib/bundler/lockfile_parser.rb#L48) for dependencies
in the `Gemfile.lock` file:

#### Gem dependencies
Regular gem dependencies are located at the source URL, in our case, always <https://rubygems.org>. Each gem can be
accessed by its name and version - rubygems.org/gems/`<name>`-`<version>`.gem

Example of a gem dependency in the `Gemfile.lock` file:

```bash
GEM
 remote: https://rubygems.org/
 specs:
  rails (6.1.4)
    # transitive dependencies
    actioncable (= 6.1.4)
    actionmailbox (= 6.1.4)
    actionmailer (= 6.1.4)
    actionpack (= 6.1.4)
    actiontext (= 6.1.4)
    actionview (= 6.1.4)
    activejob (= 6.1.4)
    activemodel (= 6.1.4)
    activerecord (= 6.1.4)
    activestorage (= 6.1.4)
    activesupport (= 6.1.4)
    bundler (>= 1.15.0)
    railties (= 6.1.4)
    sprockets-rails (>= 2.0.0)
```

#### Git dependencies
Example of a Git dependency in the `Gemfile.lock` file:

```
GIT
  remote: https://github.com/porta.git
  revision: 779beabd653afcd03c4468e0a69dc043f3bbb748
  branch: main
  specs:
    porta (2.14.1)
```

#### Path dependencies
Example of a path dependency in the `Gemfile.lock` file:

```
PATH
  remote: some/pathgem
  specs:
    pathgem (0.1.0)
```

All path dependencies must be in the project directory. Bundler
[does not copy](https://github.com/rubygems/rubygems/blob/master/bundler/lib/bundler/source/path.rb#L83) those
dependencies that are already within the root directory of the project.

#### Plugins
Installing a plugin, even when on a folder that is a Bundler project, doesn't seem to affect the `Gemfile.lock`. The
plugin seems to be installed by default in the `$PWD/.bundle/`. The `Gemfile.lock` does have a section for plugins,
though, so further investigation would be needed. This initial investigation was done with the plugins listed under
[Known Plugins](https://bundler.io/guides/plugins.html).

*Don't confuse Bundler plugins with [RubyGems plugins](https://guides.rubygems.org/plugins/). The latter are meant to
extend the functionality of `gem` itself, and don't seem to have any impact on Bundler directly.*


### Platforms
Some gems may contain pre-compiled binaries that provide native extensions to the Ruby package. Any gem declared in the
`Gemfile` can be limited to specific
[platforms](https://bundler.io/v2.5/man/gemfile.5.html#PLATFORMS), making Bundler ignore it in case the project is
being built on a non-matching platform:

```ruby
gem "nokogiri",   platforms: [:windows_31, :jruby]
```

Here's an example of how a the `PLATFORM` section looks like in the `Gemfile.lock`:

```
PLATFORMS
  arm64-darwin-20
  arm64-darwin-21
  arm64-darwin-22
  ruby
  x86_64-darwin-18
  x86_64-darwin-20
  x86_64-darwin-21
  x86_64-darwin-22
  x86_64-linux
```

In case a user wants to force all the binaries to be compiled from source, the `BUNDLE_FORCE_RUBY_PLATFORM` environment
variable can be used.

### Dev dependencies
When adding a Gem into a Gemfile, the user might opt to nest them under a specific
[group](https://bundler.io/guides/groups.html). The name of the group can be any string, but the usual groups tend to
be common labels such as `:test`, `:development` or `:production`.

Here's how it looks like in a `Gemfile`:

```ruby
# :default group
gem 'nokogiri'

group :test do
  gem 'faker'
  gem 'rspec'
end
```

Another way to declare a dependency in the `:development` group is to
[add it](https://guides.rubygems.org/specification-reference/#add_development_dependency) to the `Gem::Specification`,
which is usually declared in the `.gemspec` file. This means we can safely assume that all dependencies under
`:development` are dev dependencies.

### Dependency checksums
The support for checksums in the `Gemfile.lock` is still in development, and currently is an
[opt-in feature](https://github.com/rubygems/rubygems/pull/7217). To enable it, we need to manually add a `CHECKSUMS`
section in the `Gemfile.lock`:

```shell
# manually add `CHECKSUMS` section somewhere in the Gemfile.lock
vim Gemfile.lock
# install any gem
bundle add rails --version "6.1.7"
# check the Gemfile.lock /o\
cat Gemfile.lock
```

Example of a checksum section in the `Gemfile.lock`:

```
CHECKSUMS
  actioncable (6.1.7) sha256=ee5345e1ac0a9ec24af8d21d46d6e8d85dd76b28b14ab60929c2da3e7d5bfe64
  actionmailbox (6.1.7) sha256=c4364381e724b39eee3381e6eb3fdc80f121ac9a53dea3fd9ef687a9040b8a08
  actionmailer (6.1.7) sha256=5561c298a13e6d43eb71098be366f59be51470358e6e6e49ebaaf43502906fa4
  actionpack (6.1.7) sha256=3a8580e3721757371328906f953b332d5c95bd56a1e4f344b3fee5d55dc1cf37
  actiontext (6.1.7) sha256=c5d3af4168619923d0ff661207215face3e03f7a04c083b5d347f190f639798e
  actionview (6.1.7) sha256=c166e890d2933ffbb6eb2a2eac1b54f03890e33b8b7269503af848db88afc8d4
```

This feature is available since Bundler [v2.5.0](https://github.com/rubygems/rubygems/blob/master/bundler/lib/bundler/lockfile_parser.rb#L55),
from this [PR](https://github.com/rubygems/rubygems/pull/6374) being merged on Oct 21, 2023.

## II. Design for the implementation in Hermeto

### Prefetching

Running a bundler command to fetch the dependencies always executes the `Gemfile`, which is arbitrary Ruby code.
Executing arbitrary code is a security risk and makes it impossible to assert that the resulting SBOM is accurate
(since any random package can be fetched from the Internet during the prefetch). This means that we need to implement
custom code to fetch the dependencies.

In the `Gemfile.lock`, all Gems that come from the same remote URL are grouped under the same block:
```
GEM
 remote: https://rubygems.org/
 specs:
  rails (6.1.4)
  json-schema (1.2.1)
```

A Gem can be fetched from its original location by using the following template:
```ruby
"https://#{remote}/gems/#{name}-#{version}.gem"
```

We should also leverage the existing code used to perform parallel downloads based on `asyncio` to download the necessary
Gems from the internet.

#### Output folder structure

Bundler has a built-in feature to cache all dependencies locally. This is done with the `bundle cache --all` command or
`bundle package --all` alias. In order to make bundler use the prefetched dependencies during the build, Hermeto needs
to recreate the exact same folder structure as bundler does.

Here's an example of how the output folder should look like:

```bash
$ ls vendor/cache

actioncable-6.1.7.gem
date-3.3.4.gem
json-schema-26487618a684
nokogiri-1.16.6.gem
tzinfo-2.0.6.gem
```

Notice that all the `.gem` dependencies are kept in their original format, and Git dependencies are just plain clones
of the repository placed in a folder. For Git dependencies, the folder name must match this specific
[format](https://github.com/rubygems/rubygems/blob/3da9b1dda0824d1d770780352bb1d3f287cb2df5/bundler/lib/bundler/source/git.rb#L130):

```ruby
"#{base_name}-#{shortref_for_path(revision)}"
```

The name of the directory **must come from the Git URL**, not the actual name of the gem, and the cloned folder must
contain unpacked source code. Any other format will cause bundler to try to re-download the repository, causing the
build to fail.

##### Multiple Gems in a single repository

A single repository can hold multiple Gems, and those can be imported as dependencies. When this happens, Bundler still
expects a single clone to be made. Here's an example of how multiple gems imported from a single repository+revision
looks like in the `Gemfile.lock`:

```
GIT
  remote: https://github.com/chatwoot/azure-storage-ruby
  revision: 9957cf899d33a285b5dfe15bdb875292398e392b
  branch: chatwoot
  specs:
    azure-storage-blob (2.0.3)
      azure-storage-common (~> 2.0)
      nokogiri (~> 1, >= 1.10.8)
    azure-storage-common (2.0.4)
      faraday (~> 2.0)
      faraday-follow_redirects (~> 0.3.0)
      faraday-net_http_persistent (~> 2.0)
      net-http-persistent (~> 4.0)
      nokogiri (~> 1, >= 1.10.8)
```

#### Platform (i.e. pre-compiled) gems
By default, binary gems are ignored for similar reasons as with [pip
wheels](https://github.com/hermetoproject/hermeto/blob/main/docs/pip.md#distribution-formats),
i.e. lacking sources which report in the SBOM.
Platforms that relate to specific architectures will contain
binaries that were pre-compiled for that architecture (see [Platforms](#platforms)).

The URL schema in the default rubygems registry seems to follow this format:

```ruby
# Plain Ruby Gem
"https://rubygems.org/gems/#{name}-#{version}.gem"

# Platform-specific Gem
"https://rubygems.org/gems/#{name}-#{version}-#{platform}.gem"
```

In order to support platform gems we'll add the `allow_binary` input JSON CLI option with almost
identical behaviour as with our pip backend.

### Out of scope

#### Plugins
Bundler has support for using [plugins](https://bundler.io/guides/bundler_plugins.html), which allows users to extend
Bundler's functionality in any way that they seem fit. Since this can open the possibility for security issues, plugins
will not be supported by Hermeto.

Since we're not proposing the direct usage of Bundler to fetch the dependencies, no other actions are needed in the
prefetch phase, existing plugin definitions will be ignored.

#### Checksum verification
Since checksums in the `Gemfile.lock` is still a feature in development (see [checksums](#dependency-checksums)), we
can postpone implementing support for it until the feature is delivered.

We need to decide if we will report all dependencies as having missing checksums in the SBOM, or not.

#### Dev dependencies
Bundler declares all dev dependencies under the `:development`
[group](#dependency-groups-or-how-bundler-deals-with-dev-dependencies). Unfortunately, groups declared in the `Gemfile`
are not reflected in the `Gemfile.lock`.

To implement proper reporting of dev dependencies, we'll very likely need to also parse the `Gemfile`. It can be done
as a follow-up if the need arises.

#### Prefetching Bundler
When running `bundle install`, Bundler will always try to fetch the exact version that is pinned in the `Gemfile.lock`
to perform the install. When doing an offline install from cache, a warning message is instead printed, but Bundler
will usually perform the install as expected.

To allow users to use the pinned version instead of only relying on the Bundler version present in the base image,
Hermeto could also prefetch the specific Bundler version needed for that project. This is easy to achieve, since Bundler
is treated as an ordinary Gem: https://rubygems.org/gems/bundler.

This feature, however, is out of scope for the initial implementation, and could be added if there's user demand for
it.

### Providing the content for the hermetic build

#### Setting the Bundler configuration

The order of precedence for Bundler configuration options is as follows:

1. Local config (`<project_root>/.bundle/config or $BUNDLE_APP_CONFIG/config`)
2. Environment variables (ENV)
3. Global config (`~/.bundle/config`)
4. Bundler default config

Since the local configuration takes higher precedence than the environment variables (except `BUNDLE_APP_CONFIG`), we
need to set the Bundler configuration options to make the build work.

In order to do this, we can either use `inject-files` to overwrite the `.bundle/config` directory in the source folder,
or use `BUNDLE_APP_CONFIG` to point Bundler to a config directory within the Hermeto output directory. The latter has
the benefit of not needing to dirty the cloned sources, but it wouldn't be able to support a multiple Ruby project per
repository scenario (since we would need to keep multiple configuration files).

#### Relevant configuration for the build

```
BUNDLE_CACHE_PATH=${output_dir}/deps/rubygems
BUNDLE_DEPLOYMENT=true
BUNDLE_NO_PRUNE=true
BUNDLE_ALLOW_OFFLINE_INSTALL=true
BUNDLE_DISABLE_VERSION_CHECK=true
BUNDLE_DISABLE_LOCAL_BRANCH_CHECK=true
BUNDLE_DISABLE_LOCAL_REVISION_CHECK=true
```

- **BUNDLE_CACHE_PATH**: The directory that Bundler will place cached gems in when running bundle package, and that
Bundler will look in when installing gems. Defaults to `vendor/cache`.

- **BUNDLE_DEPLOYMENT**: Disallow changes to the Gemfile. This also has the desired
side effect of forcing Bundler to use the local package cache instead of trying to reach out for
the Internet behaving similarly to `--local` flag to `bundle install` with the exception that it
can be enforced with a configuration key which the latter cannot
[yet](https://github.com/rubygems/rubygems/issues/8265). This in turn makes hermetic builds work.
There are some considerations and consequences when it comes to enforcing the deployment setting,
see [Offline installation using deployment mode](#offline-installation-using-deployment-mode).

- **BUNDLE_NO_PRUNE**: Whether Bundler should leave outdated gems unpruned when caching. Since we're potentially using
a single cache folder for multiple Gems ("input packages" in Hermeto's terms), we need to make sure that the first
install won't prune any cached dependencies that are unrelated to it.

- **BUNDLE_ALLOW_OFFLINE_INSTALL**: Explicitly allow bundler to use cached packages during offline
installs. Note this setting on its own is not enough to **force** bundler to make use of the
package cache.

- **BUNDLE_DISABLE_VERSION_CHECK**: Stop Bundler from checking if a newer Bundler version is
available on rubygems.org.

- **BUNDLE_DISABLE_LOCAL_REVISION_CHECK**: Allow Bundler to use a local git override without a
branch specified in the Gemfile

- **BUNDLE_DISABLE_LOCAL_BRANCH_CHECK**: Allow Bundler to use a local git override without checking
if the revision present in the lockfile is present in the repository.

- **BUNDLE_LOCAL__<GEM_NAME>**: Instead of checking out the remote git repository for GEM_NAME,
the local git directory override will be used. See below for more information on Bundler's git
dependency handling.

For more information, see Bundler's [documentation](https://bundler.io/v2.5/man/bundle-config.1.html).

#### Offline installation pitfalls

Bundler seems to be very opinionated when it comes to performing offline installations. This boils
down to mainly the following factors:
- involvement of git dependencies in the application build
- inability to enforce the `--local` flag for `bundle install` via configuration options making the
_deployment_ mode the only viable workaround

Both of the above are further described in a dedicated subsection below. The need for the deployment
mode comes from the fact that bundler doesn't pull Gem specification from the local cache by
default and so trying not to make use of the deployment mode, (also without using `--local` on the
user side) leads to errors similar to the following:
```
 bundler install --verbose
 Running `bundle install --verbose` with bundler 2.5.22
 Found no changes, using resolution from the lockfile
 ...
 The definition is missing ["racc-1.8.1", ...]
```

Attempts to trick bundler into getting the gem specification from the local offline cache instead
by trying to create a `specifications` directory next to the fetched gems with `GEM_PATH`,
populated with extracted `.gemspec` files can only be successful when platform packages (i.e.
without native extensions) aren't involved otherwise leading to errors like:

```
Running `bundle install --verbose` with bundler 2.5.22
Found no changes, using resolution from the lockfile Source locally installed gems is ignoring
#<Bundler::StubSpecification name=racc version=1.8.1 platform=ruby> because it is missing
extensions
```

This all means that hacky solutions aren't going to work around bundler and so
**until Bundler enables setting the [`--local`
flag](https://github.com/rubygems/rubygems/issues/8265) via configuration options, we need to keep
making use of the deployment mode.**

##### Offline installs involving git dependencies
Bundler seems to follow a different approach when it comes to git dependencies since in its default
configuration it always tries to fetch the source from the remote to ensure the application is built
against the correct branch/revision. This argument is indirectly supported by the
[docs](https://bundler.io/guides/deploying.html#deploying-your-application), more specifically:

> If you have run bundle pack, checked in the vendor/cache directory, and do not have any git gems,
Bundler will not contact the internet while installing your bundle.

This is a problem for hermetic builds and as such setting `BUNDLE_DEPLOYMENT` alone doesn't help
and we need more settings. In order to overcome this behavioral trait, we need to follow the
recommendation in the [config](https://bundler.io/v2.5/man/bundle-config.1.html#LOCAL-GIT-REPOS)
docs and override each git dependency with the location on the disk we fetched the git dependency
to and tell bundler about it with the `BUNDLE_LOCAL__<GEM_NAME>` configuration key.
However, this still isn't enough for Bundler to honour offline installs with git dependencies,
because then it's trying to enforce further checks as outlined in the
[docs](https://bundler.io/v2.5/man/bundle-config.1.html#LOCAL-GIT-REPOS):

>Bundler does many checks to ensure a developer won't work with invalid references. Particularly,
>we force a developer to specify a branch in the Gemfile in order to use this feature. If the
>branch specified in the Gemfile and the current branch in the local git repository do not
>match, Bundler will abort.

Therefore, we additionally need to enforce both `BUNDLE_DISABLE_LOCAL_BRANCH_CHECK` and
`BUNDLE_DISABLE_LOCAL_REVISION_CHECK`.

##### Offline installation using deployment mode
Deployment mode is a way of vendoring one's code along with the dependencies.
The most important bit about the deployment mode based on the official
[docs](https://www.bundler.cn/man/bundle-install.1.html#DEPLOYMENT-MODE)

>Gems are installed to vendor/bundle not your default system location.
>
>In development, it's convenient to share the gems used in your application with other applications
>and other scripts that run on the system.
>
>In deployment, isolation is a more important default. In addition, the user deploying the
>application may not have permission to install gems to the system, or the web server may not have
>permission to read them.
>As a result, bundle install --deployment installs gems to the vendor/bundle directory in the
>application. This may be overridden using the --path option.

is that this creates a local `vendor/bundle` directory local to the application repository instead
of using a system-wide location during the install. What that means in practice is that regardless
of the Rubygems ecosystem recommendations we're enforcing a particular way of installing
applications onto our whole user base. If they wish to override it, they need to make changes to
their build recipes and explicitly by setting the `BUNDLE_PATH` configuration option and point it
to a system location. That is official the only way to tell bundler to skip creating the local
`vendor/bundle` directory using the deployment mode.

### Generating the SBOM

#### Main package metadata

Ruby uses [Gem::Specification](https://guides.rubygems.org/specification-reference/) as a means of defining a Gem's
metadata, and it is usually defined in a `{gem-name}.gemspec` file. This file is not mandatory, though, and when it
exists, it needs to be explicitly imported in the `Gemfile`:

```
source "https://rubygems.org"

gemspec
```

When the `.gemspec` file exists and is properly imported, it will be listed in the `Gemfile.lock` as a `PATH`
dependency:

```
PATH
  remote: .
  specs:
    tmp (0.1.2)
```

Since the `remote` will always point to `.` in case of the main package [^main-package], we can safely use it to get
the `name` and `version` for the SBOM component. In case this block is absent, we will need to fallback to the
repository's remote `origin` to retrieve the main package's name, and leave the version empty, since it is not a
mandatory field.

[^main-package]: In Hermeto's terms, the **main package** is the path in the repository that is currently being
  processed.

#### PURLs

Also check the Ruby PURL [specification](https://github.com/package-url/purl-spec/blob/master/PURL-TYPES.rst#gem).

##### Standard Gem
```txt
pkg:gem/my-gem-name@0.1.1
```

##### Git dependency

```txt
pkg:gem/my-git-dependency?vcs_url=git%2Bhttps://github.com/my-org/mygem.git%26487618a68443e94d623bb585cb464b07d36702
```

The metadata for a Git dependency can be read from the `Gemfile.lock`:

```
GIT
  remote: https://github.com/my-org/mygem.git
  revision: 26487618a68443e94d623bb585cb464b07d36702
  specs:
    json-schema (3.0.0)
      addressable (>= 2.4)
```

##### Path dependency

```txt
pkg:gem/my-path-dependency?vcs_url=git%2Bhttps://github.com/my-org/mygem.git%40b6f47bd07e669c8d2eced8015c4bfb06db49949#subpath
```
Source code for "official" Bundler lockfile parsing in Ruby:
<https://github.com/rubygems/rubygems/blob/master/bundler/lib/bundler/lockfile_parser.rb>
```ruby
PATH
  remote: subpath
  specs:
    my-path-dependency (1.0.0)
```

### Summary

#### Implementation scoping
- design high-level code structure into multiple modules
- create a test repository that contains all the relevant use cases
- define models for Bundler as a new package manager
- parse all gems from `Gemfile.lock`
- implement metadata parsing for the "main package"
- download all gems from rubygems.org
- download all gems from Git repositories
- validate path dependencies are relative to the project root
- inject the Bundler configuration needed for the offline install
- generate PURLs for all dependencies
- add integration and e2e tests
- add documentation

#### Out of scope
- implement checksum parsing and validation when prefetching from the registry
- downloading the Bundler version specified in the `Gemfile.lock`
- support for pre-compiled binaries (platforms other than `ruby`)
- Gemfile.lock checksum validation (blocked by pending official support)
- reporting dev dependencies
- proper support for plugins

### References
This design doc was partially based on the original implementation done in [Cachito](https://github.com/containerbuildsystem/cachito/blob/master/cachito/workers/pkg_managers/rubygems.py). Since Hermeto has different design goals from Cachito, the implementation here will deviate from the original one, with a key difference being that Hermeto needs to provide a way to perform an [offline install](#providing-the-content-for-the-hermetic-build) from the local prefetched content.
