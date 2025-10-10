# DVC (Data Version Control)

- [Overview](#overview)
- [How it works](#how-it-works)
- [Prerequisites](#prerequisites)
- [Using DVC with Hermeto](#using-dvc-with-hermeto)
- [Hermetic build workflow](#hermetic-build-workflow)
- [SBOM generation](#sbom-generation)
- [Limitations](#limitations)
- [Example](#example)

## Overview

The `x-dvc` package manager enables hermetic builds for projects that use
[DVC (Data Version Control)](https://dvc.org) to track ML models, datasets, and
other large files as external dependencies.

**Status**: Experimental (x-dvc)

## How it works

DVC tracks external dependencies (models, datasets, etc.) in a `dvc.lock` file
that contains URLs and checksums for all dependencies. Hermeto:

1. Reads `dvc.lock` to understand what dependencies exist
2. Runs `dvc fetch` to download all dependencies into a cache
3. Generates SBOM with components for each dependency
4. Outputs `DVC_CACHE_DIR` environment variable for the hermetic build

During the hermetic build, your build process sets `DVC_CACHE_DIR` and runs
`dvc pull` to checkout files from the pre-populated cache (no network needed).

## Prerequisites

- **DVC must be installed** in the environment where hermeto runs
- **dvc.lock file** must be present in your repository
- **Git repository** context is required (DVC relies on git)
- Dependencies should be **external URLs** (http, https, s3, gs, etc.)

## Using DVC with Hermeto

### 1. Setting up DVC in your project

First, add DVC to your project and track some external dependencies:

```shell
# Initialize DVC
cd my-ml-project
dvc init

# Track a HuggingFace model
dvc import-url \
  https://huggingface.co/gpt2/resolve/e7da7f221ccf5f2856f4331d34c2d0e82aa2a986/pytorch_model.bin \
  models/gpt2/pytorch_model.bin

# Track a dataset from S3
dvc import-url \
  s3://mybucket/data/train.csv \
  data/train.csv

# Commit dvc.lock to your repository
git add dvc.lock .dvc/
git commit -m "Add DVC dependencies"
```

### 2. Running Hermeto

Run hermeto to fetch all DVC dependencies:

```shell
hermeto fetch-deps \
  --source ./my-ml-project \
  --output ./hermeto-output \
  x-dvc
```

Alternatively, using JSON input:

```shell
hermeto fetch-deps \
  --source ./my-ml-project \
  --output ./hermeto-output \
  '{"type": "x-dvc", "path": "."}'
```

### 3. Understanding the output

Hermeto creates the following structure:

```
hermeto-output/
├── deps/
│   └── dvc/
│       └── cache/         # DVC cache with all downloaded files
└── bom.json               # SBOM with dependency metadata
```

The SBOM includes environment variables:

```json
{
  "environmentVariables": [
    {
      "name": "DVC_CACHE_DIR",
      "value": "${output_dir}/deps/dvc/cache"
    }
  ]
}
```

## Hermetic build workflow

### Phase 1: Fetch dependencies (with network)

```shell
# Run hermeto to populate DVC cache
hermeto fetch-deps \
  --source ./my-ml-project \
  --output ./hermeto-output \
  x-dvc
```

### Phase 2: Hermetic build (no network)

In your Dockerfile or build script:

```dockerfile
FROM python:3.11

WORKDIR /workspace

# Copy source code
COPY . /workspace/

# Mount hermeto output (contains DVC cache)
# In practice, this is mounted at build time:
#   podman build --volume ./hermeto-output:/tmp/hermeto-output:Z

# Set DVC_CACHE_DIR environment variable
ENV DVC_CACHE_DIR=/tmp/hermeto-output/deps/dvc/cache

# Pull files from cache (no network needed!)
RUN dvc pull

# Now your models/datasets are available
RUN python train.py
```

Build command:

```shell
podman build . \
  --volume "$(realpath ./hermeto-output)":/tmp/hermeto-output:Z \
  --network none \
  --tag my-ml-app
```

## SBOM generation

Hermeto generates SBOM components based on dependency URLs:

### HuggingFace models/datasets

URLs like `https://huggingface.co/{repo}/resolve/{revision}/{file}` are
recognized and generate components with `pkg:huggingface` PURL:

```json
{
  "type": "library",
  "name": "gpt2",
  "version": "e7da7f221ccf5f2856f4331d34c2d0e82aa2a986",
  "purl": "pkg:huggingface/gpt2@e7da7f221ccf5f2856f4331d34c2d0e82aa2a986"
}
```

For namespaced repos:

```json
{
  "type": "library",
  "name": "microsoft/deberta-v3-base",
  "version": "559062ad13d311b87b2c455e67dcd5f1c8f65111",
  "purl": "pkg:huggingface/microsoft/deberta-v3-base@559062ad13d311b87b2c455e67dcd5f1c8f65111"
}
```

### Other URLs (S3, HTTP, etc.)

All other external URLs generate generic components:

```json
{
  "type": "library",
  "name": "train.csv",
  "version": "abc12345",
  "purl": "pkg:generic/train.csv?checksum=md5:abc123...&download_url=s3://..."
}
```

### Local files

Local file dependencies in `dvc.lock` are **skipped** - they're already in
your repository and don't need to be fetched or recorded in the SBOM.

## Checksum validation

Hermeto validates that all external dependencies have checksums in `dvc.lock`:

- **Strict mode (default)**: Errors if any external dependency lacks a checksum
- **Permissive mode**: Warns but continues

```shell
# Permissive mode
hermeto --mode=permissive fetch-deps --source . --output ./output x-dvc
```

## Limitations

### Supported dependency types

The `x-dvc` package manager currently supports:

- ✅ External URLs (http, https, s3, gs, azure, etc.)
- ✅ HuggingFace Hub URLs (special handling)
- ❌ DVC remote configurations (must use direct URLs)
- ❌ SSH-based URLs (may work but untested)

### DVC features

- ✅ `dvc import-url` - Fully supported
- ✅ Schema 2.0+ lockfiles
- ❌ Pipeline execution - Hermeto never runs pipelines
- ❌ DVC remotes - Use direct URLs instead

### Best practices

1. **Use `dvc import-url`** for external dependencies rather than `dvc add`
2. **Commit dvc.lock** to your repository
3. **Use absolute URLs** rather than DVC remote shortcuts
4. **Specify checksums** explicitly when possible

## Example

Let's build a containerized ML application that uses a HuggingFace model.

### 1. Project setup

```shell
# Create project
mkdir my-ml-app
cd my-ml-app
git init

# Initialize DVC
dvc init

# Add a sentiment analysis model from HuggingFace
dvc import-url \
  https://huggingface.co/distilbert-base-uncased/resolve/9c169103d7e5a73936dd2b732b1d19f49f99cd63/pytorch_model.bin \
  models/sentiment/pytorch_model.bin

dvc import-url \
  https://huggingface.co/distilbert-base-uncased/resolve/9c169103d7e5a73936dd2b732b1d19f49f99cd63/config.json \
  models/sentiment/config.json

# Commit
git add .
git commit -m "Add sentiment model"
```

### 2. Create application

`app.py`:

```python
from transformers import AutoModelForSequenceClassification
import os

# Model is available after dvc pull
model_path = "./models/sentiment"
model = AutoModelForSequenceClassification.from_pretrained(model_path)

print(f"Model loaded: {model.config.model_type}")
```

### 3. Fetch dependencies with Hermeto

```shell
hermeto fetch-deps \
  --source ./my-ml-app \
  --output ./hermeto-output \
  x-dvc
```

### 4. Create Dockerfile

```dockerfile
FROM python:3.11-slim

# Install DVC
RUN pip install dvc transformers torch

WORKDIR /app

# Copy application code
COPY . /app/

# DVC cache will be mounted here
ENV DVC_CACHE_DIR=/tmp/hermeto-output/deps/dvc/cache

# Pull models from cache (hermetic - no network!)
RUN dvc pull

# Run application
CMD ["python", "app.py"]
```

### 5. Build hermetically

```shell
podman build . \
  --volume "$(realpath ./hermeto-output)":/tmp/hermeto-output:Z \
  --network none \
  --tag sentiment-app

# Run it
podman run sentiment-app
```

Output:

```
Model loaded: distilbert
```

The build succeeds without network access because DVC pulls from the
pre-populated cache!

## Troubleshooting

### "dvc command not found"

Make sure DVC is installed:

```shell
pip install dvc
```

### "dvc.lock does not exist"

Your repository needs a `dvc.lock` file. Create it with:

```shell
dvc commit  # or dvc repro, or manually track files with dvc import-url
```

### "dvc fetch failed: No remote configured"

This likely means your `dvc.lock` references a DVC remote instead of direct
URLs. The `x-dvc` package manager requires dependencies with direct URLs.

Use `dvc import-url` instead of `dvc add` for external dependencies.

### Build fails with "unable to find data"

Make sure you're setting `DVC_CACHE_DIR` and running `dvc pull` in your build:

```dockerfile
ENV DVC_CACHE_DIR=/tmp/hermeto-output/deps/dvc/cache
RUN dvc pull
```

## Further reading

- [DVC documentation](https://dvc.org/doc)
- [DVC import-url command](https://dvc.org/doc/command-reference/import-url)
- [HuggingFace models](https://huggingface.co/models)
