# NuGet

## Overview

NuGet is the primary package manager for the .NET ecosystem.
It is most often used for C#, F# and Visual Basic.

NuGet package is a zipfile using a `.nupkg` extension, containing a `.nuspec` XML manifest and the package files themselves (mostly compiled .dll files).

See NuGet's [What is NuGet?][What is NuGet] for more information.

NuGet is heavily intertwined with MSBuild, a platform for building applications.
MSBuild provides an XML schema for a project file that controls how the build platform processes and builds software.
Project files conventionally use a file extension ending with `proj`, for example `.csproj` for C#.

See [MSBuild docs][MSBuild] for more information.

NuGet is also used as a base for other tools, especially on Windows.
Supporting these use cases however oftentimes requires additional non-trivial effort as can be demonstrated on the most
relevant tool using NuGet -- Chocolatey.

### Chocolatey

Chocolatey can be understood as the Windows equivalent of Linux distro package managers (e.g. `apt` of Debian-like distros or `dnf` of RHEL-based distros)
or macOS's homebrew.

Chocolatey packages are `.nupkg` archives that additionally contain PowerShell scripts used for installation (or removal) of Windows applications.

These PowerShell scripts are by definition arbitrary code executors and usually fetch arbitrary files used for the installation of the application.

See [Chocolatey docs][Chocolatey docs] for more information.

Chocolatey for Business handles the fetched files via a feature called [Package internalizer][Package internalizer] which
"is able to automatically download packages and resources, edit the scripts, and recompile packages to internalize and remove internet dependencies from those packages" (see [Package internalizer]).

Unfortunately, this is not supported in the FOSS edition of Chocolatey, making Chocolatey support in Hermeto significantly more difficult.

## Developer workflows

NuGet can either be used standalone or as part of the dotnet CLI.
While there can be some small differences, for purposes of Hermeto, they are pretty much the same and are interchangeable as long as NuGet and MSBuild versions are in sync.

For example `dotnet restore` is functionally equivalent to `nuget restore`.

Historically, there were various ways to declare packages that should be fetched.
The current way is via `PackageReference` items in project files.
As it has been supported since 2017 and is the only one that supports lockfiles, it should be the only format that Hermeto will support.

Note that "the `install` command doesn't modify the project file or packages.config file.
The `install` and `restore` commands only add packages to disk, but don't add dependencies to projects" (see [NuGet CLI]).

### Example .NET workflow

```shell
# Creating new project
dotnet new console --name App
cd App
```

In order to create a lockfile, following should be added to the project file:
```xml
<PropertyGroup>
  <RestorePackagesWithLockFile>true</RestorePackagesWithLockFile>
</PropertyGroup>
```

```shell
# Adding a package
dotnet add package Newtonsoft.Json --version 13.0.4

# Fetching project packages
# --locked-mode doesn't allow updating project lock file
dotnet restore --locked-mode

# Building the project
# --no-restore doesn't execute an implicit restore during build
dotnet build --no-restore
```

## Design considerations

### Dependency list generation

NuGet has optional native support for lockfile generation.
Hermeto should expect the lockfile to be provided.

#### Format

NuGet does not offer an official schema, nor much else, for its lockfile format.
The closest thing to a proper description is on NuGet's wiki, see [Repeatable build using lock file implementation][Lockfile format].

NuGet lockfile currently has three different versions which are all supported by NuGet.
- **Version 1** - default
- **Version 2** - Central Package Management is used
- **Version 3** - project has multiple target framework aliases that resolve to the same underlying NuGet framework
    - Note that Version 3 changes the format of the lockfile

See NuGet's implementation of [lockfile versioning][Lockfile versioning].

Any information about lockfile's format has to be gathered directly from [NuGet's code][Project Lockfile].

##### Example lockfile
```json
{
  "version": 1,
  "dependencies": {
    "net10.0": {
      "Newtonsoft.Json": {
        "type": "Direct",
        "requested": "[13.0.4, )",
        "resolved": "13.0.4",
        "contentHash": "pdgNNMai3zv51W5aq268sujXUyx7SNdE2bj1wZcWjAQrKMFZV260lbqYop1d2GM67JI1huLRwxo9ZqnfF/lC6A=="
      }
    }
  }
}
```

#### Dependency types

NuGet differentiates between four dependency types.

- **Direct** - directly referenced via `PackageReference`
- **CentralTransitive** - not referenced directly, but has a `PackageVersion` specified via central package management
- **Transitive** - any other transitive package
- **Project** - reference to another project in the same repository, not a downloaded package

For Hermeto's purposes, `CentralTransitive` and `Transitive` are pretty much equivalent.

This differentiation could be used in the future if dependency graphs were to be supported.
At the moment, all types (besides `Project`) should be fetched and their checksums checked the same.

#### Checksum generation

NuGet's checksum generation differs between unsigned and signed packages.
For Hermeto, the only relevant type of hash is `contentHash` included in the lockfile.

For unsigned packages, it is a simple base64-encoded sha512sum of the `.nupkg` file.

##### Signed packages

The `.nupkg` of signed packages additionally contains a `.signature.p7s` file.
However, this file has to be excluded for calculation of `contentHash`.
Unzipping and zipping the file again without the signature file unfortunately results in different checksums.

Therefore, the signature has to be "cut out" from the raw bytes of `.nupkg`.
See [NuGet's logic][Checksum logic] for reference.

### Package sources

NuGet gets packages from package sources configured in `nuget.config`, see [NuGet Config reference][NuGet Config].
The file name is `nuget.config`, but `NuGet.config` and `NuGet.Config` are also accepted.

A package source can be either local, simply pointing to a directory of `.nupkg` files, or remote.

Remote package sources are implemented as HTTP services following the [NuGet Server API][NuGet Server API], usually v3, sometimes the legacy v2.
This means the packages can be fetched from remote package sources via GET requests.

The default remote package source is `nuget.org`, but others, or even self-hosted ones such as via Sonatype Nexus, are also supported.

NuGet packages are mapped to the package sources via [Package Source Mappings][Package Source Mapping].

See [Consuming packages from authenticated feeds][Private package sources] for information about authentication to private package sources.

#### Example nuget.config

The example below is taken from NuGet's [Package Source Mapping][Package Source Mapping] documentation.

```xml
<?xml version="1.0" encoding="utf-8"?>
<configuration>
  <!-- Define the package sources, nuget.org and contoso.com. -->
  <!-- `clear` ensures no additional sources are inherited from another config file. -->
  <packageSources>
    <clear />
    <!-- `key` can be any identifier for your source. -->
    <add key="nuget.org" value="https://api.nuget.org/v3/index.json" />
    <add key="contoso.com" value="https://contoso.com/packages/" />
  </packageSources>
  
  <!-- Define mappings by adding package patterns beneath the target source. -->
  <!-- Contoso.* packages and NuGet.Common will be restored from contoso.com,
       everything else from nuget.org. -->
  <packageSourceMapping>
    <!-- key value for <packageSource> should match key values from <packageSources> element -->
    <packageSource key="nuget.org">
      <package pattern="*" />
    </packageSource>
    <packageSource key="contoso.com">
      <package pattern="Contoso.*" />
      <package pattern="NuGet.Common" />
    </packageSource>
  </packageSourceMapping>
</configuration>
```

### PURL

```text
pkg:nuget/<name>@<version>?<qualifiers>#<subpath>
```

- `name` corresponds to the package name in the lockfile used as key
- `version` corresponds to the `resolved` field

Packages of type `Project` should be excluded.

Furthermore, qualifier `?repository_url` should be added if the package is fetched from a different remote package source than `nuget.org`.

See [example lockfile](#example-lockfile).

See the [PURL Type Definition][NuGet PURL] for more details.

## Preliminary design

Hermeto should fetch NuGet packages itself instead of using NuGet tooling in order to avoid the possibility of arbitrary code execution via MSBuild.

### Fetching dependencies

#### Prerequisites

Hermeto should expect to be pointed to every project of the repository, e.g.:

```shell
hermeto fetch-deps '[
  {"type": "x-nuget", "path": "src/Foo"},
  {"type": "x-nuget", "path": "src/Bar"},
]'
```

> [!NOTE]
> This should in theory be possible to avoid through parsing of a solution file in the root directory of the repository.

Every project has its own lockfile and can have its own `nuget.config`.
If there is no `nuget.config` in the project, Hermeto should look higher.

#### Downloading and verifying packages

Hermeto has to figure out which package should come from which package source based on the package source mapping in `nuget.config`, see [Package sources](#package-sources) for more information.

Afterward, it should iterate over each package in the lockfile (except those of type `Project`, see [Dependency types](#dependency-types)) and do a GET request on the appropriate package source, fetching the `.nupkg` of the package.

If the package source is private, the GET request should use the proper authentication.

Once a package is fetched, its checksum should be compared with the `contentHash` from the lockfile, see [Checksum generation](#checksum-generation).

### Injecting dependencies

Hermeto should rewrite `nuget.config` files to make its output directory the only package source used for all packages.

> [!NOTE]
> "When `<clear />` is present for a given node, NuGet ignores previously defined configuration values for that node.", see [NuGet Config reference][NuGet Config].

```xml
<?xml version="1.0" encoding="utf-8"?>
<configuration>
  <packageSources>
    <clear />
    <add key="hermeto" value="${output_dir}/deps/nuget/" />
  </packageSources>
  <fallbackPackageFolders>
    <clear />
  </fallbackPackageFolders>
  <packageSourceMapping>
    <packageSource key="hermeto">
      <package pattern="*" />
    </packageSource>
  </packageSourceMapping>
</configuration>
```

See previous section [Package sources](#package-sources) for more information about package sources.

### Environment variables

No environment variables should be strictly required as rewriting the config files should do most of the work.

There might be some nice-to-have environment variables to set to avoid warnings associated with no network access,
but setting them should be left to the implementation.

## References

- [What is NuGet?][What is NuGet]
- [MSBuild][MSBuild]
- [Chocolatey docs][Chocolatey docs]
- [Package internalizer][Package internalizer]
- [NuGet CLI][NuGet CLI]
- [Repeatable build using lock file implementation][Lockfile format]
- [Lockfile versioning][Lockfile versioning]
- [Project Lockfile][Project Lockfile]
- [Checksum logic][Checksum logic]
- [NuGet Config reference][NuGet Config]
- [NuGet Server API][NuGet Server API]
- [Package Source Mapping][Package Source Mapping]
- [Consuming packages from authenticated feeds][Private package sources]
- [PURL Type Definition][NuGet PURL]

[What is NuGet]: https://learn.microsoft.com/en-us/nuget/what-is-nuget
[MSBuild]: https://learn.microsoft.com/en-us/visualstudio/msbuild/msbuild
[Chocolatey docs]: https://docs.chocolatey.org/en-us/
[Package internalizer]: https://docs.chocolatey.org/en-us/features/package-internalizer/
[NuGet CLI]: https://learn.microsoft.com/en-us/nuget/consume-packages/install-use-packages-nuget-cli
[Lockfile format]: https://github.com/NuGet/Home/wiki/Repeatable-build-using-lock-file-implementation#lock-file-format
[Lockfile versioning]: https://github.com/NuGet/NuGet.Client/blob/4c4510d29a64fcbcba6360e29c3a1e8288a87f9f/src/NuGet.Core/NuGet.Commands/PackagesLockFileBuilder.cs#L119-L133
[Project Lockfile]: https://github.com/NuGet/NuGet.Client/tree/4c4510d29a64fcbcba6360e29c3a1e8288a87f9f/src/NuGet.Core/NuGet.ProjectModel/ProjectLockFile
[Checksum logic]: https://github.com/NuGet/NuGet.Client/blob/4c4510d29a64fcbcba6360e29c3a1e8288a87f9f/src/NuGet.Core/NuGet.Packaging/Signing/Archive/SignedPackageArchiveUtility.cs#L564-L627
[NuGet Config]: https://learn.microsoft.com/en-us/nuget/reference/nuget-config-file
[NuGet Server API]: https://learn.microsoft.com/en-us/nuget/api/overview
[Package Source Mapping]: https://learn.microsoft.com/en-us/nuget/consume-packages/package-source-mapping
[Private package sources]: https://learn.microsoft.com/en-us/nuget/consume-packages/consuming-packages-authenticated-feeds
[NuGet PURL]: https://github.com/package-url/purl-spec/blob/89a0432cc325e422dbd8b9ba6ee0931f2b7db8d0/docs/types/definitions/nuget-definition.md