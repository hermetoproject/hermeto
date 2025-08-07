# Process for Adding Package Managers

Date: 2025-07-18

## Context

Hermeto is intended to support a wide range of programming languages and dependency management
systems ("package manager ecosystems"). Now that we have a "critical mass" of package managers, the
project should codify the requirements Hermeto needs to support a given package manager, and the
"lifecycle" for evolving a package manager.

The phases of the lifecycle are inspired by our experiences with [DNF](https://docs.fedoraproject.org/en-US/quick-docs/dnf/)
and [Maven](https://maven.apache.org/), which currently do not have mature mechanisms for declaring
and pinning dependencies.

## Decision

### Candidates for Package Manager Support

Hermeto can support _any_ package manager ecosystem that is able to generate a comprehensive list
of uniquely identified dependencies, and has a process for persisting these dependencies locally to
disk. The dependency list does not have to include a set of expected checksums for each item,
however this practice is **strongly encouraged**. For Hermeto to support a package manager
ecosystem, the dependency list must include:

* Direct dependencies of the artifact to be built.
* Indirect (transitive) dependencies of the artifact.

The dependency list _may_ also include external tools needed for compilation, which may be part of
the package manager's toolchain ecosystem (ex: [Maven plugins](https://maven.apache.org/plugins/)).

Hermeto is **not** responsible for generating or validating this dependency list.

### Contribution and Promotion Process

The Hermeto project will use a phased approach for accepting new package managers. The "lifecycle"
of a package manager can be broken down into the following phases:

1. Proposal
2. Experimental
3. Incubating
4. Graduated

#### Proposal Phase

In the "proposal" phase, the candidate package manager is evaluated for project fit and
feasibility. Those who wish to add a new package manager should submit an ADR describing the
package manager and the high-level process developers should use to generate the dependency list.

Maintainers at this stage should evaluate the following:

   1. Can a developer generate a dependency list? Even an incomplete one?
   2. What is the value to the community? Is the package manager worth maintaining?
   
Part of Hermeto's mission is to encourage software supply chain best practices in the software
development lifecycle. Therefore maintainers should **not** consider limitations on how the
dependency list is generated at this phase. Procedures that rely on proof of concept tools that
have not been adopted "upstream" are fine.

Package managers graduate to "Experimental" when the ADR is approved and merged.

#### Experimental Phase

During the experimental phase, the new package manager is added to the
[_dev_package_managers](https://github.com/hermetoproject/hermeto/blob/0.31.0/hermeto/core/resolver.py#L27-L29)
list. This disables the package manager by default. Contributors begin implementing the package
manager by creating a new module within `hermeto.core.package_managers`, then declaring a main
`fetch_*` function that obtains the package manager's dependencies.

Experimental package managers do not need to work end to end, or have complete functionality. They
_should_ warn users that the package manager is experimental and not guaranteed to produce a
successful hermetic build.

As the experimental package manager is iterated on, key technical decisions should be captured in a
corresponding document in the `docs/design` folder. The document should eventually include the
following:

* Background context of the package manager - how it works, the typical "day in the life" of a
  developer.
* Procedure for generating the dependency list and/or checksum file(s). Links to external tools
  should be provided if the package manager does not natively support the generation of such files.
* Links to technical requirements/specifications if Hermeto needs to download files to a specific
  directory tree structure, or supply other metadata.
* Environment variables that need to be set so the package manager utilizes the files downloaded by
  Hermeto.
* Configuration files that need to be updated or generated so that the package manager builds with
  the dependencies pinned by Hermeto.

Package managers graduate to "Incubating" when the following criteria are satisfied:

* The package manager fully implements the pre-fetch process:
  * Downloads dependencies in a structure the package manager understands.
  * Sets environment variables so that the package manager utilizes Hermeto's pre-fetched
    dependencies.
  * Generates or updates configuration files so that the package manager builds with Hermeto's pre-
    fetched dependencies.
* Maintainers are satisfied with the design document details.
* Demonstration of a proof of concept hermetic build for the package manager. The PoC does not need
  to use a "real" project - a "hello world" project with minimal dependencies is sufficient.

#### Incubating Phase

During the incubating phase, the package manager should still be disabled by default, but does not
need to emit any "experimental" warnings. The Hermeto community should seek feedback from the
package manager's community regarding the process and demonstrate hermetic builds with increasing
levels of project complexity.

Incubating package managers should use this phase to draft end user documentation (under the
`docs/` directory). Documentation should instruct end users how to generate the dependency list and
execute a hermetic build with Hermeto. These procedures can reference external tools that are proof
of concept or otherwise not fully supported by the package manager's community. If such a tool is
utilized, the documentation should indicate these procedures are subject to change at any time.

Incubating package managers are allowed to make breaking changes. If a breaking change is
introduced, it should be documented in a release note.

Incubating package managers are promoted to "Graduated" when the Hermeto community agrees the
following criteria are satisfied:

1. The process for generating the dependency list is supported by the underlying package manager's
   community - either directly or through a well-maintained external party tool.
2. A developer with reasonable package manager experience can use Hermeto to pre-fetch dependencies
   by reading the project documentation.
3. Hermeto can verify the integrity of pre-fetched content if the required information (checksums)
   are present.
4. A sufficient number of demonstration/pilot projects have been able to use Hermeto to execute a
   hermetic build with the package manager.

#### Graduated Phase

Graduated package managers are moved from the `_dev_package_managers` set to the full set of
supported package managers. At this point the package manager is enabled by default and suitable
for general use.

Graduated package managers can only be removed with a major version update to Hermeto (ex: v1 to
v2). User-facing details such as configuration values/flags can be deprecated and rendered non-
functional with a minor version update, but cannot be removed without a major version update. Any
deprecation must include a release note announcing the change.

## Consequences

* Contribution guidelines will need to be updated to codify the process/lifecycle in this ADR,
  including:
  * Iterative design documentation alongside code changes.
  * Document the requirements for graduating package managers at each phase of the lifecycle.
* Our current support for RPM/DNF should be documented (technical designs and end-user procedures).
* The Hermeto project should create a design document template contributors should use when they
  add and improve a new package manager.
* Hermeto's release notes process must be able to announce deprecations and removals.
