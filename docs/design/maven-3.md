# Maven 3 Design Document

Status: **Experimental**

<!-- 
This template is intended to help contributors add support for a new package manager (ecosystem).
Completed design documents are not required prior to contributing code - they are meant to
facilitate conversation and technical decisionmaking.

To get started:

- Make a copy of this template.
- Fill out the "Overview" section describing the package manager to the best of your ability.
  Submit a PR to start a conversation with the community!
- Complete the "Design" sections as code is written, or if feedback is desired prior to
  implementation.
- Complete the "Implementation Notes" sections as desired, or when the package manager is ready to
  be enabled by default.
-->

## Overview

[Maven](https://maven.apache.org) is a package manager (ecosystem) for Java applications. It is one
of the first full-featured package managers for any programming language and drove the wide open
source ecosystem for Java application development. Java programs are _compiled_ into bytecode,
which is then executed within a Java virtual machine (JVM). Java programs are meant to be agnostic
to host operating systems and CPU architectures, though in practice some programs need dependencies
that have native OS/CPU extensions.

Maven's current active major version - Maven 3 - was first released in 2010, with backwards
compatibilty support for Maven 2 (released in 2005). Maven 4 is currently in a beta phase, with a
GA release planned for later in 2025.

### Developer Workflow

To use Maven, developers first install the [mvn](https://maven.apache.org/install.html) command
line tool. Next, they set up a local "Maven project" where the source code resides - see the
["Maven in 5 Minutes"](https://maven.apache.org/guides/getting-started/maven-in-five-minutes.html)
tutorial for a quick guide on how this works.

A Maven project typically has this directory tree structure:

```
my-app
|-- pom.xml
`-- src
    |-- main
    |   `-- java
    |       `-- io
    |           `-- mydomain
    |               `-- app
    |                   `-- App.java
    `-- test
        `-- java
            `-- io
                `-- mydomain
                    `-- app
                        `-- AppTest.java
```

- A `pom.xml` file in the project's root directory.
- A `src` directory where Java code resides. This is further sub-divided into `main`, `test`, and
  other directories.

Dependencies are declared in the `pom.xml` file. These include:

- Dependencies of the Java code needed at compile time.
- Dependencies used to test the Java code.
- [Plugins](https://maven.apache.org/plugins/index.html) which extend Maven's behavior for building
  and testing applications.

Maven 3 has a [default lifecycle](https://maven.apache.org/guides/introduction/introduction-to-the-lifecycle.html#Lifecycle_Reference)
that sequentially executes several [build phases](https://maven.apache.org/guides/introduction/introduction-to-the-lifecycle.html#Lifecycle_Reference).
Most Maven 3 projects invoke the `package`, `install`, or `deploy` phases to compile and/or test
their applications.

### How the Package Manager Works

#### Dependency Management

Developers declare dependencies in the `pom.xml` file using the `<dependencies>`
[XML element](https://maven.apache.org/guides/getting-started/index.html#How_do_I_use_external_dependencies.3F).
A dependency is identified by specifying its `groupId`, `artifactId`, and `version` (known 
colloquially as the "GAV"). Group IDs correspond to the artifact's domain (
`org.apache.maven.plugins`); artifact IDs correspond to a library or artifact (`plugin-api`);
version corresponds to the semantic version of the specific artifact (`3.9.11`).

Maven has built-in mechanisms to resolve
[transitive dependencies](https://maven.apache.org/guides/introduction/introduction-to-dependency-mechanism.html)
as well as group dependency versions across projects (known as
"[BOM POMs](https://maven.apache.org/guides/introduction/introduction-to-dependency-mechanism.html#Bill_of_Materials_.28BOM.29_POMs)").
They also maintain a [dependency plugin](https://maven.apache.org/plugins/maven-dependency-plugin/)
that helps developers analyze their dependency tree.

#### Artifact Repositories

Maven-compatible artifacts are published to [Maven Repositories](https://maven.apache.org/guides/introduction/introduction-to-repositories.html).
These can be public-facing repositories like [Maven Central](https://central.sonatype.com/), or
private repositories for a corporation or other organization.

Maven Central acts as the default repository for all artifacts. There are multiple ways to
obtain artifacts from [other repositories](https://maven.apache.org/guides/mini/guide-multiple-repositories.html):

- In the project's `pom.xml`, add a `<repositories>` XML element.
- Create a profile in the developer's `settings.xml` file, and adding a `<repositories>` XML
  element.

### Plugins

All actions performed by Maven (even compilation) are executed by [plugins](https://maven.apache.org/guides/getting-started/index.html#How_do_I_use_plugins.3F).
Plugins declare _goals_ which can be invoked at any time, but often are invoked by default during a
build [lifecycle phase](https://maven.apache.org/guides/introduction/introduction-to-the-lifecycle.html).

Plugins can be declared as dependencies in the `pom.xml`, where their default behavior can also be
configured. See the [Getting Started](https://maven.apache.org/guides/getting-started/index.html#How_do_I_use_plugins.3F)
guide for more informatoin.

## Design

### Scope

This design is limited to Maven 3 support. Maven 4 is under active development and introduces
[several breaking changes](https://maven.apache.org/whatsnewinmaven4.html). Given the long
lifecycle of Maven 3, we expect a large number of projects to stay on Maven 3 for a significant
period of time (a year or more).

Maven's dominance has inspired other Java package managers to be "maven-compatible" - most notably
[Gradle](https://gradle.org/). Support for Gradle and similar tools are out of scope for this
design.

Maven plugins are capable of executing arbitrary code. Some of these plugins are able to generate
code (source and compiled bytecode) or download their own dependencies outside of Maven's
resolver. Pre-fetching non-Maven content is out of scope in this design, as well as creating SBOMs
for generated content.

### Dependency List Generation

Unfortunately, Maven 3 does not have a native notion of a "lockfile." A typical Maven project only
lists a minimum set of direct dependencies and required plugins, relying on the resolver to obtain
transitive dependencies at compile or test time. Full specification of transitive dependencies is
[not required](https://maven.apache.org/guides/introduction/introduction-to-dependency-mechanism.html#Transitive_Dependencies).

This design relies on the [Maven Lockfile](https://github.com/chains-project/maven-lockfile)
plugin, which aims to provide this capability. This plugin is not maintained or endorsed by the
Maven project, nor is it widely adopted. However, this plugin is actively maintained and is
[receptive to improvements](https://github.com/chains-project/maven-lockfile/pull/1271) from
external contributors.

#### Dependency List Toolchain

The lockfile plugin comes with three [Maven goals](https://github.com/chains-project/maven-lockfile?tab=readme-ov-file#usage):

- `generate`: generates a lockfile (`lockfile.json`).
- `validate`: verifies that the lockfile dependencies have not changed.
- `freeze`: generates a `pom.lockfile.xml` file, which Maven can use to run a build with pinned
  dependencies.

Developers create the required `lockfile.json` and `pom.lockfile.xml` files by invoking the
`generate` and `freeze` goals from the command line:

```sh
mvn io.github.chains-project:maven-lockfile:generate io.github.chains-project:maven-lockfile:freeze
```

Developers can ensure the lockfile is kept up to date by adding the `validate` goal to the
Maven build lifecycle. We recommend the following plugin configuration:

```xml
<plugin>
  <groupId>io.github.chains-project</groupId>
  <artifactId>maven-lockfile</artifactId>
  <version>5.6.0</version>
  <executions>
    <execution>
      <phase>validate</phase>
      <goals>
        <goal>validate</goal>
      </goals>
    </execution>
  </executions>
  <configuration>
    <checksumMode>remote</checksumMode>
    <checksumAlgorithm>SHA-1</checksumAlgorithm>
    <includeMavenPlugins>true</includeMavenPlugins>
  </configuration>
</plugin>
```

#### Dependency List Format

The `lockfile.json` file has similar structure to npm's `package-lock.json` file, with additional
fields for describing Maven dependencies.

Below is an sample `lockfille.json` file - a fuller, [maintained example](https://github.com/chains-project/maven-lockfile/blob/main/maven_plugin/lockfile.json)
is used by the plugin itself.

```json
{
  "artifactId": "single-dependency-example",
  "groupId": "com.mycompany.app",
  "version": "1",
  "lockFileVersion": 1,
  "dependencies": [
    {
      "groupId": "org.junit.jupiter",
      "artifactId": "junit-jupiter-api",
      "version": "5.9.2",
      "checksumAlgorithm": "SHA-256",
      "checksum": "f767a170f97127b0ad3582bf3358eabbbbe981d9f96411853e629d9276926fd5",
      "scope": "test",
      "resolved": "https://repo.maven.apache.org/maven2/org/junit/jupiter/junit-jupiter-api/5.9.2/junit-jupiter-api-5.9.2.jar",
      "selectedVersion": "5.9.2",
      "included": true,
      "id": "org.junit.jupiter:junit-jupiter-api:5.9.2",
      "children": [
        {
          "groupId": "org.apiguardian",
          "artifactId": "apiguardian-api",
          "version": "1.1.2",
          "checksumAlgorithm": "SHA-256",
          "checksum": "b509448ac506d607319f182537f0b35d71007582ec741832a1f111e5b5b70b38",
          "scope": "test",
          "resolved": "https://repo.maven.apache.org/maven2/org/apiguardian/apiguardian-api/1.1.2/apiguardian-api-1.1.2.jar",
          "selectedVersion": "1.1.2",
          "included": true,
          "id": "org.apiguardian:apiguardian-api:1.1.2",
          "parent": "org.junit.jupiter:junit-jupiter-api:5.9.2",
          "children": []
        },
        {
          "groupId": "org.junit.platform",
          "artifactId": "junit-platform-commons",
          "version": "1.9.2",
          "checksumAlgorithm": "SHA-256",
          "checksum": "624a3d745ef1d28e955a6a67af8edba0fdfc5c9bad680a73f67a70bb950a683d",
          "scope": "test",
          "resolved": "https://repo.maven.apache.org/maven2/org/junit/platform/junit-platform-commons/1.9.2/junit-platform-commons-1.9.2.jar",
          "selectedVersion": "1.9.2",
          "included": true,
          "id": "org.junit.platform:junit-platform-commons:1.9.2",
          "parent": "org.junit.jupiter:junit-jupiter-api:5.9.2",
          "children": [
            {
              "groupId": "org.apiguardian",
              "artifactId": "apiguardian-api",
              "version": "1.1.2",
              "checksumAlgorithm": "SHA-256",
              "checksum": "b509448ac506d607319f182537f0b35d71007582ec741832a1f111e5b5b70b38",
              "scope": "test",
              "resolved": "https://repo.maven.apache.org/maven2/org/apiguardian/apiguardian-api/1.1.2/apiguardian-api-1.1.2.jar",
              "selectedVersion": "1.1.2",
              "included": false,
              "id": "org.apiguardian:apiguardian-api:1.1.2",
              "parent": "org.junit.platform:junit-platform-commons:1.9.2",
              "children": []
            }
          ]
        },
        {
          "groupId": "org.opentest4j",
          "artifactId": "opentest4j",
          "version": "1.2.0",
          "checksumAlgorithm": "SHA-256",
          "checksum": "58812de60898d976fb81ef3b62da05c6604c18fd4a249f5044282479fc286af2",
          "scope": "test",
          "resolved": "https://repo.maven.apache.org/maven2/org/opentest4j/opentest4j/1.2.0/opentest4j-1.2.0.jar",
          "selectedVersion": "1.2.0",
          "included": true,
          "id": "org.opentest4j:opentest4j:1.2.0",
          "parent": "org.junit.jupiter:junit-jupiter-api:5.9.2",
          "children": []
        }
      ]
    }
  ],
  "mavenPlugins": [],
  "metaData": {
    "environment": {
      "osName": "Mac OS X",
      "mavenVersion": "3.8.2",
      "javaVersion": "21.0.5"
    },
    "config": {
      "includeMavenPlugins": false,
      "allowValidationFailure": false,
      "includeEnvironment": true,
      "reduced": false,
      "mavenLockfileVersion": "5.4.3-SNAPSHOT",
      "checksumMode": "local",
      "checksumAlgorithm": "SHA-256"
    }
  }
}
```

#### Checksum Generation

The lockfile plugin can compute checksums locally, or use the checksum hosted on the remote Maven
repository. Maven Central requires artifacts to provide at least one checksum using a
[supported algorithm](https://maven.apache.org/resolver/about-checksums.html) when published.
Most artifacts on Maven Central provide MD5 and SHA-1 checksums.

The recommended configuration above utilizes the remote SHA-1 checksum if present. The lockfile
plugin falls back to locally-computed SHA-256 checksums if the desired remote checksum is not
available.

#### Alternatives Considered

The Maven [dependency plugin](https://maven.apache.org/plugins/maven-dependency-plugin/) provides
some tooling to facilitate dependency analysis and download content. Though this is an official
plugin sponsored by Maven's parent foundation (Apache Software Foundation), the maintainers are
reluctant to add features Hermeto would likely need, such as [checksums](https://lists.apache.org/thread/p1st01lcrp2jy127jtjqwg72v9bbrcyz).
With the upcoming work in Maven 4, there is a high likelhood that this plugin will be replaced with
an entirely new tool, such as current [toolbox](https://github.com/maveniverse/toolbox) authored by
active Maven maintainers.

In Maven 3.9, the default dependency resolver added a new ["trusted checksums"](https://maven.apache.org/resolver/expected-checksums.html)
feature. This allows checksums to be stored alongside source code and used for validation during
the build process. The current documentation is woefully inadequate, and enabling this feature
requires configuration to be added outside of the project's `pom.xml` file. This feature does not
work with earlier versions of Maven, nor is it guaranteed to work as is when Maven 4 is released.

The lockfile plugin does not necessarily require teams to add it as a plugin dependency - its goals
can be executed as "standalone" commands. The plugin is also backwards-compatible with prior
versions of Maven, making it easier to adopt in legacy codebases.

### Fetching Content

TBD
<!-- 
Describe how Hermeto should fetch dependencies on the dependency list. This will form the core of
the `fetch-deps` command implementation.

_Note: The subsections below are not required, but serve as a useful starting point_.
-->

#### Native vs. Hermeto Fetch

TBD

<!-- 
Decide if the package manager can be trusted to fetch dependencies, or if Hermeto should "reverse
engineer" the dependency download process:

- Does the package manager have mechanisms to resolve dependencies from a fixed list?
- Does the package manager have plugins, hooks, or other mechanisms that allow arbitrary code to be
  executed during the download/resolution phase?

In general, Hermeto should be responsible for downloading dependencies.
-->

#### Project Structure

TBD
<!-- 
Provide directory tree diagrams of the following:

- The developer's project (where dependencies are typically declared).
- Any "cache" directories where dependencies are installed locally to disk.
-->

#### File Formats and Metadata

TBD
<!-- 
Document any specific file format requirements:

- **Package file formats**: Expected formats for downloaded packages
- **Metadata requirements**: Additional metadata files Hermeto must provide
- **Naming conventions**: Required naming patterns for files and directories
- **Version handling**: How different versions should be organized
-->

#### Network Requirements

TBD
<!-- 
Describe network-related considerations:

- **Registry endpoints**: URLs and APIs Hermeto needs to access
- **Authentication**: Any authentication requirements for package registries
- **Rate limiting**: Considerations for API rate limits
- **Mirror support**: Support for alternative registries or mirrors
-->

### Build Environment Config

TBD

<!-- 
Describe how the build environment should be configured to use Hermeto's pre-fetched dependencies.
This section will form the basis of the `generate-env` and `inject-files` commands.
-->

#### Environment Variables

TBD

<!-- Describe any environment variables that need to be set so that the package manager uses the
dependencies pre-fetched by Hermeto. A table is usually sufficient:

| Variable Name | Purpose | Example Value | Required |
|---------------|---------|---------------|----------|
| `EXAMPLE_VAR` | Points to dependency cache | `/path/to/hermeto-deps` | Yes | 
-->

#### Configuration Files

<!-- 
Describe any files that Hermeto should generate or provide to the package manager. This will form
the basis of the `inject-files` implementation. A tree diagram can be helpful here:

```
hermeto-deps/
├── [package-manager-name]/
│   ├── metadata/
│   ├── packages/
│   └── manager-config.json
```

If needed, add sub-sections to describe specific files in detail.
-->

#### Build Process Integration (optional)

TBD

<!-- 
If needed, describe any build process changes that are required outside of the environment
variables and configuration file changes above. -->

## Implementation Notes

<!-- 
This section helps the community evaluate the maturity of the package manager. Experimental package
managers are hidden behind the `--dev-package-managers` flag (disabled by default). It is optional
for experimental package managers, but should be completed before the package manager is enabled
by default.
-->

### `maven` Generic Prefetcher

The "generic" prefetcher supports one-off downloads of [Maven artifacts](../generic.md). This was
initially created as a work-around for full Maven support. However, in many scenarios Java binaries
are installed from Maven repositories directly and incorporated into a non-Java build (ex: download
in a container image build). The current `maven` flavor of the [generic pre-fetcher](https://hermetoproject.github.io/hermeto/generic/#maven-artifacts)
produces a more accurate SBOM in this situation, allowing downstream systems to better monitor
these dependencies for vulnerabilities.

In light of this use case, the `maven` generic prefetcher should not be deprecated. Documentation
will need to guide developers to the right Hermeto package manager for their use case.

### Current Limitations

TBD
<!-- 
Document known limitations of the current implementation:

- **Missing features**: Functionality not yet implemented _in Hermeto_
- **Edge cases**: Scenarios that may not work correctly
- **Performance considerations**: Known performance issues or bottlenecks
- **Ecosystem considerations**: Features and discussion in the package manager ecosystem that may
  impact Hermeto's implementation
-->

### Testing Strategy

TBD
<!-- Describe how the package manager implementation is tested:

- **Unit tests**: Key areas covered by unit tests
- **Integration tests**: End-to-end testing scenarios
- **Test data**: Sample projects and dependencies used for testing
-->

## References

Optional - provide reference links that support decisions in this document.

- **Apache Maven**: https://maven.apache.org/index.html
- **Maven Lockfile Plugin**: https://github.com/chains-project/maven-lockfile

## Changelog

- 2025-07-30: Initial draft

