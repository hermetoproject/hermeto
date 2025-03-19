# Problem Statement

In February 2021, a [blog post](https://medium.com/@alex.birsan/dependency-confusion-4a5d60fec610)
popularized a novel way to inject malicious content into applications. The question was raised as to
whether using Hermeto makes this issue worse. This document is an analysis of each package manager
supported by Hermeto to address the security concern named *dependency confusion*.

# Dependency Confusion

To paraphrase the meaning of
*[dependency confusion](https://medium.com/@alex.birsan/dependency-confusion-4a5d60fec610)*:
a package manager is tricked into installing a dependency from an official, public, repository when
it should’ve installed it from a custom, often private, repository. For example, a company may host
its own internal npm registry which hosts internal-only packages. Then, someone on the Internet
uploads a package of the same name to the official npm repository. When the application is
installed, the package manager chooses the dependency package from the public repository instead
of the internal one.

## Potential Impact

What happens when Hermeto is tricked into downloading a malicious version of one of your dependencies?
To the system that runs Hermeto, nothing. Hermeto never executes your code, or the code of any of your
dependencies. What about your build?

Hopefully, you are building your application in an isolated environment (such as a container build
with no network access) to protect your own system. In the best case scenario, the malicious behaviour
will manifest at build time and the build will fail. In the worst case scenario, the build will succeed
but the built product will contain malicious code, which may then affect whoever tries to run it.

## Dependency Confusion vs Hermeto

Before delving into the specifics of each supported package manager, let us make a general statement
about Hermeto’s stance towards dependency confusion.

Hermeto aims to match the behaviour of your package manager as closely as possible. Generally
speaking, Hermeto is as vulnerable to dependency confusion as your package manager, not more, not
less. However, all the supported package managers provide means to protect yourself by following
best practices. For example, you can remove all known attack vectors by simply verifying the
checksums of your dependencies.

Hermeto enforces *some* of these best practices. Typically, each package manager provides a lockfile
or other mechanism for pinning dependency versions. Hermeto requires this. Verifying checksums is
usually up to you.

# Package Managers

## gomod

*TL;DR: commit your go.sum.*

Golang modules are defined by their [go.mod](https://golang.org/ref/mod#go-mod-file) files. These
define the name of your module as well as the names and versions of its dependencies. Thanks to
golang’s unique system of dependency management, this file is enough to ensure reproducible builds
on its own.

However, due to golang versions being tied to git tags, you are technically at the mercy of
repository maintainers. To prevent surprises, whenever go downloads any dependencies, it saves
their checksums in a [go.sum](https://golang.org/ref/mod#go-sum-files) file. This file is later used
to verify that the content of all direct and indirect dependencies is what you expect it to be.

As long as you make sure to always commit your go.sum file, your Golang module should be safe from
dependency attacks.

## pip

*TL;DR: if your package is Hermeto compliant, it is most likely safe. If you want to be sure, verify
checksums.*

Python packages can have various formats, which are specified across multiple PEPs. Pip supports
most of them. Hermeto support for pip, on the other hand, is quite simple. You must define all the
direct and indirect dependencies of your package in
[requirements.txt](https://pip.pypa.io/en/stable/user_guide/#requirements-files) style files and
tell Hermeto to process them.

Hermeto further restricts what you can put in your requirements.txt files. All of the dependencies
must be
[pinned](https://github.com/release-engineering/cachito/blob/master/docs/pip.md#pinning-versions)
to an exact version. Hermeto will refuse to process requirements files that use the
--extra-index-url option, which means that if a private registry is to be specified, only a single
one supported and the `--index-url` option should be used.

To protect yourself even further, use pip’s
[hash-checking mode](https://pip.pypa.io/en/stable/reference/pip_install/#hash-checking-mode). Note
that pip does not support hash checking for VCS dependencies, e.g. git. Consider transforming your
git dependencies to plain https dependencies. For example, if the repository is hosted on github,
you can use https://github.com/{org_name}/{repo_name}/{commit_id}.tar.gz to get the tarball for a
specific commit.

## npm

*TL;DR: do not use unofficial registries. Even if you try to do so via .npmrc, Hermeto will ignore
it.*

Npm packages follow the typical package file + lock file approach. The package file is
[package.json](https://docs.npmjs.com/cli/v6/configuring-npm/package-json), the lock file is
[package-lock.json](https://docs.npmjs.com/cli/v6/configuring-npm/package-lock-json) or
[npm-shrinkwrap.json](https://docs.npmjs.com/cli/v6/configuring-npm/shrinkwrap-json). Hermeto
requires the lock file.

The lock file pins all dependencies to exact versions. For https dependencies, which are impossible
to pin, Hermeto requires the integrity value (a checksum). For dependencies from the npm registry,
Hermeto does not require integrity values, but newer versions of npm will always include them. Check
if all your dependencies have an integrity value, update your lock file if not.

Do not try to use private registries. If you point npm to a private registry (or any registry other
than the official one) via [.npmrc](https://docs.npmjs.com/cli/v6/configuring-npm/npmrc), Hermeto
will ignore it and look in the official registry anyway.

## yarn

*TL;DR: same as npm, but the handling of unofficial registries is slightly less surprising. Still,
you probably should not use them.*

Yarn packages are identical to npm packages except that they use
[yarn.lock](https://classic.yarnpkg.com/en/docs/yarn-lock/) as the lock file. Everything that
applies to npm applies to yarn.

The one difference is the handling of unofficial registries. If you point yarn to an unofficial
registry via .npmrc or [.yarnrc](https://classic.yarnpkg.com/en/docs/yarnrc), this will be reflected
in the resolved url in the lock file. Hermeto will see that the url does not point to the official
registry and will treat the dependency as a plain https dependency. If the url is accessible to
Hermeto, it will download the dependency directly without relying on npm/yarn dependency resolution.
That does not necessarily make using unofficial registries a good idea. If the registry is private,
your build will either fail or leak internal package names.

## RubyGems (Bundler)

*TL;DR: allowing `https://rubygems.org` as the only source should mitigate the issue for GEM dependencies*

Hermeto parses `Gemfile.lock` which pins all dependencies to exact versions. Hermeto allows GEM dependencies
to be fetched only from `https://rubygems.org`, otherwise raises an error. GIT dependencies are specified using
a repo URL and pinned to a commit hash.

Bundler doesn't verify checksums of dependencies yet, however, there's an effort to bring
[internal GitLab implementation](https://gitlab.com/gitlab-org/gitlab/-/merge_requests/92633)
to [the upstream](https://github.com/rubygems/rubygems/pull/5808).
