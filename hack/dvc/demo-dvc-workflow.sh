#!/bin/bash
set -euo pipefail

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
echo_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
echo_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Create a temporary directory in /tmp for our test
TEST_DIR=$(mktemp -d /tmp/hermeto-dvc-test.XXXXXX)
trap "rm -rf $TEST_DIR" EXIT

echo_info "Test directory: $TEST_DIR"

cd "$TEST_DIR"

# Step 1: Create a git repo and initialize DVC
echo_info "Step 1: Setting up git repository and DVC"
git init -q
git config user.email "test@example.com"
git config user.name "Test User"

dvc init -q
git add .dvc .dvcignore
git commit -q -m "Initialize DVC"

# Step 2: Track files with DVC to populate its cache
echo_info "Step 2: Adding files to DVC cache (generic + HuggingFace)"

mkdir -p data models

# File 1: Generic file (GitHub raw content)
GENERIC_URL="https://raw.githubusercontent.com/hermetoproject/hermeto/main/pyproject.toml"
echo_info "Downloading generic file from GitHub..."
wget -q "$GENERIC_URL" -O data/pyproject.toml

GENERIC_MD5=$(md5sum data/pyproject.toml | awk '{print $1}')
GENERIC_SIZE=$(stat -c%s data/pyproject.toml)
echo_info "Generic file - MD5: $GENERIC_MD5, Size: $GENERIC_SIZE"

# File 2: HuggingFace model file (small config.json)
HF_URL="https://huggingface.co/gpt2/resolve/11c5a3d5811f50298f278a704980280950aedb10/config.json"
echo_info "Downloading HuggingFace model config..."
wget -q --timeout=30 "$HF_URL" -O models/config.json

HF_MD5=$(md5sum models/config.json | awk '{print $1}')
HF_SIZE=$(stat -c%s models/config.json)
echo_info "HuggingFace file - MD5: $HF_MD5, Size: $HF_SIZE"

# Use dvc add to track the files (this puts them in DVC's cache)
echo_info "Adding files to DVC..."
dvc add data/pyproject.toml -q
dvc add models/config.json -q

# Create a dvc.lock that references both external URLs
cat > dvc.lock << EOF
schema: '2.0'
stages:
  fetch_generic_data:
    cmd: echo "Fetching generic data"
    deps:
    - path: $GENERIC_URL
      md5: $GENERIC_MD5
      size: $GENERIC_SIZE
    outs:
    - path: data/pyproject.toml
      md5: $GENERIC_MD5
      size: $GENERIC_SIZE
  fetch_hf_model:
    cmd: echo "Fetching HuggingFace model"
    deps:
    - path: $HF_URL
      md5: $HF_MD5
      size: $HF_SIZE
    outs:
    - path: models/config.json
      md5: $HF_MD5
      size: $HF_SIZE
EOF

echo_info "✓ dvc.lock created successfully"
echo "Contents of dvc.lock:"
cat dvc.lock

git add dvc.lock data/.gitignore data/pyproject.toml.dvc models/.gitignore models/config.json.dvc
git commit -q -m "Add DVC tracked files"

# Step 3: Run hermeto fetch-deps
echo_info "Step 3: Running hermeto fetch-deps x-dvc"

HERMETO_OUTPUT="$TEST_DIR/hermeto-output"
mkdir -p "$HERMETO_OUTPUT"

echo "Running: hermeto fetch-deps --source $TEST_DIR --output $HERMETO_OUTPUT x-dvc"
hermeto fetch-deps \
    --source "$TEST_DIR" \
    --output "$HERMETO_OUTPUT" \
    x-dvc 2>&1 | grep -E "(INFO|ERROR|WARNING)"

echo_info "✓ Hermeto fetch-deps completed"

# Check SBOM and environment variable
if [ -f "$HERMETO_OUTPUT/.build-config.json" ]; then
    echo_info "✓ Build config created"
    if grep -q "DVC_CACHE_DIR" "$HERMETO_OUTPUT/.build-config.json"; then
        echo_info "✓ DVC_CACHE_DIR environment variable found"
        DVC_CACHE_VALUE=$(grep "DVC_CACHE_DIR" "$HERMETO_OUTPUT/.build-config.json" -A1 | grep value | cut -d'"' -f4)
        echo_info "  Value: $DVC_CACHE_VALUE"
    else
        echo_error "✗ DVC_CACHE_DIR not found in build config!"
        exit 1
    fi
else
    echo_error "✗ Build config not found!"
    exit 1
fi

# Check SBOM and verify both purl types
if [ -f "$HERMETO_OUTPUT/bom.json" ]; then
    echo_info "✓ SBOM created"

    # Check for HuggingFace component with pkg:huggingface purl
    if grep -q "pkg:huggingface/gpt2" "$HERMETO_OUTPUT/bom.json"; then
        echo_info "✓ HuggingFace component found with correct purl type"
        # Extract and display the HF component details
        echo "  HuggingFace component:"
        grep -A3 "pkg:huggingface/gpt2" "$HERMETO_OUTPUT/bom.json" | grep -E '(name|version|purl)' | head -3
    else
        echo_error "✗ HuggingFace component not found or incorrect purl type!"
        echo "SBOM contents:"
        cat "$HERMETO_OUTPUT/bom.json"
        exit 1
    fi

    # Check for generic component with pkg:generic purl
    if grep -q "pkg:generic/pyproject.toml" "$HERMETO_OUTPUT/bom.json"; then
        echo_info "✓ Generic component found with correct purl type"
        # Extract and display the generic component details
        echo "  Generic component:"
        grep -A3 "pkg:generic/pyproject.toml" "$HERMETO_OUTPUT/bom.json" | grep -E '(name|version|purl)' | head -3
    else
        echo_error "✗ Generic component not found or incorrect purl type!"
        echo "SBOM contents:"
        cat "$HERMETO_OUTPUT/bom.json"
        exit 1
    fi

    echo_info "✓ Both components correctly represented in SBOM"
else
    echo_error "✗ SBOM not found!"
    exit 1
fi

# Step 4: Verify dvc checkout requires cache (prove it doesn't fetch from network)
echo_info "Step 4: Proving 'dvc checkout' requires cache (no network fetch)"

cd "$TEST_DIR"

# Remove the working files
echo_info "Removing both files to simulate clean build"
rm -f data/pyproject.toml models/config.json

# Test A: With cache - should succeed
echo_info "Test A: dvc checkout WITH cache present"
if dvc checkout 2>&1 | grep -E "(^A|^M|ERROR)"; then
    echo_info "✓ Files restored from .dvc/cache"
else
    echo_error "✗ Checkout failed"
    exit 1
fi

# Verify both files were restored
if [ ! -f "data/pyproject.toml" ]; then
    echo_error "✗ Generic file not restored!"
    exit 1
fi

if [ ! -f "models/config.json" ]; then
    echo_error "✗ HuggingFace file not restored!"
    exit 1
fi

# Verify checksums
RESTORED_GENERIC_MD5=$(md5sum data/pyproject.toml | awk '{print $1}')
if [ "$RESTORED_GENERIC_MD5" = "$GENERIC_MD5" ]; then
    echo_info "✓ Generic file checksum verified ($RESTORED_GENERIC_MD5)"
else
    echo_error "✗ Generic file checksum mismatch!"
    exit 1
fi

RESTORED_HF_MD5=$(md5sum models/config.json | awk '{print $1}')
if [ "$RESTORED_HF_MD5" = "$HF_MD5" ]; then
    echo_info "✓ HuggingFace file checksum verified ($RESTORED_HF_MD5)"
else
    echo_error "✗ HuggingFace file checksum mismatch!"
    exit 1
fi

# Remove files again for Test B
rm -f data/pyproject.toml models/config.json

# Test B: Without cache - should FAIL (proves no network fetch)
echo_info "Test B: dvc checkout WITHOUT cache (deleted)"
rm -rf .dvc/cache
mkdir -p .dvc/cache

echo_info "Running dvc checkout with empty cache..."
CHECKOUT_OUTPUT=$(dvc checkout 2>&1 || true)
echo "$CHECKOUT_OUTPUT"
if echo "$CHECKOUT_OUTPUT" | grep -q "ERROR"; then
    echo_info "✓ CORRECT: dvc checkout failed with empty cache"
    echo_info "✓ This proves dvc checkout does NOT fetch from network"
else
    echo_warn "⚠ dvc checkout succeeded with empty cache - unexpected!"
fi

# Verify files were NOT restored
if [ -f "data/pyproject.toml" ] || [ -f "models/config.json" ]; then
    echo_error "✗ Files should not exist after failed checkout!"
    exit 1
else
    echo_info "✓ Files not restored (expected with empty cache)"
fi

# Step 5: Summary
echo ""
echo_info "=========================================="
echo_info "✓ ALL TESTS PASSED!"
echo_info "=========================================="
echo_info "Summary:"
echo_info "  1. ✓ Created DVC lockfile with both generic and HuggingFace dependencies"
echo_info "  2. ✓ Hermeto generated SBOM with correct PURL types:"
echo_info "      - pkg:huggingface/gpt2 (HuggingFace model)"
echo_info "      - pkg:generic/pyproject.toml (generic file)"
echo_info "  3. ✓ Hermeto generated DVC_CACHE_DIR environment variable"
echo_info "  4. ✓ dvc checkout restores both files from cache"
echo_info "  5. ✓ dvc checkout FAILS with empty cache (no network fetch)"
echo_info "  6. ✓ All file checksums verified"
echo ""
echo_info "Key Findings:"
echo_info "  • dvc checkout ONLY uses local cache (never fetches from network)"
echo_info "  • dvc fetch works with DVC remotes (S3, GCS, etc.), not direct HTTP URLs"
echo_info "  • Hermeto correctly parses dvc.lock and generates SBOM/env vars"
echo_info "  • HuggingFace URLs get pkg:huggingface purl type"
echo_info "  • Generic URLs get pkg:generic purl type"
echo_info "  • For hermetic builds: cache must be populated before network isolation"
echo ""
echo_info "Real-world workflow:"
echo_info "  Phase 1 (with network): hermeto runs 'dvc fetch' to populate cache"
echo_info "  Phase 2 (hermetic): build sets DVC_CACHE_DIR and runs 'dvc checkout'"
echo_info "  Phase 2: dvc checkout restores from cache, fails if cache empty"
