# Design Document for Hugging Face Support

Contents:

1. [Background](#i-background)
2. [Hugging Face Ecosystem Overview](#ii-hugging-face-ecosystem-overview)
3. [Design Approach and Ecosystem Maturity](#iii-design-approach-and-ecosystem-maturity)
4. [Lockfile Format Design](#iv-lockfile-format-design)
5. [Implementation Details](#v-implementation-details)
6. [Security Considerations](#vi-security-considerations)
7. [Appendices](#appendices)

## I. Background

### The Rise of Machine Learning in Software Supply Chains

[Hugging Face Hub](https://huggingface.co) has emerged as the primary distribution platform for machine learning models and datasets, hosting hundreds of thousands of models used across the AI/ML ecosystem. Organizations increasingly incorporate pre-trained models from Hugging Face into their applications, creating new challenges for software supply chain security and hermetic builds.

The ecosystem is still young. Hugging Face Hub is the obvious centerpoint today, but may be replaced by something else in years to come.

### The SBOM-for-AI Movement

The software industry is actively working to extend Software Bill of Materials (SBOM) practices to cover AI systems. Key developments include:

- **[CISA SBOM-for-AI Tiger Team](https://github.com/aibom-squad/SBOM-for-AI-Use-Cases)**: Launched in May 2024, this collaborative effort brings together industry experts to develop guidelines for AI-specific SBOMs. The project identifies critical use cases including compliance, vulnerability management, risk assessment for open-source models/datasets, and model lifecycle tracking.

- **CycloneDX ML-BOM**: CycloneDX introduced [machine learning capabilities](https://cyclonedx.org/capabilities/mlbom/) to represent datasets, models, and configurations. ML-BOM supports documentation of dataset provenance, ethical considerations, and risks related to bias, data integrity, and model security.

- **SPDX 3.0 AI Profile**: The SPDX specification has added AI-specific extensions in version 3.0 to better represent machine learning components in SBOMs.

Hermeto's Hugging Face support is a step toward integrating AI artifacts into traditional software supply chain practices, enabling organizations to track, audit, and secure their ML dependencies alongside conventional software packages.

### Hermetic Builds for ML Applications

Hermetic build systems struggle with ML applications because models are typically downloaded arbitrarily from scripts created by data scientists who manage the ML portions of the application, lacking the consistency and repeatability of typical software dependency management.

The work in hermeto here exploits an opportunity presented by the near-ubiquitos Hugging Face python sdk, called [transformers](https://huggingface.co/docs/transformers/en/index). That library supports a `HF_HUB_OFFLINE=1` environment variable, which instructs Hugging Face libraries to operate entirely from local cache without network access. If we can **prefetch** all required models and datasets before the hermetic build, we can achieve the same level of reproducibility and security for ML applications that we have for traditional software.

### Problem Statement

To enable hermetic builds of ML applications, we need:

1. A way to **declare** which Hugging Face models and datasets a project depends on
2. A mechanism to **prefetch** these artifacts before the build
3. A **cache structure** compatible with Hugging Face libraries' offline mode
4. An **SBOM** documenting all fetched ML artifacts with proper provenance

This design document describes Hermeto's approach to solving these challenges.

## II. Hugging Face Ecosystem Overview

### Glossary

- **Hugging Face Hub**: The primary platform for hosting and distributing ML models, datasets, and spaces. Similar to how PyPI hosts Python packages or npm hosts JavaScript packages.

- **Repository**: A Git repository on Hugging Face Hub containing a model or dataset. Repositories follow the format `namespace/name` (e.g., `microsoft/deberta-v3-base`) or just `name` for models in the default namespace (e.g., `gpt2`).

- **Revision**: A Git commit hash identifying a specific version of a repository. Since Hugging Face repositories are Git repos, every change creates a new commit. Using explicit revisions ensures reproducibility.

- **Model**: A trained machine learning model, typically consisting of weights files, configuration, and tokenizer data. Models can range from a few MB to hundreds of GB.

- **Dataset**: A collection of data used for training or evaluating models. Datasets are also versioned via Git.

- **Repository Type**: Either `"model"` or `"dataset"`. This determines which API endpoints and cache paths are used.

- **Blob**: A content-addressable file stored by its hash. The Hugging Face cache uses a blob store similar to Git's object database.

- **Snapshot**: A directory structure representing all files in a repository at a specific revision. Snapshots contain symlinks to blobs.

- **Ref**: A named pointer to a revision, similar to Git branches and tags. Examples: `main`, `v1.0`.

- **Git LFS (Large File Storage)**: Hugging Face uses Git LFS to handle large model weight files efficiently. LFS files are not stored directly in Git but referenced by pointer files.

### Repository Structure on Hugging Face Hub

A typical Hugging Face model repository contains:

```
microsoft/deberta-v3-base/
├── README.md                    # Model card with documentation
├── config.json                  # Model architecture configuration
├── tokenizer_config.json        # Tokenizer settings
├── vocab.txt                    # Vocabulary file
├── pytorch_model.bin            # PyTorch weights (pickle format) ⚠️
├── model.safetensors            # SafeTensors weights (recommended) ✓
├── tf_model.h5                  # TensorFlow weights
└── flax_model.msgpack           # JAX/Flax weights
```

Models may include weights in multiple framework formats (PyTorch, TensorFlow, JAX). Users typically only need one format.

### File Formats and Security Implications

**Safe formats** (no code execution):
- `*.safetensors`: SafeTensors format, designed for safe serialization without code execution
- `*.json`: Configuration and metadata files
- `*.txt`: Vocabulary and text files

**Unsafe formats** (arbitrary code execution during loading):
- `*.bin`, `*.pt`, `*.pth`: PyTorch pickle format - executes Python code during deserialization
- `*.pkl`, `*.pickle`: Raw pickle files
- `modeling_*.py`: Custom model code imported by transformers library

The security risk is **NOT during Hermeto's fetch** (which only performs HTTP downloads) but **during model loading** by the user's application (when pickle deserialization occurs).

### Git LFS and Large Files

Hugging Face uses Git LFS extensively. When you clone a model repository, you receive:
- Small files (configs, tokenizers): Stored directly in Git
- Large files (model weights): LFS pointer files in Git, actual content stored separately

The `huggingface_hub` Python library handles LFS downloads transparently, fetching the actual file content when needed.

### Hugging Face Cache Structure

The `huggingface_hub` library uses a cache structure compatible with Git and offline mode:

```
~/.cache/huggingface/hub/
├── models--microsoft--deberta-v3-base/
│   ├── blobs/
│   │   ├── 559062ad13d311b87b2c455e67dcd5f1c8f65111  # Content-addressed files
│   │   └── a1b2c3d4...
│   ├── refs/
│   │   └── main  # Points to a specific revision
│   └── snapshots/
│       └── 559062ad13d311b87b2c455e67dcd5f1c8f65111/  # Full directory tree
│           ├── config.json -> ../../blobs/a1b2c3d4...
│           └── model.safetensors -> ../../blobs/559062ad...
```

This structure enables:
- **Deduplication**: Identical files across revisions share the same blob
- **Atomic snapshots**: Each revision gets a complete directory view via symlinks
- **Offline mode**: When `HF_HUB_OFFLINE=1`, libraries load from snapshots without network calls

Hermeto must recreate this exact structure to ensure compatibility with Hugging Face libraries.

## III. Design Approach and Ecosystem Maturity

### The Dependency Declaration Problem

Modern SBOM generation tools (Syft, Hermeto, Trivy, etc.) depend on established package manager conventions and file formats. They work by parsing standardized dependency declarations:

- Python: `requirements.txt`, `Pipfile`, `pyproject.toml`
- JavaScript: `package.json`, `package-lock.json`
- Go: `go.mod`, `go.sum`
- Rust: `Cargo.toml`, `Cargo.lock`

These standards didn't always exist. Consider Python's evolution:

**Early Python (pre-2000s):**
- Only `setup.py` existed for package building
- No standard way to declare dependencies
- Each project handled dependencies differently (or not at all)
- **SBOM tools were not feasible** - no consistent format to parse

**Setuptools era (2000s):**
- `setup.py` gained `install_requires`
- Still programmatic Python code, not a declarative format
- Inconsistent patterns across projects
- SBOM tools remained difficult

**Requirements.txt era (2010s):**
- `pip freeze > requirements.txt` became common practice
- First **declarative** format for dependencies
- SBOM tools became possible but had to handle variations
- No lock file format - versions could still drift

**Modern Python (2020s):**
- `Pipfile` + `Pipfile.lock` (Pipenv)
- `pyproject.toml` + `poetry.lock` (Poetry)
- `pyproject.toml` with PDM, Hatch, etc.
- **SBOM tools can reliably extract dependency graphs**

### Current State of the AI/ML Dependency Ecosystem

The Hugging Face and broader AI/ML ecosystem is currently in a **pre-standardization phase** similar to early Python.

**How projects currently reference ML dependencies:**
- Hardcoded strings in code: `model = AutoModel.from_pretrained("gpt2")`
- Configuration files: YAML, JSON, TOML with varying schemas
- Environment variables: `MODEL_NAME=microsoft/deberta-v3-base`
- Comments and documentation: "Download model X before running"
- Download scripts: Custom bash/Python scripts that fetch models
- No references at all: Models downloaded at runtime on-demand

**Even major ML frameworks lack dependency standards:**

[LangChain](https://python.langchain.com/) and [LlamaIndex](https://developers.llamaindex.ai/), two of the most popular AI orchestration frameworks, use entirely **programmatic configuration**:

- **LangChain**: Models configured directly in Python code
  ```python
  model = init_chat_model("gemini-2.5-flash", model_provider="google_genai")
  chain = prompt | ChatAnthropic(model="claude-2.1")
  ```
  Uses standard `pyproject.toml` for library dependencies, but has no declarative format for model dependencies. "Configurable runnables" allow runtime model swapping, but it's still code-based.

- **LlamaIndex**: Same situation - programmatic model initialization, environment variables for API keys, no dependency declaration format.

The closest things to standards in the wild are:
- **MLflow's `MLmodel` file** - but this is for model serving/registry, not dependency declaration
- **Custom YAML configs** - every team creates their own ad-hoc format
- **Environment variables** - `MODEL_NAME=gpt-4` patterns with no standardization

**There is no `huggingface.txt` equivalent.** No de facto standard exists for declaring Hugging Face dependencies in a machine-readable format that SBOM tools can rely upon.

This presents a challenge: How do we enable hermetic builds and generate SBOMs for ML applications when the ecosystem hasn't standardized dependency declaration?

### Hermeto's Approach: Custom Lockfile Format

Given the current state of ecosystem maturity, Hermeto introduces a **custom lockfile format**: `huggingface.lock.yaml`.

This approach:
- Provides an **explicit, declarative** format for Hugging Face dependencies
- Enables **hermetic builds today** without waiting for ecosystem standardization
- Generates **proper SBOMs** with provenance and version information
- Uses a **simple, human-readable** format (YAML)
- Avoids **arbitrary code execution** (unlike parsing Python code or config files that might import modules)

**Implementation:**
- Custom lockfile: `huggingface.lock.yaml` in the repository root
- Direct HTTP downloads via `huggingface_hub` Python library
- Cache structure compatible with Hugging Face tools (`HF_HUB_OFFLINE=1` mode)
- SBOM generation using the [Hugging Face PURL specification](https://github.com/package-url/purl-spec/blob/main/types-doc/huggingface-definition.md)

**Advantages:**
- ✅ Works today with current tooling
- ✅ Explicit and auditable - no hidden dependencies
- ✅ Secure - no code execution during fetch
- ✅ Reproducible - uses Git commit hashes for versioning
- ✅ Compatible with Hugging Face offline mode

**Disadvantages:**
- ❌ Requires manual lockfile creation
- ❌ Not a community standard (yet)
- ❌ Adds another file format to the ecosystem
- ❌ Can become outdated if code changes model references

### Future Evolution and Deprecation Path

**This implementation should be considered a bridge solution**, not a permanent standard.

When the AI/ML community develops standardized dependency declaration formats (analogous to how Python evolved from `setup.py` to `requirements.txt` to `pyproject.toml`), Hermeto should:

1. **Add support for the community standard** when it emerges
2. **Deprecate the custom lockfile format** in favor of the standard
3. **Provide migration tooling** to help users transition
4. **Document the deprecation timeline** clearly

Possible future standards to watch:
- Hugging Face official lockfile format (if developed)
- ML-specific sections in `pyproject.toml` or equivalent
- Framework-specific conventions from LangChain, LlamaIndex, or Hugging Face Transformers
  - Currently these frameworks use programmatic configuration only
  - They may evolve to define declarative dependency formats
- Industry-wide AI dependency specifications from CISA/SBOM-for-AI efforts

**The goal is not to create a new standard, but to enable hermetic builds while the ecosystem matures.**

## IV. Lockfile Format Design

### Design Goals

The lockfile format must be:
1. **Human-readable**: Data scientists should be able to write and review it
2. **Validatable**: Clear schema with helpful error messages
3. **Explicit**: No implicit behavior or default version resolution
4. **Reproducible**: Pin exact versions via Git commit hashes
5. **Flexible**: Support file filtering for large models

### Why YAML?

YAML was chosen over JSON or TOML because:
- **Common in ML workflows**: Used by MLflow, Kubernetes, Docker Compose, etc.
- **Human-friendly**: Comments, multiline strings, less punctuation than JSON
- **Good Python support**: `pyyaml` is widely available
- **Familiar to data scientists**: More common than TOML in ML tooling

### Lockfile Schema

```yaml
metadata:
  version: "1.0"

models:
  - repository: "gpt2"
    revision: "e7da7f221ccf5f2856f4331d34c2d0e82aa2a986"
    type: "model"

  - repository: "microsoft/deberta-v3-base"
    revision: "559062ad13d311b87b2c455e67dcd5f1c8f65111"
    type: "model"
    include_patterns:
      - "*.safetensors"
      - "config.json"
      - "tokenizer*"

  - repository: "squad"
    revision: "d6ec3ceb99ca480ce37cdd35555d6cb2511d223b"
    type: "dataset"
```

### Required Fields

#### `metadata`
The metadata section contains lockfile format information.

- **`version`**: Lockfile format version. Currently `"1.0"`.
  - Type: String literal `"1.0"`
  - Purpose: Enables future format evolution with backwards compatibility

#### `models`
List of models and datasets to fetch.

- **`repository`**: Repository identifier
  - Type: String
  - Format: `"name"` or `"namespace/name"`
  - Examples: `"gpt2"`, `"microsoft/deberta-v3-base"`, `"openai/whisper-large-v2"`
  - Validation: Cannot be empty, cannot have leading/trailing whitespace, max 2 path segments

- **`revision`**: Git commit hash
  - Type: String (40 hexadecimal characters)
  - Example: `"e7da7f221ccf5f2856f4331d34c2d0e82aa2a986"`
  - Validation: Must be a full 40-character SHA-1 hash (lowercase hex)
  - Why not branches/tags? Commit hashes are immutable and ensure reproducibility

- **`type`**: Repository type
  - Type: String literal `"model"` or `"dataset"`
  - Default: `"model"`
  - Purpose: Determines API endpoints and cache paths

### Optional Fields

#### `include_patterns`
List of glob patterns to filter which files to download.

- **Type**: List of strings, or omit entirely
- **Default**: `null` (download all files)
- **Pattern syntax**: Standard glob patterns with `**` for recursive matching
  - `*.safetensors` - All SafeTensors files in root
  - `**/*.json` - All JSON files recursively
  - `config.json` - Specific file
  - `tokenizer*` - Files starting with "tokenizer"

**Why include patterns?**
1. **Save bandwidth**: Models often have PyTorch, TensorFlow, and JAX weights - users typically need only one
2. **Security**: Exclude unsafe pickle-based formats, download only SafeTensors
3. **Size**: Skip documentation, examples, or framework-specific files

**Pattern matching details:**
- Patterns match against full file paths relative to repository root
- `**` matches any number of directories
- `*` matches any characters within a directory
- Patterns are case-sensitive
- Multiple patterns are OR'd together (file matches if ANY pattern matches)

### Validation Rules

The lockfile is validated using Pydantic models with strict error messages:

<details>
<summary>Invalid metadata version</summary>

```yaml
metadata:
  version: "2.0"  # ❌ Only "1.0" supported
models: []
```

Error: `Field required [type=literal_error, input_value='2.0', input_type=str]`
</details>

<details>
<summary>Missing required field</summary>

```yaml
metadata:
  version: "1.0"
models:
  - repository: "gpt2"
    # ❌ Missing 'revision'
    type: "model"
```

Error: `Field required [type=missing, input_value={'repository': 'gpt2', 'type': 'model'}, input_type=dict]`
</details>

<details>
<summary>Invalid revision format</summary>

```yaml
metadata:
  version: "1.0"
models:
  - repository: "gpt2"
    revision: "main"  # ❌ Not a commit hash
    type: "model"
```

Error: `Revision must be a 40-character Git commit hash, got 'main'. You can find the commit hash on the HuggingFace model page or using the huggingface_hub library.`
</details>

<details>
<summary>Invalid repository format</summary>

```yaml
metadata:
  version: "1.0"
models:
  - repository: "org/team/model"  # ❌ Too many path segments
    revision: "e7da7f221ccf5f2856f4331d34c2d0e82aa2a986"
    type: "model"
```

Error: `Repository must be in format 'name' or 'namespace/name', got 'org/team/model'`
</details>

<details>
<summary>Invalid model type</summary>

```yaml
metadata:
  version: "1.0"
models:
  - repository: "gpt2"
    revision: "e7da7f221ccf5f2856f4331d34c2d0e82aa2a986"
    type: "space"  # ❌ Only "model" or "dataset"
```

Error: `Input should be 'model' or 'dataset' [type=literal_error, input_value='space', input_type=str]`
</details>

<details>
<summary>Extra fields not allowed</summary>

```yaml
metadata:
  version: "1.0"
models:
  - repository: "gpt2"
    revision: "e7da7f221ccf5f2856f4331d34c2d0e82aa2a986"
    type: "model"
    custom_field: "value"  # ❌ Unknown field
```

Error: `Extra inputs are not permitted [type=extra_forbidden, input_value='value', input_type=str]`
</details>

### Finding Revision Hashes

Users can find revision hashes in several ways:

**1. Hugging Face Hub Web UI:**
- Navigate to the model page (e.g., https://huggingface.co/gpt2)
- Click on "Files and versions" or the commit history
- Click on a specific commit to see the full 40-character hash
- Copy the hash from the URL or commit details

**2. Using `huggingface_hub` Python library:**

```python
from huggingface_hub import model_info

info = model_info("microsoft/deberta-v3-base")
print(info.sha)  # Current revision (main branch)
```

**3. Using Git directly:**

```bash
git clone https://huggingface.co/microsoft/deberta-v3-base
cd deberta-v3-base
git log  # View commit history
```

### Example Lockfiles

<details>
<summary>Minimal example</summary>

```yaml
metadata:
  version: "1.0"
models:
  - repository: "gpt2"
    revision: "e7da7f221ccf5f2856f4331d34c2d0e82aa2a986"
    type: "model"
```
</details>

<details>
<summary>With file filtering for security</summary>

```yaml
metadata:
  version: "1.0"
models:
  - repository: "microsoft/deberta-v3-base"
    revision: "559062ad13d311b87b2c455e67dcd5f1c8f65111"
    type: "model"
    include_patterns:
      - "*.safetensors"  # Only safe format
      - "config.json"
      - "tokenizer*"
      - "vocab.txt"
```
</details>

<details>
<summary>Multiple models and datasets</summary>

```yaml
metadata:
  version: "1.0"
models:
  # Encoder model
  - repository: "sentence-transformers/all-MiniLM-L6-v2"
    revision: "8b3219a92973c328a8e22fadcfa821b5dc75636a"
    type: "model"
    include_patterns:
      - "*.safetensors"
      - "config.json"
      - "tokenizer*"

  # Decoder model
  - repository: "gpt2"
    revision: "e7da7f221ccf5f2856f4331d34c2d0e82aa2a986"
    type: "model"
    include_patterns:
      - "*.safetensors"
      - "*.json"

  # Training dataset
  - repository: "squad"
    revision: "d6ec3ceb99ca480ce37cdd35555d6cb2511d223b"
    type: "dataset"
```
</details>

<details>
<summary>Framework-specific downloads</summary>

```yaml
metadata:
  version: "1.0"
models:
  # PyTorch only (safetensors)
  - repository: "microsoft/deberta-v3-base"
    revision: "559062ad13d311b87b2c455e67dcd5f1c8f65111"
    type: "model"
    include_patterns:
      - "*.safetensors"
      - "config.json"
      - "tokenizer*"

  # TensorFlow only
  - repository: "bert-base-uncased"
    revision: "86b5e0934494bd15c9632b12f734a8a67f723594"
    type: "model"
    include_patterns:
      - "tf_model.h5"
      - "config.json"
      - "tokenizer*"
```
</details>

## V. Implementation Details

### High-Level Architecture

```
┌──────────────────────────────────────────────────────────────┐
│  hermeto fetch-deps x-huggingface                            │
└──────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌──────────────────────────────────────────────────────────────┐
│  1. Load and validate huggingface.lock.yaml                  │
│     - Parse YAML                                             │
│     - Validate with Pydantic models                          │
│     - Reject on validation errors                            │
└──────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌──────────────────────────────────────────────────────────────┐
│  2. For each model/dataset entry:                            │
│     a. Fetch repository metadata from HF Hub API             │
│     b. Filter files based on include_patterns                │
│     c. Download files via huggingface_hub                    │
│     d. Organize into cache structure                         │
│     e. Generate SBOM component                               │
└──────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌──────────────────────────────────────────────────────────────┐
│  3. Create cache structure:                                  │
│     hermeto-output/deps/huggingface/hub/                     │
│     └── models--namespace--name/                             │
│         ├── blobs/                                           │
│         ├── refs/                                            │
│         └── snapshots/                                       │
└──────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌──────────────────────────────────────────────────────────────┐
│  4. Generate output:                                         │
│     - SBOM components list                                   │
│     - Environment variables (HF_HUB_OFFLINE=1, etc.)         │
└──────────────────────────────────────────────────────────────┘
```

### Core Components

**`hermeto/core/package_managers/huggingface/`**
- `main.py`: Entry point and orchestration logic
- `models.py`: Pydantic models for lockfile validation
- `cache.py`: Cache structure management

### Fetching Process

#### 1. Lockfile Loading and Validation

```python
def _load_lockfile(lockfile_path: Path) -> HuggingFaceLockfile:
    """Load and validate the Hugging Face lockfile."""
    with open(lockfile_path) as f:
        lockfile_data = yaml.safe_load(f)

    return HuggingFaceLockfile.model_validate(lockfile_data)
```

The lockfile is:
1. Parsed as YAML using `safe_load` (no code execution)
2. Validated against Pydantic models
3. Rejected with helpful error messages on validation failure

#### 2. Repository Metadata Fetching

For each model/dataset entry, fetch metadata from Hugging Face Hub:

```python
if repo_type == "model":
    info = model_info(
        repo_id=repo_id,
        revision=revision,
        files_metadata=True,  # Include file sizes, LFS info
    )
else:  # dataset
    info = dataset_info(
        repo_id=repo_id,
        revision=revision,
        files_metadata=True,
    )
```

The API response includes:
- Repository metadata (name, description, tags)
- List of files (`siblings`) with paths and sizes
- Git LFS metadata (SHA-256 hashes for large files)
- Commit information

#### 3. File Filtering

Files are filtered based on `include_patterns`:

```python
def _should_include_file(filename: str, include_patterns: Optional[list[str]]) -> bool:
    """Check if a file should be included based on patterns."""
    if include_patterns is None:
        return True  # Download everything

    file_path = PurePath(filename)
    for pattern in include_patterns:
        if file_path.match(pattern):
            return True
        # Also try matching with pattern variations for ** edge cases
        if pattern.startswith("**/"):
            simple_pattern = pattern[3:]  # Remove "**/" prefix
            if file_path.match(simple_pattern):
                return True

    return False
```

**Pattern matching examples:**
- `*.safetensors` matches `model.safetensors` but not `subdir/model.safetensors`
- `**/*.safetensors` matches `model.safetensors` AND `subdir/model.safetensors`
- `config.json` matches only `config.json` in root
- `tokenizer*` matches `tokenizer.json`, `tokenizer_config.json`, etc.

#### 4. File Download

Files are downloaded using the `huggingface_hub` library:

```python
downloaded_file = hf_hub_download(
    repo_id=repo_id,
    filename=filename,
    revision=revision,
    repo_type=repo_type,
    cache_dir=temp_path,
    local_dir=temp_path / "download",
)
```

The library handles:
- Git LFS files automatically
- HTTP retries and error handling
- Progress reporting
- Checksum validation (for LFS files)

#### 5. Cache Structure Creation

Downloaded files are organized into the Hugging Face cache structure:

```python
cache_manager.add_file_to_cache(
    repo_cache_dir=repo_cache_dir,
    revision=revision,
    file_path=filename,
    local_file=Path(downloaded_file),
    checksum_info=checksum_info,
)
```

The cache manager:
1. Computes blob hash (SHA-256 of file content)
2. Copies file to `blobs/{hash}`
3. Creates symlink in `snapshots/{revision}/{filename}` → `../../blobs/{hash}`
4. Creates ref in `refs/{ref_name}` → `{revision}`

**Example cache structure:**
```
hermeto-output/deps/huggingface/
├── hub/                                  # Hub cache (models and raw dataset files)
│   ├── models--microsoft--deberta-v3-base/
│   │   ├── blobs/
│   │   │   ├── a1b2c3d4e5f6...  # config.json content
│   │   │   ├── f1e2d3c4b5a6...  # tokenizer.json content
│   │   │   └── 559062ad13d3...  # model.safetensors content
│   │   ├── refs/
│   │   │   └── main  # Contains "559062ad13d311b87b2c455e67dcd5f1c8f65111"
│   │   └── snapshots/
│   │       └── 559062ad13d311b87b2c455e67dcd5f1c8f65111/
│   │           ├── config.json -> ../../blobs/a1b2c3d4e5f6...
│   │           ├── tokenizer.json -> ../../blobs/f1e2d3c4b5a6...
│   │           └── model.safetensors -> ../../blobs/559062ad13d3...
│   └── datasets--garage-bAInd--open-platypus/
│       ├── blobs/
│       │   ├── 1a2b3c4d5e6f...  # README.md content
│       │   └── 7f8e9d0c1b2a...  # data.parquet content
│       ├── refs/
│       │   └── main  # Contains dataset revision hash
│       └── snapshots/
│           └── abc123def456.../
│               ├── README.md -> ../../blobs/1a2b3c4d5e6f...
│               └── data.parquet -> ../../blobs/7f8e9d0c1b2a...
└── datasets/                             # Datasets cache (processed Arrow files)
    └── garage-bAInd___open-platypus/
        └── default/
            └── 0.0.0/
                ├── dataset_info.json
                └── open-platypus-train.arrow  # Processed Arrow format
```

**Note on datasets caching:**
For dataset-type entries, Hermeto not only downloads the raw files to the hub cache
but also loads the dataset using the `datasets` library. This populates the Arrow
cache with processed files, enabling seamless offline usage. The datasets library
requires these pre-processed Arrow files to work in offline mode (with `HF_HUB_OFFLINE=1`).

### SBOM Generation

For each model/dataset, generate an SBOM component using the [Hugging Face PURL specification](https://github.com/package-url/purl-spec/blob/main/types-doc/huggingface-definition.md):

```python
purl = PackageURL(
    type="huggingface",
    namespace=namespace,  # e.g., "microsoft"
    name=name,            # e.g., "deberta-v3-base"
    version=revision.lower(),  # PURL spec requires lowercase
).to_string()

component = Component(
    name=repository,  # Full repository path
    version=revision,
    purl=purl,
    type="library",
    external_references=[
        ExternalReference(
            url=f"https://huggingface.co/{repository}",
            type="distribution",
        )
    ],
)
```

**Example PURLs:**
- `pkg:huggingface/gpt2@e7da7f221ccf5f2856f4331d34c2d0e82aa2a986`
- `pkg:huggingface/microsoft/deberta-v3-base@559062ad13d311b87b2c455e67dcd5f1c8f65111`
- `pkg:huggingface/squad@d6ec3ceb99ca480ce37cdd35555d6cb2511d223b?type=dataset`

### Environment Variables

Hermeto generates environment variables for hermetic builds:

```bash
HF_HOME=${output_dir}/deps/huggingface
HF_HUB_CACHE=${output_dir}/deps/huggingface/hub
HF_DATASETS_CACHE=${output_dir}/deps/huggingface/datasets
HF_HUB_OFFLINE=1
HUGGINGFACE_HUB_CACHE=${output_dir}/deps/huggingface/hub
```

These variables:
- Point Hugging Face libraries to the prefetched cache
- Enable offline mode (`HF_HUB_OFFLINE=1`)
- Work with transformers, datasets, diffusers, and other HF libraries
- **Note**: `HF_DATASETS_CACHE` uses a separate directory from `HF_HUB_CACHE`
  to avoid conflicts between the hub cache (raw files) and datasets cache
  (processed Arrow files)

### Error Handling

The implementation includes detailed error messages for common issues:

<details>
<summary>Repository not found</summary>

```
Repository 'microsoft/deberta-v3-base' not found on Hugging Face Hub at revision 559062ad13d311b87b2c455e67dcd5f1c8f65111

Solution: Check that the repository name is correct and the revision exists. You can verify on https://huggingface.co/
```
</details>

<details>
<summary>Lockfile not found</summary>

```
Hermeto Hugging Face lockfile '/path/to/huggingface.lock.yaml' does not exist

Solution: Make sure your repository has Hermeto Hugging Face lockfile 'huggingface.lock.yaml' checked in, or the supplied lockfile path is correct.
```
</details>

<details>
<summary>Invalid YAML</summary>

```
Hermeto Hugging Face lockfile 'huggingface.lock.yaml' has invalid YAML format: mapping values are not allowed here

Solution: Check correct YAML syntax in the lockfile.
```
</details>

<details>
<summary>No files matched patterns</summary>

```
WARNING: No files matched the include patterns for 'microsoft/deberta-v3-base'. Patterns: ['*.onnx']
```

This is a warning, not an error, to help users debug incorrect patterns.
</details>

## VI. Security Considerations

### Arbitrary Code Execution Risk

**⚠️ CRITICAL SECURITY ISSUE**: PyTorch model files use Python's `pickle` serialization format, which **executes arbitrary code during deserialization**. Malicious actors can embed code in model files that will execute when the model is loaded.

### Important Distinction: When Does the Risk Occur?

**Hermeto itself is NOT at risk during the fetch phase:**
- Hermeto only downloads files via HTTP
- No deserialization or model loading occurs
- Hermeto creates cache directories and symlinks
- No arbitrary code execution can compromise Hermeto

**The user's application IS at risk during model loading:**
- When your code calls `AutoModel.from_pretrained("model-name")`
- The transformers/torch library deserializes pickle files
- Any embedded code in `*.bin`, `*.pt`, `*.pkl` files executes
- This happens during your build or runtime, NOT during Hermeto's fetch

### Unsafe File Formats

The following formats use pickle serialization and can execute arbitrary code when loaded:

- `*.bin` - PyTorch weights in pickle format
- `*.pt` - PyTorch checkpoint format
- `*.pth` - PyTorch checkpoint format
- `*.pkl` - Raw pickle files
- `*.pickle` - Raw pickle files
- `modeling_*.py` - Custom model code imported by transformers library

### Safe File Formats

The following formats are safe and do not execute code:

- `*.safetensors` - **Strongly recommended** - Safe serialization format created specifically to avoid pickle risks
- `*.json` - Configuration and metadata
- `*.txt` - Vocabulary and text files
- `*.h5` - TensorFlow format (uses HDF5, not pickle)
- `*.msgpack` - JAX/Flax format (MessagePack serialization)

### Warning System

Hermeto implements warnings to help users avoid unsafe formats:

**1. No include patterns specified:**
```
WARNING: Model 'microsoft/deberta-v3-base' has no include_patterns specified. This will download ALL files including potentially unsafe formats (*.bin, *.pt, *.pkl) that execute arbitrary code when YOUR application loads them (not during Hermeto's fetch). Consider restricting to safe formats like *.safetensors
```

**2. Unsafe patterns explicitly included:**
```
WARNING: Model 'microsoft/deberta-v3-base' includes potentially unsafe patterns: ['*.bin', '*.pt']. These file formats use pickle serialization which executes arbitrary code when YOUR application loads the model (not during Hermeto's fetch). Consider using SafeTensors format (*.safetensors) instead.
```

### Recommendations

**For production use:**
1. **Always prefer SafeTensors** format when available:
   ```yaml
   include_patterns:
     - "*.safetensors"  # Safe format
     - "config.json"
     - "tokenizer*"
   ```

2. **Audit models before use**: Review the model's Hugging Face page, check for community trust signals, verify the model author

3. **Use trusted sources**: Prefer official model releases from reputable organizations

4. **Pin exact revisions**: Always use commit hashes, never "main" or floating versions

5. **Review file lists**: Check what files will be downloaded before running fetch

**If you must use pickle formats:**
1. Download from highly trusted sources only
2. Scan with security tools before loading
3. Run model loading in sandboxed environments
4. Document the security decision and risk acceptance

### SafeTensors: The Safe Alternative

[SafeTensors](https://github.com/huggingface/safetensors) is a format developed specifically to address pickle security issues:

- **No code execution**: Pure tensor data, no Python code
- **Fast loading**: Faster than pickle in many cases
- **Framework agnostic**: Works with PyTorch, TensorFlow, JAX
- **Growing adoption**: Many models now provide SafeTensors weights

**Converting models to SafeTensors:**

Many models on Hugging Face Hub now include both pickle and SafeTensors formats. When both are available, use SafeTensors:

```yaml
models:
  - repository: "microsoft/deberta-v3-base"
    revision: "559062ad13d311b87b2c455e67dcd5f1c8f65111"
    type: "model"
    include_patterns:
      - "*.safetensors"  # Use SafeTensors, skip *.bin files
      - "config.json"
      - "tokenizer*"
```

If a model only has pickle weights, you can convert them locally using the SafeTensors library before uploading to your own Hugging Face repository.

### Network Isolation During Fetch

While Hermeto's fetch phase requires network access to download models, the **build phase** should run in a network-isolated environment:

```bash
# Fetch phase (requires network)
hermeto fetch-deps x-huggingface

# Build phase (network isolated)
podman build . \
  --volume "$(realpath ./hermeto-output)":/tmp/hermeto-output:Z \
  --network none \  # ← Network isolation
  --tag my-ml-app
```

This ensures:
- No unexpected network calls during build
- All dependencies come from the prefetched cache
- Build is reproducible and hermetic

## Appendices

### Appendix A: Hugging Face PURL Specification

Hermeto uses the [Hugging Face PURL specification](https://github.com/package-url/purl-spec/blob/main/types-doc/huggingface-definition.md) for SBOM component identification.

**Format:**
```
pkg:huggingface[/NAMESPACE]/NAME[@VERSION][?type=TYPE]
```

**Components:**
- `type`: Always `"huggingface"`
- `namespace`: Optional, the organization or user (e.g., `"microsoft"`, `"openai"`)
- `name`: The model or dataset name (e.g., `"deberta-v3-base"`, `"gpt2"`)
- `version`: The Git commit hash (lowercase per PURL spec)
- `type` qualifier: Optional, `"dataset"` for datasets (omitted for models)

**Examples:**

Models without namespace:
```
pkg:huggingface/gpt2@e7da7f221ccf5f2856f4331d34c2d0e82aa2a986
```

Models with namespace:
```
pkg:huggingface/microsoft/deberta-v3-base@559062ad13d311b87b2c455e67dcd5f1c8f65111
```

Datasets:
```
pkg:huggingface/squad@d6ec3ceb99ca480ce37cdd35555d6cb2511d223b?type=dataset
```

**Mapping from lockfile to PURL:**

| Lockfile Field | PURL Component | Example |
|---------------|----------------|---------|
| `repository: "gpt2"` | `name: "gpt2"`, `namespace: null` | `pkg:huggingface/gpt2@...` |
| `repository: "microsoft/deberta"` | `namespace: "microsoft"`, `name: "deberta"` | `pkg:huggingface/microsoft/deberta@...` |
| `revision: "559062..."` | `version: "559062..."` (lowercase) | `@559062...` |
| `type: "dataset"` | `type` qualifier | `?type=dataset` |

### Appendix B: Future Ecosystem Evolution and Deprecation Considerations

**When should this lockfile format be deprecated?**

This custom lockfile format should be deprecated when any of the following emerge:

1. **Official Hugging Face dependency declaration standard**
   - If Hugging Face releases an official format for declaring model dependencies
   - Example: `hf-requirements.txt`, `huggingface.toml`, or extensions to `transformers` config

2. **Python ecosystem standard for ML dependencies**
   - If `pyproject.toml` gains ML-specific sections
   - If Poetry, PDM, or other tools add Hugging Face dependency support
   - Example: PEP proposal for ML artifact dependencies

3. **Framework-specific standards**
   - LangChain, LlamaIndex, or other frameworks define standard dependency formats
   - Currently these frameworks use only programmatic configuration (see Section III)
   - If they develop and the community adopts declarative dependency formats

4. **Industry-wide standards**
   - CISA SBOM-for-AI effort produces standard formats
   - CycloneDX or SPDX create de facto conventions that tools adopt

**Migration path:**

When deprecating, Hermeto should:
1. Add support for the new standard alongside the legacy format
2. Announce deprecation with clear timeline (e.g., 6 months)
3. Provide migration tooling: `hermeto convert huggingface.lock.yaml --to pyproject.toml`
4. Update documentation with migration guide
5. Eventually remove support after deprecation period

**Signals to watch:**

Monitor these resources for ecosystem evolution:
- Hugging Face Hub product announcements
- Python packaging PEPs and discussions
- CISA SBOM-for-AI tiger team publications
- ML framework changelogs and announcements:
  - Hugging Face (transformers, diffusers, datasets)
  - LangChain and LangServe
  - LlamaIndex
- Community discussions on Twitter, Reddit, HN about ML dependency management

### Appendix C: Potential Future Enhancements

**1. Private Model Support**

Currently, Hermeto uses unauthenticated Hugging Face Hub API access. Private models require authentication.

**Possible approaches:**
- Accept HF token via environment variable: `HF_TOKEN`
- Read token from `~/.huggingface/token` (standard HF location)
- Add `token` field to lockfile (not recommended for security)

**Implementation:**
```python
from huggingface_hub import login

login(token=os.environ.get("HF_TOKEN"))
```

**2. Automatic Lockfile Generation**

Currently users must manually create `huggingface.lock.yaml`. A helper tool could scan code for model references:

```bash
hermeto generate-hf-lockfile --scan ./src --output huggingface.lock.yaml
```

**Challenges:**
- Models referenced as runtime variables: `model = load_model(config["model_name"])`
- Dynamic model selection based on environment
- False positives from string literals

**Better approach:** Interactive tool that prompts users:
```bash
hermeto generate-hf-lockfile --interactive
> Which models does your project use?
> Enter repository name (or 'done'): microsoft/deberta-v3-base
> Use latest revision? [Y/n]: y
> Fetching latest revision... 559062ad13d311b87b2c455e67dcd5f1c8f65111
> Include patterns (comma-separated, or 'all'): *.safetensors,config.json,tokenizer*
> Enter repository name (or 'done'): done
> Writing huggingface.lock.yaml...
```

**3. Lockfile Validation Command**

Check if lockfile is up-to-date with latest model revisions:

```bash
hermeto validate-hf-lockfile --check-updates
```

Output:
```
✓ gpt2@e7da7f2... is current (main branch)
⚠ microsoft/deberta-v3-base@559062a... outdated
  Latest revision: 8a9b1c2d... (main branch)
  Your revision: 559062ad... (2 commits behind)
```

**4. Dataset Filtering**

Datasets can be very large. Add support for:
- Downloading specific splits: `split: "train"`, `split: "test"`
- Row/sample limits: `max_samples: 1000`
- Column filtering: `columns: ["text", "label"]`

**5. Model Quantization Support**

Support for quantized models (GGUF, GPTQ, AWQ formats):
```yaml
models:
  - repository: "TheBloke/Llama-2-7B-GGUF"
    revision: "abc123..."
    type: "model"
    include_patterns:
      - "llama-2-7b.Q4_K_M.gguf"  # 4-bit quantized
```

**6. Multi-lockfile Support**

For monorepos with multiple ML applications:
```bash
hermeto fetch-deps x-huggingface --lockfile ml-service-1/hf.lock.yaml
hermeto fetch-deps x-huggingface --lockfile ml-service-2/hf.lock.yaml
```

Or a single lockfile with namespaces:
```yaml
metadata:
  version: "1.0"

services:
  ml-service-1:
    models:
      - repository: "gpt2"
        revision: "..."

  ml-service-2:
    models:
      - repository: "bert-base-uncased"
        revision: "..."
```

**7. Parallel Downloads**

Speed up fetching by downloading multiple files concurrently:
```python
import asyncio
from concurrent.futures import ThreadPoolExecutor

async def download_files(files, max_workers=4):
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        loop = asyncio.get_event_loop()
        tasks = [
            loop.run_in_executor(executor, download_file, file)
            for file in files
        ]
        await asyncio.gather(*tasks)
```

### Appendix D: Related Work and Alternatives

**MLflow Model Registry:**
- Supports model versioning and metadata
- Requires MLflow server infrastructure
- Not focused on hermetic builds
- No native Hugging Face Hub integration

**DVC (Data Version Control):**
- Git-like versioning for datasets and models
- Requires setting up remote storage (S3, GCS, etc.)
- More complex setup than Hermeto's approach
- Good for ML experiment tracking, less focused on build hermeticity

**Hugging Face Hub CLI:**
- Can download models: `huggingface-cli download model-name`
- No built-in lockfile or versioning concept
- Doesn't generate SBOMs
- Hermeto wraps this functionality with reproducibility guarantees

**Manual wget/curl:**
- Direct downloads from HF CDN
- No cache structure creation
- Requires knowing exact URLs
- No SBOM generation
- Error-prone and not reproducible

**Why Hermeto's approach:**
- **Integrated with existing SBOM tooling**: Works with Hermeto's existing architecture
- **Reproducible**: Lockfile + commit hashes ensure consistency
- **Hermetic**: Compatible with network-isolated builds
- **Auditable**: SBOM tracks all ML artifacts
- **Simple**: YAML lockfile, no infrastructure required

---

## Summary

Hermeto's Hugging Face support enables hermetic builds for ML applications by:

1. **Declaring dependencies** via a custom `huggingface.lock.yaml` lockfile format
2. **Prefetching models and datasets** before the build phase
3. **Creating a cache structure** compatible with Hugging Face offline mode
4. **Generating SBOMs** with proper provenance using Hugging Face PURLs
5. **Warning about security risks** from pickle-based model formats

This implementation is a **bridge solution** to enable hermetic ML builds while the AI/ML ecosystem matures. When community standards emerge for declaring Hugging Face dependencies, this custom format should be deprecated in favor of those standards.

The work contributes to the broader **SBOM-for-AI movement**, bringing ML artifacts into the software supply chain security conversation and enabling organizations to track, audit, and secure their AI dependencies.
