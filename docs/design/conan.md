# [REJECTED] Adding Conan Support to Hermeto


This document captures the decision to not support [Conan][] in Hermeto.  This
decision is made basing on the fact that Conan allows and encourages arbitrary
code execution at effectively every step in dependencies processing.  The
document contains the necessary background that made Hermeto team strongly
decide against supporting it. While all limitations could be worked around
the amount of work necessary makes it unfeasible for the team. This decision
could be revisited in the future.


## Background

[Conan][] is one of many package managers for C++. At the time of writing
it is not abandoned and supports quite a few C++ libraries. Support for this
PM was  inquired about by users. Conan provides a lot of functionality out of the
box: all major platforms are supported, integration with multiple build
systems exists, can handle both pre-compiled binaries and build from source,
has an official [central repository][] for hosting community provided packages
as well as an option for hosting [private repositories][]. Conan is written in
Python and could be used both as a stand-alone tool and as a library (however
Python APIs are [considered experimental][]). Conan allows to use Python as
a dependencies specification language as well which creates ample opportunities
for arbitrary code execution during both collection and build time.

Conan is [available via PyPI][] or [as rpm][].

Technically Conan cannot download dependencies from anywhere except from a
repository, however it is possible to amend _conanfile.py_
in such a way that it would download a package from GitHub and would expose it
via a local repository.


## Specifying dependencies

Any project that uses Conan must define either _conanfile.txt_ or
_conanfile.py_.  The former one is an ini-like list of dependencies and
generators for config files for build systems in use, the latter one is a
full-blown Python script allowing users to dynamically modify dependencies (and
to execute arbitrary code).

_conanfile.py_ could be defined by both package consumer and by recipes for
building packages. Below are descriptions of the most relevant Conan package
files.

By default dependencies originate from [Conan Central][] which, in turn,
appears to [store recipes on Github][]. It is possible to use any instance of
Conan server given that it is set up either globally or passed as an argument
to Conan.


### conanfile.txt

_conanfile.txt_ provides a simpler interface to Conan while not allowing many
features like conditional requirements. It consists of several static sections.

<details>
  <summary>conanfile.txt static sections</summary>

  ```
  [requires] : a list of required packages specified in `<package-name>/<package-version>` format.
     Examples: 
          poco/1.9.4              -- single version
          poco/[>1.0 <2.0]        -- versions range
          zlib/1.2.13#revision1   -- specifies _recipe_ (i.e. conanfile.py) revision
          poco/[~1.2]             -- allows variations of all digits that could appear after the last one
          poco/[\^1.]             -- makes only the last digit variable
          otherlib/2.1@otheruser/testing  -- allows to specify a different channel for the same package
                                             with an addition of an extra tag to make it even more
                                             specific.
  
  [tool_requires] : List of tool requirements (executable tools) specifying the full reference.
      > In practice the [tool_requires] will be always installed (same as
      > [requires]) as installing from a conanfile.txt means that something is
      > going to be built.  Note however, that by default tool_requires live in
      > the “build” context, they cannot be libraries to built with, just
      > executable tools (From
      > https://docs.conan.io/2/reference/conanfile_txt.html)
      The tools could be cmake, different packages could request different
      versions of the same tool and these versions are not visible to other by
      default. (However this could be amended via conanfile.py or options)
  
  [test_requires] : similar to requires, but for testing.
  
  [generators] : List of build tools configuration generators to run before
      building this package (can pick from a predefined list).
  
  [options] : options for each of the packages
      Example:
      poco/\*:shared=True -- will make poco shared as well as all packages it
                             pulls in.
  
  [layout] : a choice box for one of two layouts (information about where to find
      sources and about various components of the package e.g. libs to link against,
      list of included path etc).
  ```

</details>


### conanfile.py

 > The conanfile.py is the recipe file of a package, responsible for defining
 > how to build it and consume it.

_conanfile.py_ is a Python script that governs package-related activities. In
Conan-speak it is called a "recipe". It allows for much more dynamic manipulations
of setting compared to _conanfile.txt_. Typically a recipe contains a single class
with multiple standard attributes and methods defined for fine-tuning package
creation, build process, testing and several others.  Since a recipe is a
Python module it can contain arbitrary code which could be [referenced by other
recipes][].  Moreover, a recipe could use an out of bound mechanism to reuse
code from another recipe, some artefacts shipped along with it or even add a
parent class to itself. This could be done by setting `python_requires` and
`python_requires_exntend` attributes. These attributes accept dependencies in
the same format as other requirements fields which effectively allows to have an
import from a version that will not be determined until during run time. It is
possible (but discouraged) to build complex recipe hierarchies.  Any piece of
code from a recipe defined as `python_requires` is accessible, including
variables and helper functions. This seems to be limited to recipes shared via
the central repository and at least does not allow to directly reference
arbitrary code -- one would need to publish it as a recipe first.

The recommended way of using a recipe class is to define several of methods
related to various aspects of producing a package: `build()`, `generate()`,
`requirements()` and others.  These methods are called when a user executes
corresponding CLI commands, sometimes at rather unexpected moments. For
instance, running `conan build . ` (a command which is supposed to build a
recipe) results in `requirements()` and `build()` invocation (which is quite
logical), and running `conan graph info .` (a command which is supposed to
report a dependency graph) results in the same two invocations.

There does not seem to be any provisions limiting what a piece of code could be
doing, invocation of external tooling appears to be encouraged.  Thus the only
defence against any tampering with packages would be a thorough audit of all
published packages.

Conan does not appear to validate recipe attributes opening up another
potential abuse vector.

Conan allows one to specify tools to use for building a package (e.g. a
specific version of cmake), such versions will be downloaded to the cache. It
also provides wrappers for invoking system package managers.  Some recipes rely
on using system package managers a lot (for example, xorg recipe does that).


### conandata.yml

A useful source of information containing URLs and hashes for package sources.

<details>
  <summary>sample conandata.yml</summary>

  ```yaml
  sources:
  "1.3.1":
    url:
      - "https://zlib.net/fossils/zlib-1.3.1.tar.gz"
      - "https://github.com/madler/zlib/releases/download/v1.3.1/zlib-1.3.1.tar.gz"
    sha256: "9a93b2b7dfdac77ceba5a558a580e74667dd6fede4585b91eefb60f03b72df23"
   patches:
  "1.3.1":
    - patch_file: "patches/1.3.1/0001-fix-cmake.patch"
      patch_description: "separate static/shared builds, disable debug suffix"
      patch_type: "conan"
  ```
</details>


It is [referenced in the latest Conan documentation][], however there is no
dedicated section for it in the docs anymore ([it was present in 1.xx docs][]).
At the time of writing the first package listed on Conan page [still contains
conandata.yml][]. Most packages in the official repository, in fact, contain it,
however since it is not mentioned directly anymore we should not rely
solely on its presence.

This file could also be used to specify patches which are to be applied to
sources. The patches are shipped along the recipe.

### conan.lock

Conan provides a mechanism for locking dependencies versions via a lock file.
A lock file contains dependencies pinned to specific recipe revision i.e. to a
hash of the recipe itself. An example of a lock file for a minimal project with
a dependency on just zlib could be found below

<details>
  <summary>sample conan.lock</summary>

  ```json
  {
    "version": "0.5",
    "requires": [
        "zlib/1.3.1#b8bc2603263cf7eccbd6e17e66b0ed76%1733936244.862"
    ],
    "build_requires": [],
    "python_requires": [],
    "config_requires": []
  }
  ```

</details>

Note, that while allowing to pin dependencies to certain versions a lock file
says nothing about which remote must be used for retrieving the recipes. At the
time of writing a [recommended
mechanism](https://github.com/conan-io/conan/issues/8318) for remote
distribution along with the code [is considered
experimental](https://docs.conan.io/2/reference/commands/config.html#conan-config-install-pkg).
It is possible to switch to a different remote manually.
At the time of writing none of the recipes in the central repository
contained lock files.  Note, that while pinning a recipe version a lock file
does not prevent arbitrary code execution, but merely point to a revision of
code that will be executed.


## Downloading dependencies

Conan is expected to download, build (if downloaded in sources form) and
install dependencies.  The first stop to look for a dependency is a local cache
-- a directory of rather complicated structure pointed to by `CONAN_HOME`
variable (defaults to $HOME/.conan2). The cache is not concurrent, thus it is
recommended to have different caches per task in an environment where multiple
Conan tasks coexist. Setting CONAN_HOME affects both download and build, thus
it can provide a convenient mechanism for sharing downloaded packages. If a
dependency is not found in local cache Conan would consult all specified
remotes. Unless stated otherwise Conan would prefer binary packages.

Conan provides  `download` subcommand which allows one to download just *one* dependency
without any respect to its dependencies or any other transient dependencies.
This command does just that -- downloads something. The powerhouse of Conan
is `install` command which would not only collect al requirements, but also build them
and install them in the local cache. It appears that unless a package was told to
be built sources will not be downloaded by install:

<details>
  <summary>Sample conan session</summary>

  ```
  # Directly requesting to install zlib of specific version.
  # Since it is not present in a lock file that happens to be present in
  # the same directory  I override it
  $ conan install --requires=zlib/1.2.13 --lockfile=""
  ...
  ERROR: Missing prebuilt package for 'zlib/1.2.13'. ...
  ...
  $ echo $?
  1
  $ ls $CONAN_HOME
  extensions  global.conf  migrations  p  profiles  remotes.json  settings.yml  version.txt
  $ ls $CONAN_HOME/p/
  cache.sqlite3      zlib4f058de3f8852/
  $ ls $CONAN_HOME/p/zlib4f058de3f8852/
  d  e
  $ ls $CONAN_HOME/p/zlib4f058de3f8852/d
  conandata.yml  conan_export.tgz
  $ ls $CONAN_HOME/p/zlib4f058de3f8852/e
  conandata.yml  conanfile.py  conanmanifest.txt
  # Now let's see what changes when I add build missing:
  $ conan install --requires=zlib/1.2.13 --lockfile="" --build=missing
  ...
  Install finished successfully
  $ echo $?
  0
  $ ls $CONAN_HOME/p
  b  cache.sqlite3  zlib4f058de3f8852
  $ ls $CONAN_HOME/p/b
  zlib3de6e91b5a12b
  $ ls $CONAN_HOME/p/b/zlib3de6e91b5a12b/
  b  d  p
  $ ls $CONAN_HOME/p/b/zlib3de6e91b5a12b/b
  build  conaninfo.txt  patches  src
  $ ls $CONAN_HOME/p/b/zlib3de6e91b5a12b/b/build/
  Release
  $ ls $CONAN_HOME/p/b/zlib3de6e91b5a12b/b/build/Release/
  CMakeCache.txt  cmake_install.cmake  generators            libz.a    zconf.h
  CMakeFiles      CTestTestfile.cmake  install_manifest.txt  Makefile  zlib.pc
  $ ls $CONAN_HOME/p/b/zlib3de6e91b5a12b/d
  metadata
  $ ls $CONAN_HOME/p/b/zlib3de6e91b5a12b/d/metadata/
  $ ls $CONAN_HOME/p/b/zlib3de6e91b5a12b/p
  conaninfo.txt  conanmanifest.txt  include  lib  licenses
  $ ls $CONAN_HOME/p/b/zlib3de6e91b5a12b/p/lib
  libz.a
  $ ls $CONAN_HOME/p/b/zlib3de6e91b5a12b/p/include/
  zconf.h  zlib.h
  # Sources are in:
  $ ls $CONAN_HOME/p/zlib4f058de3f8852/s
  patches  src
  $ ls $CONAN_HOME/p/zlib4f058de3f8852/s/src
  adler32.c       deflate.c  gzwrite.c   inftrees.h    qnx            zconf.h          zlib.pc.in
  amiga           deflate.h  INDEX       LICENSE       README         zconf.h.cmakein  zutil.c
  ...
  ```
</details>

One possible solution to the problem of a build that happens during an install
could be monkey-patching of `build()` method in any recipe that is to be
downloaded.  The question of how to do this efficiently remains open, also some
recipes download their sources as part of `build()`.

The process of downloading sources for a recipe is supposed to be controlled by
`source()` method and a corresponding subcommand. Judging from current state of
Conan recipes on Github (1849 entires) all recipes have conanfile.py and most
recipes define a separate `source()` method, just 29 do not do that. For example cmake
just downloads the sources during a build.

On the other hand 1839 of 1849 contain `conandata.yml` which could be used for
collecting and injecting sources into a cache. A notable exception is `xorg`
which apparently contains just a list of libraries to be installed with a
package manager (i.e. without usage of any Conan-native tooling).

`download` does not seem to be doing anything helpful at all: it downloads just
a binary form without any transitive dependencies. There does not seem to be a
way to make it download sources as well.

  ```bash
    $ conan download -r=conancenter zlib/1.2.13
    Connecting to remote 'conancenter' anonymously
    Skip recipe zlib/1.2.13#9df41c65e2c2b6ef47633dc32e0b699a download, already in cache
    Downloading package 'zlib/1.2.13#9df41c65e2c2b6ef47633dc32e0b699a:41ad450120fdab2266b1185a967d298f7ae5259
    5#58e0515ae415ebd08d63ea0db6fd1761'
    ...
    $ ls $CONAN_HOME/p
    cache.sqlite3      zlib53546468985f4  zlib719177b87d205  zliba265d66faee39
    zlib17bfee322e8d5  zlib561027700ae6e  zlib92b0cd6d35aaa  zlibc6a4b1fe33208
    zlib4f058de3f8852  zlib5e2678cdd172f  zlib94fda36d28e71  zlibf12c65c363f25
    $ ls $CONAN_HOME/p/zlib17bfee322e8d5/
    d/ p/
    $ ls $CONAN_HOME/p/zlib17bfee322e8d5/p
    conaninfo.txt  conanmanifest.txt  include  lib  license
  ```

There are very few environment variables which affect Conan, the one useful is
CONAN_HOME. All other settings should be done via profile or global.conf residing
in CONAN_HOME.

Another option that is present in Conan, but which does not seem to be used in the
wild is package lock. A package could be locked with `conan lock create <path>`.
That would create a lock file similar to one below:

```
{
    "version": "0.5",
    "requires": [
        "fmt/11.2.0#579bb2cdf4a7607621beea4eb4651e0f%1746298708.362"
    ],
    "build_requires": [],
    "python_requires": [],
    "config_requires": []
}
```

However, none of upstream packages seem to contain lock files, moreover it is
possible to specify a different lock file or to just ignore lock file
completely by providing an empty string for path when building a package.

Conan also defines vendoring dependencies for packages, in Conan this means
hiding dependencies from package consumers and making packages binary-only. The
other thing to  mention about vendoring is that at the time of writing it is
considered unstable and experimental.

Finally a build could be made hermetic by forbidding using remotes with `-nr` flag.
It will fail if it does not find sources or binaries locally.


## Other features

### SBOMs

Conan is capable of producing some SBOMs, however their usability out of the
box is questionable.

### Workspaces

[Conan workspaces][] is an experimental feature at the time of writing seeking
volunteers to test it.


## Proposed solution

Conan could be used as a library or as a binary. While it is lucrative to use
it as a library I think we should not do that to ensure isolation between
Hermeto and recipes which could potentially contain anything and would end up
being executed effectively from within Hermeto.  While this won't eliminate the
threat it at least would not also break Hermeto too easily.

The most universal option for getting everything seems to be to let Conan
build  everything non-hermetically first, then purge resulting binaries. This addresses
the permitted variety of specifying dependencies and build dependencies. This seems
to be the most feasible option since `build()` will be run on multiple occasions.

An alternative to that would be to extensively monkey-patch Conan to cleanly separate
fetch and build phases and to convert system package manager invocations to
a request to RPM PM.

Processing of intermediate results could be done either by converting them to
json and then reusing Conan tools or, likely, by just reimplementing the
relevant parts (I estimate that an effort to learn the interface and to work
around its peculiarities would require same time as to implement necessary
handlers ourselves).


[Conan]: https://conan.io
[central repository]: https://conan.io/center
[private repositories]: https://docs.conan.io/2/tutorial/conan_repositories/setting_up_conan_remotes/artifactory/artifactory_ce_cpp.html
[considered experimental]: https://docs.conan.io/2/reference/extensions/python_api.html
[referenced by other recipes]: https://docs.conan.io/2/reference/extensions/python_requires.html
[available via PyPI]: https://pypi.org/project/conan/
[as rpm]: https://rpmfind.net/linux/rpm2html/search.php?query=conan
[Conan workspaces]: https://docs.conan.io/2/reference/workspace_files.html
[referenced in the latest Conan documentation]: https://docs.conan.io/2/tutorial/creating_packages/handle_sources_in_packages.html#using-the-conandata-yml-file
[it was present in 1.xx docs]: https://docs.conan.io/en/1.66/reference/config_files/conandata.yml.html
[still contains conandata.yml]: https://github.com/conan-io/conan-center-index/blob/19fef8334e091b857e99cb5261a61fdee82ea430/recipes/zlib/all/conandata.yml
[Conan Central]: https://conan.io/center
[store recipes on Github]: https://github.com/conan-io/conan-center-index/tree/master/recipes
