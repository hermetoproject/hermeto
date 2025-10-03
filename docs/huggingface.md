# Hugging Face

⚠️ **EXPERIMENTAL**: This package manager is experimental and not production-ready. See [CONTRIBUTING.md](../CONTRIBUTING.md#experimental-features) for more information.

- [Prerequisites](#prerequisites)
- [Usage](#usage)
- [Lock file format](#lock-file-format)
- [Using fetched dependencies](#using-fetched-dependencies)
- [Hermetic build](#hermetic-build)
- [Limitations](#limitations)

## Support scope

The Hugging Face package manager fetches machine learning models and datasets from [Hugging Face Hub][] to enable hermetic builds. This is useful when building containers that use ML models from Hugging Face but need to work in network-isolated environments.

## Prerequisites

To use Hermeto with Hugging Face locally:
- Ensure you have the `huggingface_hub` Python library available (included in the Hermeto container)
- Have a `huggingface.lock.yaml` file in your project directory

## Usage

Run the following command to prefetch Hugging Face models and datasets:

```bash
cd path-to-your-project
hermeto fetch-deps x-huggingface
```

Note the `x-` prefix indicating this is an experimental package manager.

The default output directory is `hermeto-output`. You can change it by passing the `--output` option:

```bash
hermeto fetch-deps --output /tmp/hermeto-output x-huggingface
```

## Lock file format

The Hugging Face fetcher requires a lockfile named `huggingface.lock.yaml` in your repository. Alternatively, you can specify a custom lockfile path in the JSON input.

### Lockfile structure

The lockfile must contain a `metadata` header and a list of `models` (which can also include datasets):

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
      - "tokenizer.json"
```

### Required fields

Each model/dataset entry requires:

- **`repository`**: Repository identifier in format `"name"` or `"namespace/name"`
  - Examples: `"gpt2"`, `"microsoft/deberta-v3-base"`
- **`revision`**: Git commit hash (40 hexadecimal characters)
  - You can find this on the model's page on huggingface.co or using the `huggingface_hub` library
  - Example: `"e7da7f221ccf5f2856f4331d34c2d0e82aa2a986"`
- **`type`**: Either `"model"` or `"dataset"`

### Optional fields

- **`include_patterns`**: List of glob patterns to filter which files to download
  - If omitted, all files are downloaded
  - Examples: `["*.safetensors", "config.json"]`, `["**/*.json"]`

### Datasets

To fetch datasets instead of models, set `type: "dataset"`:

```yaml
metadata:
  version: "1.0"
models:
  - repository: "squad"
    revision: "d6ec3ceb99ca480ce37cdd35555d6cb2511d223b"
    type: "dataset"
```

### Finding revision hashes

To find the revision (commit hash) for a model:

1. Visit the model page on [huggingface.co][]
2. Click on the commit history
3. Copy the full 40-character commit hash

Or use the `huggingface_hub` library:

```python
from huggingface_hub import model_info

info = model_info("gpt2")
print(info.sha)  # Current commit hash
```

### Custom lockfile path

You can specify a custom lockfile location using JSON input:

```bash
hermeto fetch-deps --source ./my-repo '{"type": "x-huggingface", "lockfile": "/absolute/path/to/custom.yaml"}'
```

Note: The lockfile path must be absolute.

## Using fetched dependencies

Hermeto downloads models and datasets into the `deps/huggingface/hub/` subdirectory of the output directory. The cache structure follows Hugging Face Hub's native format with blobs, snapshots, and refs directories.

This structure enables offline usage with `HF_HUB_OFFLINE=1`:

```bash
export HF_HUB_CACHE=/path/to/hermeto-output/deps/huggingface/hub
export HF_HUB_OFFLINE=1

python your_script.py
```

Or in Python code:

```python
import os
os.environ["HF_HUB_CACHE"] = "/path/to/hermeto-output/deps/huggingface/hub"
os.environ["HF_HUB_OFFLINE"] = "1"

from transformers import AutoModel
model = AutoModel.from_pretrained("gpt2")
```

## Hermetic build

After using `fetch-deps` to download your models, you can build your project hermetically in a network-isolated container.

### Example Dockerfile

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install your dependencies (huggingface libraries)
COPY requirements.txt .
RUN pip install -r requirements.txt

# Set Hugging Face cache environment variables
ENV HF_HUB_CACHE=/tmp/hermeto-output/deps/huggingface/hub
ENV HF_HUB_OFFLINE=1

# Copy your application code
COPY . .

CMD ["python", "app.py"]
```

### Building the container

Mount the hermeto output directory during the build:

```bash
podman build . \
  --volume "$(realpath ./hermeto-output)":/tmp/hermeto-output:Z \
  --network none \
  --tag my-ml-app
```

The `--network none` flag ensures the build is truly hermetic.

### Example application code

```python
from transformers import AutoModel, AutoTokenizer

# These will load from the hermeto cache without network access
model = AutoModel.from_pretrained("gpt2")
tokenizer = AutoTokenizer.from_pretrained("gpt2")

# Use the model...
```

## Limitations

### Security: Arbitrary code execution risk

⚠️ **CRITICAL SECURITY WARNING**: PyTorch model files (`*.bin`, `*.pt`, `*.pkl`) use Python's pickle serialization format, which **executes arbitrary code during deserialization**. Malicious actors can embed code in these files that will execute when you load the model with `AutoModel.from_pretrained()` or similar functions.

**Important distinction:**
- ✅ **Hermeto itself is NOT at risk** - During the fetch phase, Hermeto only downloads files via HTTP and creates cache directories. No model loading or deserialization occurs, so Hermeto cannot be compromised.
- ⚠️ **Your application IS at risk** - The arbitrary code execution happens when YOUR code loads the downloaded models (e.g., `AutoModel.from_pretrained()`). This occurs during your build or runtime, not during Hermeto's fetch.

**Safe formats:**
- `*.safetensors` - SafeTensors format (no code execution possible, **strongly recommended**)
- `*.json` - Configuration files
- `*.txt` - Vocabulary files

**Unsafe formats (arbitrary code execution risk):**
- `*.bin` - PyTorch pickle format
- `*.pt` - PyTorch pickle format
- `*.pkl` - Python pickle format
- `modeling_*.py` - Custom model code (imported and executed by transformers library)

**Recommendation:** Always use `include_patterns` to download only SafeTensors weights:

```yaml
models:
  - repository: "gpt2"
    revision: "e7da7f221ccf5f2856f4331d34c2d0e82aa2a986"
    type: "model"
    include_patterns:
      - "*.safetensors"  # Safe format - no code execution
      - "config.json"
      - "tokenizer*"
      - "vocab.txt"
```

Hermeto will warn you if your lockfile allows downloading potentially unsafe file formats.

### Framework-specific files

By default, Hermeto downloads all files for a model. Models often include weights for multiple frameworks (PyTorch, TensorFlow, JAX, etc.). Use `include_patterns` to download only what you need:

```yaml
models:
  - repository: "gpt2"
    revision: "e7da7f221ccf5f2856f4331d34c2d0e82aa2a986"
    type: "model"
    include_patterns:
      - "*.safetensors"  # SafeTensors format (recommended)
      - "config.json"
      - "tokenizer*"
```

### Large models

Very large models (>10GB) use Git LFS and may take significant time to download. Consider:
- Using smaller model variants for development
- Downloading during CI/CD pipeline execution
- Using include patterns to download only required files

### Model cards and documentation

By default, README files and model cards are downloaded. If you want to exclude them to save space:

```yaml
include_patterns:
  - "*.safetensors"
  - "config.json"
  - "tokenizer*"
  # README.md is not matched
```

### Revision pinning

Always use explicit commit hashes (revisions) rather than branch names or tags. This ensures reproducibility:

❌ **Bad** (not supported):
```yaml
revision: "main"  # This won't work
```

✅ **Good**:
```yaml
revision: "e7da7f221ccf5f2856f4331d34c2d0e82aa2a986"  # Exact commit hash
```

### Private models

Currently, Hermeto uses the public Hugging Face Hub API. For private models:
- Download them manually first
- Use hermeto's [generic fetcher][] to include them in your build

## SBOM

Hermeto generates an SBOM (Software Bill of Materials) for all fetched models and datasets using the [Hugging Face PURL specification][]:

```
pkg:huggingface/microsoft/deberta-v3-base@559062ad13d311b87b2c455e67dcd5f1c8f65111
```

The PURL includes:
- Type: `huggingface`
- Namespace: Organization/user (if applicable)
- Name: Model/dataset name
- Version: Git commit hash (lowercase per PURL spec)

[Hugging Face Hub]: https://huggingface.co
[huggingface.co]: https://huggingface.co
[generic fetcher]: generic.md
[Hugging Face PURL specification]: https://github.com/package-url/purl-spec/blob/main/types-doc/huggingface-definition.md
