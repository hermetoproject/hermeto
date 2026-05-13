# Gradle Design Document

## Overview

[Gradle](https://docs.gradle.org) is a build tool designed for multiple programming languages and
ecosystems. It was first built to support Java projects, but has since expanded to support other
JVM-based languages (Scala, Kotlin, Groovy script), JavaScript, and C++.

### Developer Workflow

Developers set up their projects with Gradle by adding the following to their source code:

- A `build.gradle` or `build.gradle.kts` file with the build definition, plugins to use, and dependencies.
- The [Gradle Wrapper](https://docs.gradle.org/current/userguide/gradle_wrapper.html#gradle_wrapper),
  which invokes the Gradle binary that is distributed alongside the source code. The wrapper
  consists of `gradlew`, `gradlew.bat`, `gradle/wrapper/gradle-wrapper.properties`, and
  `gradle/wrapper/gradle-wrapper.jar` — all of which are committed to source control.

Once configured, developers invoke the Gradle Wrapper and one or more tasks to complete:

```sh
./gradlew clean build
```

Developers harden the supply chain security of their Gradle projects through the following
procedure:

1. **Enable version locking**: there are two mechanisms that must be set to [lock versions](https://docs.gradle.org/current/userguide/dependency_locking.html)
   for dependencies as well as plugins.
   - `buildscript`: Set the configuration classpath to activate locking. This must be done
     after all `import` statements and before any `plugin` declaration in the `build.gradle[.kts]` file.
     Adding this block will lock versions for all plugins (and their dependencies).

     ```groovy
     buildscript {
         configurations.classpath {
             resolutionStrategy.activateDependencyLocking()
         }
     }

     plugins {
         ...
     }
     ```

    - `dependencyLocking`: Apply this before the `dependencies` declaration in the
      `build.[gradle|kts]` file.
    
    ```groovy
    dependencyLocking {
        lockAllConfigurations()
    }
    ```

2. **Enable version locking for shared build configuration**: if the Gradle project uses a shared
   [buildSrc](https://docs.gradle.org/current/userguide/sharing_build_logic_between_subprojects.html)
   directory, repeat step 1 for the `buildSrc/build.gradle[.kts]` file.

3. **Generate all lockfiles**:

   ```sh
   ./gradlew dependencies --write-locks
   ```

4. **Enable [dependency verification](https://docs.gradle.org/current/userguide/dependency_verification.html#sec:bootstrapping-verification)**:

  ```sh
  ./gradlew --write-verification-metadata sha256
  ```

With these steps completed, Hermeto can utilize the following file data to prefetch dependencies:

- `*.lockfile`: a flat list of dependencies by their identifying coordinate (usually Maven-style `groupId:artifactId:version`)
- `gradle/verification-metadata.xml`: an XML-formatted set of dependencies with their associated checksums (and/or pgp signatures)

### How the Package Manager Works

- **Registry/repository model**: Gradle resolves artifacts from
  [Maven-compatible repositories](https://docs.gradle.org/current/userguide/declaring_repositories.html)
  declared in `build.gradle[.kts]` via a `repositories {}` block. Built-in shorthand notations
  cover the most common public repositories:

  | Shorthand | URL |
  |-----------|-----|
  | `mavenCentral()` | `https://repo.maven.apache.org/maven2/` |
  | `google()` | `https://dl.google.com/dl/android/maven2/` |
  | `gradlePluginPortal()` | `https://plugins.gradle.org/m2/` |

  Plugin dependencies are resolved separately: the `plugins {}` block in `build.gradle[.kts]`
  fetches plugins from the [Gradle Plugin Portal](https://plugins.gradle.org) by default (a
  Maven-compatible repository), unless overridden via `pluginManagement {}` in
  `settings.gradle[.kts]`. Regular dependencies declared in `dependencies {}` use the
  `repositories {}` block.

- **Package identity and versioning**: Gradle uses Maven-style coordinates:
  `group:artifact:version` (e.g., `com.google.guava:guava:30.0-jre`). Plugins are additionally
  referenced by a plugin ID (e.g., `org.springframework.boot`) in the `plugins {}` block, which
  maps to a Maven artifact on the Plugin Portal. Versions are typically static strings; dynamic
  versions (e.g., `1.0.+`) are incompatible with dependency locking.

- **Dependency resolution**: Gradle resolves dependencies in two phases: a **graph resolution**
  phase that builds the full dependency graph from declarations and repository POM metadata, and an
  **artifact resolution** phase that downloads the actual files. Dependencies are grouped into
  [configurations](https://docs.gradle.org/current/userguide/dependency_configurations.html) (e.g.,
  `compileClasspath`, `runtimeClasspath`, `annotationProcessor`) that define which deps are used at
  which build phase.

- **Configuration options**: Gradle's behavior is controlled via:
  - `build.gradle[.kts]`: per-project build logic, dependencies, and repository declarations
  - `settings.gradle[.kts]`: multi-project structure, plugin management, and version catalogs
  - `gradle.properties`: project and JVM property key-value pairs
  - Environment variables: `GRADLE_USER_HOME`, `GRADLE_OPTS`, `JAVA_HOME`
  - Init scripts in `$GRADLE_USER_HOME/init.d/`: applied to all builds automatically

## Design

### Scope

**In scope**:

- Java, Kotlin, Groovy, and Scala projects using Gradle with Maven-compatible repositories
  (Maven Central, Gradle Plugin Portal, Google Maven, and custom Maven repos)
- Projects with dependency locking enabled (`gradle.lockfile`, `buildscript-gradle.lockfile`)
- Projects with dependency verification enabled (`gradle/verification-metadata.xml` with SHA-256
  or SHA-512 checksums)
- Single-project and multi-project builds

**Out of scope**:

- Projects using flat-directory dependencies (local filesystem artifacts not from a registry)
- Dynamic/snapshot versions (e.g., `1.0.+`, `latest.release`) — these are incompatible with
  dependency locking and cannot be prefetched deterministically
- Projects without dependency locking or dependency verification — Hermeto requires both files
- Ivy-format repositories
- The Gradle Wrapper distribution itself: Hermeto assumes Gradle is pre-installed in the build
  environment. The wrapper's `gradle-wrapper.properties` points to `services.gradle.org`, which
  requires network access; this is a known limitation (see [Current Limitations](#current-limitations))

**Edge cases**:

- Projects declaring custom Maven repositories beyond the built-in shorthands — Hermeto must
  discover and access these registries during prefetch
- PGP signature verification in `verification-metadata.xml` (`<verify-signatures>true</verify-signatures>`)
  — only checksum-based verification is implemented in the initial version

### Dependency List Generation

Hermeto uses two files produced by the supply chain hardening workflow described in
[Developer Workflow](#developer-workflow):

1. **Lockfiles** (`gradle.lockfile`, `buildscript-gradle.lockfile`): provide the resolved
   `group:artifact:version` coordinate for every dependency, scoped by configuration
2. **Verification metadata** (`gradle/verification-metadata.xml`): provides the artifact
   filenames (including non-standard classifiers) and SHA-256/SHA-512 checksums for every file
   to download

Both files are required. Lockfiles supply coordinates; verification metadata supplies the
concrete filenames and checksums Hermeto needs to download and validate each artifact.

In multi-project builds, each subproject has its own `gradle.lockfile` and
`buildscript-gradle.lockfile` at its root. The `gradle/verification-metadata.xml` is a single
file at the repository root, shared across all subprojects.

#### Dependency List Format

**Lockfile** (`gradle.lockfile` / `buildscript-gradle.lockfile`):

- **File format**: plain text
- **Required fields**: `group:artifact:version` coordinate; one or more configuration names
- **Example**:

  ```
  # This is a Gradle generated file for dependency locking.
  # Manual edits can break the build and are not advised.
  # This file is expected to be part of source control.
  org.springframework:spring-beans:5.0.5.RELEASE=compileClasspath,runtimeClasspath
  org.springframework:spring-core:5.0.5.RELEASE=compileClasspath,runtimeClasspath
  org.springframework:spring-jcl:5.0.5.RELEASE=compileClasspath,runtimeClasspath
  empty=annotationProcessor
  ```

  Lines beginning with `#` are comments. The `empty=<config>` sentinel marks configurations that
  have no dependencies. Entries are sorted alphabetically.

**Verification metadata** (`gradle/verification-metadata.xml`):

- **File format**: XML
- **Required fields**: `component` (group, name, version attributes), `artifact` (name attribute),
  and at least one checksum element (`sha256` or `sha512` with a `value` attribute)
- **Supported algorithms**: `md5`, `sha1`, `sha256`, `sha512`; Hermeto requires `sha256` or
  `sha512`
- **Example**:

  ```xml
  <?xml version="1.0" encoding="UTF-8"?>
  <verification-metadata>
     <configuration>
        <verify-metadata>true</verify-metadata>
        <verify-signatures>false</verify-signatures>
     </configuration>
     <components>
        <component group="com.google.guava" name="guava" version="30.0-jre">
           <artifact name="guava-30.0-jre.jar">
              <sha256 value="9a5e1a85d3b23e6c82e7d..." origin="Generated by Gradle"/>
           </artifact>
           <artifact name="guava-30.0-jre.pom">
              <sha256 value="3a8d2bca9f1e..." origin="Generated by Gradle"/>
           </artifact>
        </component>
     </components>
  </verification-metadata>
  ```

  When `<verify-metadata>true</verify-metadata>` is set, POM files also appear as `artifact`
  entries and must be downloaded and verified.

#### Checksum Generation

- **Native checksum support**: Yes — `gradle/verification-metadata.xml` is generated natively by
  Gradle via `./gradlew --write-verification-metadata sha256`
- **Checksum algorithms**: MD5, SHA-1, SHA-256, and SHA-512 are all supported. Hermeto requires
  at least one SHA-256 or SHA-512 entry per artifact.
- **Checksum sources**: Checksums are computed by Gradle locally at generation time and written
  with `origin="Generated by Gradle"`.
- **Missing checksum handling**: Artifacts without a SHA-256 or SHA-512 entry in
  `verification-metadata.xml` cannot be prefetched and Hermeto should reject the request with an
  actionable error message.

### Fetching Content

#### Native vs. Hermeto Fetch

Hermeto must fetch dependencies directly rather than delegating to Gradle. Gradle build scripts
are written in Groovy or Kotlin DSL — both are full programming languages capable of executing
arbitrary code during the dependency resolution phase. Plugins can likewise execute arbitrary code
once on the classpath. Hermeto cannot safely invoke Gradle to fetch dependencies on its behalf.

Instead, Hermeto parses the lockfiles and `verification-metadata.xml` to enumerate what to
download, then fetches each artifact directly from the declared Maven repositories using HTTP.

#### Wrapper JAR Validation

Before prefetching any dependencies, Hermeto validates the integrity of `gradle/wrapper/gradle-wrapper.jar`,
which is committed to source control. The wrapper JAR bootstraps the Gradle build process; a
compromised JAR could execute arbitrary code before any dependency resolution occurs.

The expected SHA-256 checksum is not stored in `gradle-wrapper.properties`. Instead, Hermeto
derives it from Gradle's release metadata API:

1. Read `gradle/wrapper/gradle-wrapper.properties` and extract the `distributionUrl` property.
2. Parse the Gradle version from the URL (e.g., `…/gradle-9.5.1-bin.zip` → `9.5.1`).
3. Fetch release metadata from `https://services.gradle.org/versions/all` (JSON array).
4. Find the entry matching the parsed version and read its `wrapperChecksum` field (SHA-256).
5. Compute the SHA-256 of the local `gradle/wrapper/gradle-wrapper.jar`.
6. Reject the request if the computed hash does not match `wrapperChecksum`.

If `gradle-wrapper.jar` is absent from the repository, Hermeto rejects the request with an error
directing the user to commit the file to source control.

#### Project Structure

**Developer's project**:

```
project.git/
├── gradle/
│   ├── libs.versions.toml           # optional: version catalog (Gradle 7.4+)
│   ├── verification-metadata.xml    # dependency checksums (required by Hermeto)
│   └── wrapper/
│       ├── gradle-wrapper.jar           # must be committed; checksum-validated by Hermeto
│       └── gradle-wrapper.properties    # contains distributionUrl (required by Hermeto)
├── gradlew                          # Unix wrapper script
├── gradlew.bat                      # Windows wrapper script
├── gradle.lockfile                  # resolved dependency versions (required by Hermeto)
├── buildscript-gradle.lockfile      # plugin dependency versions (required by Hermeto)
├── settings.gradle[.kts]            # project structure, pluginManagement
└── build.gradle[.kts]               # build logic, dependencies, repositories
```

For **multi-project builds**, each subproject has its own lockfiles:

```
project.git/
├── gradle/
│   ├── verification-metadata.xml    # shared across all subprojects
│   └── wrapper/...
├── gradlew
├── settings.gradle[.kts]            # declares all subprojects via include(...)
├── build.gradle[.kts]
├── gradle.lockfile                  # root-project lockfile
├── buildscript-gradle.lockfile
├── subproject-a/
│   ├── build.gradle[.kts]
│   ├── gradle.lockfile
│   └── buildscript-gradle.lockfile
└── subproject-b/
    ├── build.gradle[.kts]
    ├── gradle.lockfile
    └── buildscript-gradle.lockfile
```

**Hermeto output directory** — artifacts stored in Gradle dependency cache layout:

```
$output_dir/gradle-home/caches/modules-2/
├── files-2.1/
│   └── com.google.guava/           # groupId — dots preserved, NOT converted to slashes
│       └── guava/                  # artifactId
│           └── 30.0-jre/           # version
│               ├── <sha1-of-jar>/
│               │   └── guava-30.0-jre.jar
│               └── <sha1-of-pom>/
│                   └── guava-30.0-jre.pom
└── metadata-<version>/             # binary metadata store — required for --offline
    └── ...
```

The `files-2.1` path pattern is:

```
{groupId.with.dots}/{artifactId}/{version}/{sha1_of_file}/{filename}
```

The SHA-1 directory name is the SHA-1 hash of the file's byte content. Each artifact file
(JAR, POM, classifier JAR, etc.) receives its own hash-named subdirectory.

#### Dependency metadata "pre-warming"

Populating `files-2.1` alone is not sufficient for Gradle's `--offline` mode. Gradle uses a binary
metadata store (`metadata-<version>/`) to locate artifacts by coordinate; if it is absent, Gradle
cannot find files in `files-2.1` and the build fails. The `<version>` is specific to the version
of Gradle running with the wrapper.

Since all dependencies are version-locked and have relevant metadata checksums, Hermeto can
instruct Gradle to re-populate the metadata cache by generating the verification metadata in 
`--dry-run` mode:

```sh
./gradlew --write-verification-metadata sha256 --dry-run
```

This will generate the file `gradle/verification-metadata.dryrun.xml`, which can be discarded.


#### File Formats and Metadata

- **Package file formats**: JAR files (`.jar`), Maven POM metadata files (`.pom`), and
  classifier variants (e.g., `-sources.jar`, `-javadoc.jar`, `-tests.jar`)
- **Metadata requirements**: Each dependency's POM file must be downloaded alongside the JAR.
  Gradle reads POM files to resolve transitive dependency metadata and validate artifact integrity
  when `<verify-metadata>true</verify-metadata>` is set.
- **Hash subdirectory**: Every file stored in `files-2.1` must be placed inside a subdirectory
  named by the SHA-1 hash of its byte content. Hermeto computes this SHA-1 locally after
  downloading and verifying each artifact (the SHA-256 from `verification-metadata.xml` is used
  for download verification; SHA-1 is then computed separately for the cache directory name).
- **Naming conventions**: Gradle dependency cache layout (see above); note that `groupId` uses
  dots, not directory separators (`com.google.guava`, not `com/google/guava`)
- **Version handling**: Versions are static strings from the lockfile. Dynamic/snapshot versions
  are out of scope.

#### Network Requirements

- **Registry endpoints**:

  | Endpoint | URL | Purpose |
  |----------|-----|---------|
  | Maven Central | `https://repo.maven.apache.org/maven2/` | Library and plugin artifacts |
  | Gradle Plugin Portal | `https://plugins.gradle.org/m2/` | Plugin artifacts |
  | Google Maven | `https://dl.google.com/dl/android/maven2/` | Android artifacts |
  | Gradle release metadata | `https://services.gradle.org/versions/all` | Wrapper JAR checksums |
  | Custom repos | As declared in `build.gradle[.kts]` / `settings.gradle[.kts]` | Project-specific artifacts |

- **Repository discovery**: Hermeto parses `build.gradle[.kts]` and `settings.gradle[.kts]` to
  extract declared repository URLs, resolving built-in shorthands to their canonical URLs. The
  `pluginManagement { repositories {} }` block in `settings.gradle[.kts]` governs plugin
  repositories; the `repositories {}` block in `build.gradle[.kts]` governs library dependencies.
  Hermeto tries repositories in declaration order and uses the first one that serves each artifact.

  Parsing Groovy DSL (`.gradle`) and Kotlin DSL (`.gradle.kts`) differs syntactically. The
  initial implementation uses pattern matching on common declaration forms; see
  [Current Limitations](#current-limitations) for caveats.

- **Artifact URL pattern**:

  ```
  {repo_url}/{group/with/slashes}/{artifactId}/{version}/{artifactId}-{version}[-{classifier}].{ext}
  ```

  Example:
  `https://repo.maven.apache.org/maven2/com/google/guava/guava/30.0-jre/guava-30.0-jre.jar`

- **Authentication**: Custom/private repositories may require credentials. The initial
  implementation targets public repositories only.
- **Rate limiting**: Maven Central and the Gradle Plugin Portal do not enforce strict rate limits
  for normal usage patterns.

### Build Environment Config

After prefetch, Gradle must be configured to use Hermeto's pre-populated dependency cache
instead of fetching from the network. Hermeto's output directory acts as a self-contained
`GRADLE_USER_HOME`, and builds are run with `--offline` to prevent any network access.

#### Environment Variables

| Variable Name | Purpose | Example Value | Required |
|---------------|---------|---------------|----------|
| `GRADLE_USER_HOME` | Points to Hermeto's pre-populated Gradle home | `${output_dir}/gradle-home` | Yes |

Setting `GRADLE_USER_HOME` causes Gradle to load its dependency cache from
`$GRADLE_USER_HOME/caches/modules-2/`, which Hermeto pre-populates with all required artifacts.
No init scripts or repository overrides are needed.

#### Configuration Files

Hermeto creates a directory that serves as `GRADLE_USER_HOME`, containing the Gradle
[dependency cache](https://docs.gradle.org/current/userguide/dependency_caching.html) in its
native format:

```
$output_dir/gradle-home/
└── caches/
    └── modules-2/
        ├── files-2.1/
        │   └── com.google.guava/       # groupId (dots preserved)
        │       └── guava/              # artifactId
        │           └── 30.0-jre/       # version
        │               ├── <sha1>/
        │               │   └── guava-30.0-jre.jar
        │               └── <sha1>/
        │                   └── guava-30.0-jre.pom
        └── metadata-<version>/         # binary metadata — see Current Limitations
```

#### Build Process Integration

Builds must be invoked with the
[`--offline` flag](https://docs.gradle.org/current/userguide/dependency_caching.html#sec:controlling-dependency-caching-command-line),
which instructs Gradle to use only cached artifacts and fail if any required module is absent
from the cache:

```sh
./gradlew --offline <tasks>
```

## Implementation Notes

### Current Limitations

- **Binary metadata store**: Gradle's `--offline` mode requires both the `files-2.1` artifact
  store and the `modules-2/metadata-<version>/` binary metadata store to be present. The binary
  metadata tells Gradle how to locate artifacts by coordinate; without it, Gradle cannot find
  files in `files-2.1` and the build fails. The metadata format is internal to Gradle, is not
  publicly documented, and varies by Gradle version. The current "fix" is to have Hermeto generate
  the dependency metadata in its "dry run" mode, which repopulates the metadata cache. There is
  a risk that this repopulated cache is incomplete, and may result in build failures.

- **Gradle Wrapper distribution**: The `gradlew` script downloads the Gradle binary from
  `https://services.gradle.org/distributions/`. This requires network access in the build
  environment. The initial implementation assumes the Gradle wrapper is pre-installed and checked
  into source control; pre-fetching the wrapper distribution is not yet supported.

- **Build file parsing**: Repository discovery requires parsing Groovy DSL (`.gradle`) and
  Kotlin DSL (`.gradle.kts`) files. The initial implementation uses pattern matching on common
  declaration forms (e.g., `mavenCentral()`, `maven { url = "..." }`). Complex programmatic
  repository declarations (computed URLs, conditionals) may not be recognized, causing Hermeto to
  miss some repository sources.

- **PGP signature verification**: `verification-metadata.xml` supports PGP signature
  verification (`<verify-signatures>true</verify-signatures>`). Hermeto does not currently
  download or verify `.asc` signature files; only SHA-256 and SHA-512 checksum verification
  is implemented.

- **SBOM generation**: Component metadata (group, artifact, version, checksums) for SBOM output
  is not yet populated.

## References

- [Gradle User Manual](https://docs.gradle.org/current/userguide/userguide.html)
- [Declaring Repositories](https://docs.gradle.org/current/userguide/declaring_repositories.html)
- [Dependency Caching](https://docs.gradle.org/current/userguide/dependency_caching.html)
- [Dependency Locking](https://docs.gradle.org/current/userguide/dependency_locking.html)
- [Dependency Verification](https://docs.gradle.org/current/userguide/dependency_verification.html)
- [Gradle Wrapper](https://docs.gradle.org/current/userguide/gradle_wrapper.html)
- [Gradle Plugin Portal](https://plugins.gradle.org)
- [Gradle Build Environment](https://docs.gradle.org/current/userguide/build_environment.html)
