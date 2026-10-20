# NuGet

## Overview

NuGet is the primary package manager for the .NET ecosystem.
It is most often used for C#, F# and Visual Basic.

A [NuGet][What is NuGet] package is a zipfile using a `.nupkg` extension, containing a `.nuspec` XML manifest and the package files themselves (mostly compiled .dll files).
The packages are platform-independent.

NuGet is heavily intertwined with MSBuild, a platform for building applications.
MSBuild provides an XML schema for a project file that controls how the build platform processes and builds software and which dependencies it uses.
Project files conventionally use a file extension ending with `proj`, for example `.csproj` for C#.
When enabled, NuGet records dependencies with their versions and checksums in a lockfile, usually called `packages.lock.json`.

See [MSBuild docs][MSBuild] for more information.

NuGet is also used as a base for other tools, especially on Windows.
Supporting these use cases however oftentimes requires additional non-trivial effort as can be demonstrated on the most
relevant tool using NuGet -- Chocolatey.

### Chocolatey

Chocolatey can be understood as the Windows equivalent of Linux distro package manager or macOS's homebrew.

Chocolatey packages are `.nupkg` archives that additionally contain PowerShell scripts used for installation (or removal) of Windows applications.

These PowerShell scripts are by definition arbitrary code executors and usually fetch arbitrary files used for the installation of the application.

See [Chocolatey docs][Chocolatey docs] for more information.

Chocolatey for Business handles the fetched files via a feature called [Package internalizer][Package internalizer] which
"is able to automatically download packages and resources, edit the scripts, and recompile packages to internalize and remove internet dependencies from those packages" (see [Package internalizer]).

Unfortunately, this is not supported in the FOSS edition of Chocolatey, making full Chocolatey support in Hermeto significantly more difficult and out of scope for initial implementation of NuGet support.

## Developer workflows

NuGet can either be used standalone or as part of the dotnet CLI.
While there can be some small differences, they are similar enough for purposes of this doc.

Historically, there were various ways to declare dependencies.
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

This creates the following minimal project file:

<details>
  <summary>Click to expand</summary>

```xml
<Project Sdk="Microsoft.NET.Sdk">

  <PropertyGroup>
    <OutputType>Exe</OutputType>
    <TargetFramework>net10.0</TargetFramework>
    <ImplicitUsings>enable</ImplicitUsings>
    <Nullable>enable</Nullable>
  </PropertyGroup>

</Project>
```
</details>


In order to create a lockfile, following should be added to the `PropertyGroup` of the project file:
```xml
<PropertyGroup>
  <RestorePackagesWithLockFile>true</RestorePackagesWithLockFile>
</PropertyGroup>
```

This addition cannot be easily automated, but results in the lockfile being generated on any `dotnet` command relevant to package management (e.g. `add`, `restore`).

It is also possible to use `dotnet restore --use-lock-file` one-time which results in the lockfile being generated.
The lockfile then gets updated automatically.
However, in case of its deletion, it does not regenerate until another `dotnet restore --use-lock-file` invocation.

```shell
# Adding a package
dotnet package add Newtonsoft.Json --version 13.0.4

# Restoring project packages
#
# NuGet looks for dependencies defined in the project file and downloads them if necessary
#
# --locked-mode doesn't allow updating project lock file
dotnet restore --locked-mode

# Building the project
# --no-restore doesn't execute an implicit restore during build
dotnet build --no-restore
```
`dotnet` commands that manage packages change the underlying project file, in this case resulting in an `ItemGroup` being created with the new `PackageReference`:

<details>
  <summary>Click to expand</summary>

```xml
<Project Sdk="Microsoft.NET.Sdk">

  <PropertyGroup>
    <OutputType>Exe</OutputType>
    <TargetFramework>net10.0</TargetFramework>
    <ImplicitUsings>enable</ImplicitUsings>
    <Nullable>enable</Nullable>
    <RestorePackagesWithLockFile>true</RestorePackagesWithLockFile> <!-- Added manually -->
  </PropertyGroup>

  <ItemGroup>
    <PackageReference Include="Newtonsoft.Json" Version="13.0.4" />
  </ItemGroup>

</Project>
```
</details>

## Design considerations

### Dependency list generation

NuGet has optional native support for lockfile generation.
Hermeto should expect the lockfile to be provided by its users in their repositories.

#### Lockfile format

NuGet does not offer an official schema, nor much else, for its lockfile format.
The closest thing to a proper description is on NuGet's wiki, see [Repeatable build using lock file implementation][Lockfile format].

NuGet lockfile currently has three different versions.
All three versions are still in use and supported by NuGet and should thus all be supported by Hermeto.

The version 1 is the default choice.

In case Central Package Management (in short, setting versions in one place for all projects) is used, the lockfile gets changed to version 2 which supports a `CentralTransitive` dependency type explained further down in [Dependency types](#dependency-types).
For Hermeto, this does not matter much and versions 1 and 2 can be treated almost the same as version 1 is a subset of version 2.

However, if the project has multiple target framework aliases that resolve to the same underlying NuGet framework, it switches to version 3.
Version 3 is unfortunately a substantially different format that has to be treated differently from versions 1 and 2.

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

A package source can be either local or remote.

#### Local package sources

Local package sources are simply a hierarchical directory structure of `.nupkg` files.
For details, see [Local feeds][Local feeds].

They should only be supported if located inside the repository.

Afterward, their `contentHash` should be verified and copied into Hermeto's output directory.

In SBOM, they should use `vcs_url` qualifier.

### Remote package sources

Remote package sources are implemented as HTTP services following the [NuGet Server API][NuGet Server API], usually v3, sometimes the legacy v2.
V3 is used if the package source URL ends with `.json` or if it has `protocolVersion="3"` in `nuget.config`.

V3 package source points to its service index from which the package base address can be gathered.
Then the package can be fetched from it via GET requests.

Every other package source should be V2 and has to be parsed accordingly according to NuGet's implementation.

The default remote package source is `nuget.org`, but others, or even self-hosted ones such as via Sonatype Nexus, are also supported.

NuGet packages are mapped to the package sources via [Package Source Mappings][Package Source Mapping].

#### Private package sources

See [Consuming packages from authenticated feeds][Private package sources] for detailed information about authentication to private package sources.

> For HTTP feeds, NuGet will make an unauthenticated request, and if the server responds with an HTTP 401 response, NuGet will search for credentials in the following order:
> 1. An environment variable `NuGetPackageSourceCredentials_{name}`.
> 2. Credentials in `nuget.config` files.
> 3. Use a NuGet credential provider, if your package source provides one.

Hermeto should support authentication only through environment variables and `nuget.config` as credential providers provide additional complexity.

##### Environment variables

Environment variables are fairly straightforward, the `{name}` in `NuGetPackageSourceCredentials_{name}` is simply the key of the package source.

For example:
```xml
<packageSources>
    <add key="foo" value="https://foo.example/packages/" />
</packageSources>
```

Would have environment variable `NuGetPackageSourceCredentials_foo` with its value in the following format `Username={username};Password={password}`.

##### Credentials in nuget.config

Credentials can be configured via `<packageSourceCredentials>` in `nuget.config`.
Their values get substituted for environment variables via `%ENV_VAR_NAME%` syntax.

Note encrypted `Password` is supported only on Windows, so `ClearTextPassword` has to be used.

```xml
<packageSourceCredentials>
    <foo>
        <add key="Username" value="user" />
        <add key="ClearTextPassword" value="%FOO_PASSWORD%" />
    </foo>
</packageSourceCredentials>
```

#### Example nuget.config

The example below is taken from NuGet's [Package Source Mapping][Package Source Mapping] documentation.

The config defines two sources for project's packages, `nuget.org` and `contoso.com`.

Because of defined patterns in `<packageSourceMapping>`, packages with names starting with `Contoso.` as well as `NuGet.Common` will be sourced from `contoso.com`.
Since these patterns are more specific than catch-all `*` of `nuget.org`, only `contoso.com` will be tried for them even if it fails to resolve.

Every other package will be fetched from `nuget.org` due to its pattern `*`.

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
It should point to the NuGet [service-index URL][Service index].

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

However, `nuget.config` files inherit settings from `nuget.config` files higher up the directory structure until the root directory of the repository.
All nodes get merged unless `<clear />` is used in the node.
If the same key is set in multiple configs, use the closest one.
Note that `<clear />` simply gets rid of all previous elements in the node, so it should only ever be used as the first element of the node.
The `nuget.config` inheritance should be implemented in Hermeto as well with the path traversal ending early if `<clear />` is found for all wanted nodes.

In original implementation, `nuget.config` can also inherit from global configs, but this will not be supported by Hermeto.

#### Resolving package sources

Hermeto needs to figure out where to get dependencies from.

Since package sources are defined in `nuget.config`, if there is no `nuget.config` file found, only the default source `nuget.org` should be tried.

If only `<packageSources>` are set, each of them should be tried for each package until `contentHash` matches.

If `<packageSourceMapping>` is used, the declared patterns should be followed and only package sources with the closest matching patterns should be tried.
If no patterns match, the resolution should fail.

Note that it is possible to disable a package source via `<disabledPackageSources>`.

See [Package sources](#package-sources) for more information.

#### Downloading and verifying packages

Hermeto should iterate over each package in the lockfile (except those of type `Project`, see [Dependency types](#dependency-types)).
For remote dependencies, Hermeto should do a GET request on the appropriate package sources, fetching the `.nupkg` of the package.

If the package source is private, the GET request should use the proper authentication.

Once a package is fetched, its checksum should be compared with the `contentHash` from the lockfile, see [Checksum generation](#checksum-generation).
If the `contentHash` does not match, it should try all other resolved package sources, raising an exception if none of them match.

### Injecting dependencies

Hermeto should rewrite `nuget.config` files to make its output directory the only package source used for all packages.
The output directory should follow hierarchical pattern described in [Local package sources][Local feeds].

NuGet always extracts restored packages into a [global packages folder][Global packages folder], `~/.nuget/packages` by default and prefers packages already present there over querying package sources.
It cannot be disabled, so Hermeto should point it to an empty directory via `globalPackagesFolder` to ensure no packages are picked up from the build environment.

> [!NOTE]
> "When `<clear />` is present for a given node, NuGet ignores previously defined configuration values for that node.", see [NuGet Config reference][NuGet Config].

```xml
<?xml version="1.0" encoding="utf-8"?>
<configuration>
  <config>
    <add key="globalPackagesFolder" value="${output_dir}/deps/nuget-global-packages/" />
  </config>
  <packageSources>
    <clear />
    <add key="hermeto" value="${output_dir}/deps/nuget/" />
  </packageSources>
  <fallbackPackageFolders>
    <clear />
  </fallbackPackageFolders>
  <packageSourceMapping>
    <clear />
    <packageSource key="hermeto">
      <package pattern="*" />
    </packageSource>
  </packageSourceMapping>
  <auditSources>
    <clear />
  </auditSources>
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
- [Local feeds][Local feeds]
- [Global packages folder][Global packages folder]
- [NuGet Server API][NuGet Server API]
- [Package Source Mapping][Package Source Mapping]
- [Consuming packages from authenticated feeds][Private package sources]
- [Service index][Service index]
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
[Local feeds]: https://learn.microsoft.com/en-us/nuget/hosting-packages/local-feeds
[Global packages folder]: https://learn.microsoft.com/en-us/nuget/consume-packages/managing-the-global-packages-and-cache-folders
[NuGet Server API]: https://learn.microsoft.com/en-us/nuget/api/overview
[Package Source Mapping]: https://learn.microsoft.com/en-us/nuget/consume-packages/package-source-mapping
[Private package sources]: https://learn.microsoft.com/en-us/nuget/consume-packages/consuming-packages-authenticated-feeds
[Service index]: https://learn.microsoft.com/en-us/nuget/api/service-index
[NuGet PURL]: https://github.com/package-url/purl-spec/blob/89a0432cc325e422dbd8b9ba6ee0931f2b7db8d0/docs/types/definitions/nuget-definition.md
