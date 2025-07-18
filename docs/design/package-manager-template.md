# [Package Manager Name] Design Document

This template is intended to help contributors add support for a new package manager (ecosystem).
Yes it is big - please try not to be intimidated by its size! The sections and probative questions
are based on the community's experiences adding new package manager ecosystems to Hermeto.

You may also use past [design documents](https://github.com/hermetoproject/hermeto/tree/main/docs/design)
as inspiration.

**Contributors**:

Completed design documents are not required prior to contributing code - they are meant to
facilitate conversation and technical decisionmaking. _You do not need to submit a completed_
_document in a single pull request or commit_.

To get started:

- Make a copy of this template.
- Fill out the "Overview" section describing the package manager to the best of your ability.
  Submit a PR to start a conversation with the community!
- Complete the "Design" sections as code is written, or if feedback is desired prior to
  implementation.
- Complete the "Implementation Notes" sections as desired, or when the package manager is ready to
  be enabled by default.

## Overview

Briefly describe the package manager and its primary use cases. Provide links to the package
manager's documentation (avoid copy/pasting content that is maintained elsewhere).

### Developer Workflow

Describe the typical workflow for a developer using this package manager:

1. **Prerequisites**: What tools are installed? How is a new project set up?
2. **Adding dependencies**: How are dependencies declared and added?
3. **Dependency management**: How do developers manage, update, and remove dependencies?
4. **Build process**: How does the build/compilation process work?

Include common commands and configuration files developers interact with.

### How the Package Manager Works

Briefly describe the package manager's architecture, workflow, and core concepts. Provide links
if this content is maintained elsewhere. Items to provide may include:

- **Registry/repository model**: Where are packages hosted and discovered?
- **Package identity and versioning**: How are packages identified?
- **Dependency resolution**: Which tools resolve and manage dependencies?
- **Configuration options**: How can a developer tune/adjust the behavior of the package manager?

## Design

This is the core of the template, where the technical decisions for Hermeto can be worked through.
Complete these sections alongside code implementation as needed.

### Scope

Good designs are aware of their limitations, and it helps to state these up front:

- Which package managers, tools, and configuration are "in scope" for this design?
- Are there related tools that should be considered in or out of scope?
- Is there any behavior or configuration that should be considered an "edge case"?

### Dependency List Generation

Hermeto needs a reliable list of dependencies to pre-fetch. This section documents the tools and
procedure needed to generate this list. Hermeto is **not** responsible for generating the
dependency list on its own.

_Note: The subsections below are not required, but serve as a useful starting point_.

#### Dependency List Toolchain [optional]

Describe how a developer can generate a machine-readable list of dependencies for Hermeto to pre-
fetch. Consider the following:

- Does the package manager natively provide this information?
- If not, is there external tooling that can provide this information?
- Are there any known limitations to these tools?
- Are required tools widely used, or are they considered experimental?

#### Dependency List Format [optional]

Document the structure and content of the generated dependency list:

- **File format**: JSON, YAML, text, etc.
- **Required fields**: Essential information for each dependency
- **Optional fields**: Additional metadata that may be included
- **Example output**: Provide a sample dependency list (or snippet)

#### Checksum Generation [optional]

Describe how checksums are handled:

- **Native checksum support**: Whether the package manager provides checksums
- **Checksum algorithms**: Which hashing algorithms are used (SHA-256, SHA-1, etc.)
- **Checksum sources**: Where checksums are obtained (registry metadata, computed locally, etc.)
- **Missing checksum handling**: What happens when checksums are unavailable?

### Fetching Content

Describe how Hermeto should fetch dependencies on the dependency list. This will form the core of
the `fetch-deps` command implementation.

_Note: The subsections below are not required, but serve as a useful starting point_.

#### Native vs. Hermeto Fetch [optional]

Decide if the package manager can be trusted to fetch dependencies, or if Hermeto should "reverse
engineer" the dependency download process:

- Does the package manager have mechanisms to resolve dependencies from a fixed list?
- Does the package manager have plugins, hooks, or other mechanisms that allow arbitrary code to be
  executed during the download/resolution phase?

In general, Hermeto should be responsible for downloading dependencies.

#### Project Structure [optional]

Provide directory tree diagrams of the following:

- The developer's project (where dependencies are typically declared).
- Any "cache" directories where dependencies are installed locally to disk.

#### File Formats and Metadata [optional]

Document any specific file format requirements:

- **Package file formats**: Expected formats for downloaded packages
- **Metadata requirements**: Additional metadata files Hermeto must provide
- **Naming conventions**: Required naming patterns for files and directories
- **Version handling**: How different versions should be organized

#### Network Requirements [optional]

Describe network-related considerations:

- **Registry endpoints**: URLs and APIs Hermeto needs to access
- **Authentication**: Any authentication requirements for package registries
- **Rate limiting**: Considerations for API rate limits
- **Mirror support**: Support for alternative registries or mirrors

### Build Environment Config

Describe how the build environment should be configured to use Hermeto's pre-fetched dependencies.
This section will form the basis of the `generate-env` and `inject-files` commands.

#### Environment Variables

Describe any environment variables that need to be set so that the package manager uses the
dependencies pre-fetched by Hermeto. A table is usually sufficient:

| Variable Name | Purpose | Example Value | Required |
|---------------|---------|---------------|----------|
| `EXAMPLE_VAR` | Points to dependency cache | `/path/to/hermeto-deps` | Yes |

#### Configuration Files

Describe any files that Hermeto should generate or provide to the package manager. This will form
the basis of the `inject-files` implementation. A tree diagram can be helpful here:

```
project.git/
├── <dependencies lockfile> # ex: requirements.txt, yarn.lock, pom.xml, packages.json
├── [package-manager-data]/ # ex: node_modules, .config, etc.
│   ├── cache/
│   ├── config/
│   └── manager-file.json
```

If needed, add sub-sections to describe specific files in detail.

#### Build Process Integration [optional]

If needed, describe any build process changes that are required outside of the environment
variables and configuration file changes above.

## Implementation Notes

This section helps the community evaluate the maturity of the package manager. Experimental package
managers use the `x-` prefix in their package manager name. This section should be completed prior
to the community declaring the package manager "fully supported."

### Current Limitations

Document known limitations of the current implementation:

- **Missing features**: Functionality not yet implemented _in Hermeto_
- **Edge cases**: Scenarios that may not work correctly
- **Performance considerations**: Known performance issues or bottlenecks
- **Ecosystem considerations**: Features and discussion in the package manager ecosystem that may
  impact Hermeto's implementation

## References [optional]

Provide reference links that support decisions in this document.

- **Official documentation**: Links to package manager documentation
- **Specifications**: Relevant technical specifications or RFCs
- **Community resources**: Forums, mailing lists, or chat channels
- **Related tools**: Other tools in the ecosystem
