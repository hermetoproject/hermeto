---
name: regenerate-rpms-lock
description: Repair RPM integration test lockfiles under tests/integration/rpm
  when CI fails because locked package versions have disappeared from the
  CentOS Stream RPM index, or when the user requests this repair.
  Generate replacement candidates with
  rpm-lockfile-prototype, then update only RPMs whose existing URLs no longer
  resolve. Preserve all still-resolving packages and each test's intent.
---

# Repair RPM integration test lockfiles

Upstream has no repository-root `rpms.lock.yaml`. This skill targets fixtures
under `tests/integration/rpm/scenarios/`, including nested `in/this-project/`
and `in/another-project/` directories. CentOS Stream removes older package
versions over time, breaking previously passing tests. Regeneration supplies
candidates, not a wholesale package refresh.

Only the CentOS Stream index (`mirror.stream.centos.org`) is relevant to this
skill. Apply the repair workflow only to RPMs from that index; report failures
involving other indexes separately without changing those entries.

## 1. Reproduce failures with RPM integration tests

Run from the repository root. Read `tests/integration/README.md` for container
setup and image options. Activate the shared repository venv, then invoke
`pytest` directly for easier debugging. Limit collection to the RPM directory:

```bash
HERMETO_TEST_GENERATE_DATA=0 pytest tests/integration/rpm -vv --tb=long \
  --log-cli-level=DEBUG
```

The test fixtures use Podman by default and build the Hermeto image unless
`HERMETO_TEST_IMAGE` or `--hermeto-image` specifies an existing image. Keep
expected-data generation disabled during diagnosis. If container setup fails,
report the setup failure; it is not evidence of unavailable RPMs.

Inspect this run's failure tracebacks, captured Hermeto output, and pytest
summary. Record the failed test node IDs and download errors. The parameter ID
in brackets identifies the scenario, for example `rpm_multiple_archs`. Rerun a
failing scenario to isolate its complete download error:

```bash
scenario=rpm_multiple_archs  # Replace with an actual failing scenario ID.
HERMETO_TEST_GENERATE_DATA=0 pytest tests/integration/rpm \
  -k "$scenario" -vv --tb=long \
  --log-cli-level=DEBUG
```

Separate missing-RPM download failures from expected checksum/size failures,
SBOM mismatches, and unrelated build or network failures. Investigate each
actual download failure before changing fixtures.

## 2. Extract locked URLs and query the CentOS Stream index

Read the failing case in `tests/integration/rpm/test_rpm.py` and its fixture's
`in/README.md`. Locate the relevant `rpms.lock.yaml` under
`tests/integration/rpm/scenarios/<scenario>/in/`; multi-project tests have
lockfiles in `this-project/` and `another-project/`. Follow fixture symlinks
to their shared source so a repair updates the original file.

Match the failed RPM filename or URL from the test output to its entry in that
lockfile. Extract the exact `url`, `name`, `evr`, `repoid`, and enclosing
`arches[].arch`. Do not reconstruct a URL from a guessed version. Read the
matching `rpms.in.yaml` to identify the configured repositories and any pins.

Check the locked URL with a GET following redirects. Set `url` to the exact
value copied from the entry, then run:

```bash
curl -sS -L -o /dev/null -w '%{http_code}\n' "$url"
index_url="${url%/*}/"
curl -fsSL "$index_url"
```

The second request queries the CentOS Stream `Packages/` directory index for the
same release, repository, and architecture. Inspect its listed filenames:
confirm the old filename is absent and identify available versions of the
same package and architecture. A newer listed version alone is not a reason
to replace a locked RPM whose URL still works. A 404/410 for the locked URL,
with a reachable index showing the old file is absent, establishes removal.
If the index cannot be queried, report that diagnostic limitation rather than
assuming the version disappeared.

Check other lockfiles containing the same failed URL, and check every RPM URL
in an affected lockfile if tests stopped at the first failed download. Record
an allowlist of confirmed unavailable entries, with lockfile path, package,
architecture, repository, old version, and old URL. Timeouts, TLS failures,
rate limits, and server errors do not establish removal; resolve or report
those failures. Preserve every still-resolving entry exactly.

## 3. Generate a candidate lockfile

Build the tool image unless it already exists in the local image store:

```bash
tool_url=https://raw.githubusercontent.com/konflux-ci/rpm-lockfile-prototype
podman image exists localhost/rpm-lockfile-prototype || \
  curl -fsSL "${tool_url}/refs/heads/main/Containerfile" \
    | podman build -t localhost/rpm-lockfile-prototype -
```

Use `docker` if `podman` is not installed; replace the image-existence check
with `docker image inspect localhost/rpm-lockfile-prototype`.

Run from the directory containing the affected `rpms.in.yaml`. Follow the
scenario README's options; the fixtures commonly require `--bare`. Write a
separate candidate file, never overwrite the tracked lockfile:

```bash
podman run --rm -v "${PWD}":/work:Z localhost/rpm-lockfile-prototype:latest \
  --bare --outfile=rpms.lock.candidate.yaml rpms.in.yaml
```

Passing `--image` should not be needed unless the scenario instructions or
user explicitly require it. If no matching input exists, inspect the
fixture's provenance; do not substitute an unrelated scenario's input.

If generation fails naming a missing/unresolvable package, trust that error
as-is, do not re-verify it with `dnf repoquery` or similar. Leave the tracked
lockfile untouched and report the package name. Ignore
`WARNING:rpm_lockfile:No sources found for ...` lines; they do not affect the
binary RPM entries written.

## 4. Apply only confirmed replacements

Copy only replacements for unavailable RPMs on the allowlist into the
original lockfile. Match package identity, architecture, and repository.
Update the URL and associated `evr`, checksum, size, `sourcerpm`, and other
artifact metadata together. Confirm each replacement URL resolves.

Preserve the package set, architectures, repositories, module metadata,
ordering, and unrelated entries. Never copy the entire candidate or accept
incidental dependency/version churn. If no matching replacement exists, or
broader dependency changes are required, stop and explain the blocker.

Preserve deliberate test mutations, e.g. those intentionally omitting checksums,
lockfiles reporting incorrect checksums or incorrect package size.
Read the scenario's assertions before applying generated metadata so the
repair preserves test intent.

## 5. Update expected outputs and verify

Return to the repository root. Update expected outputs under the affected
scenario's `out/` directory only where the replaced RPM changes the SBOM or
other asserted output. Include scenarios sharing the repaired input, such as
architecture filtering.
Set a pytest expression covering the affected cases, e.g.:

```bash
affected='rpm_multiple_archs or rpm_multiple_archs_with_filtering'
HERMETO_TEST_GENERATE_DATA=1 pytest tests/integration/rpm -k "$affected" -vv
```

Replace this example with the actual affected IDs. Review generated changes
for unrelated churn. Then rerun the affected tests without data generation:

```bash
HERMETO_TEST_GENERATE_DATA=0 pytest tests/integration/rpm -k "$affected" -vv
```

Finally, run the RPM-only suite to check shared fixtures and remaining URLs:

```bash
HERMETO_TEST_GENERATE_DATA=0 pytest tests/integration/rpm -vv
```

If another missing RPM appears, repeat URL/index diagnosis and add only that
confirmed unavailable entry to the allowlist. If the required container or
network environment is unavailable, report the verification limitation.

## 6. Review and report

Remove temporary candidates. From the repository root, review:

```bash
git diff --stat -- tests/integration/rpm
git diff -- tests/integration/rpm
```

Every changed RPM must map to an old URL confirmed unavailable, and
expected-output changes must map to those replacements. Verify that
still-resolving entries are unchanged. Report replaced packages, old and new
versions, affected scenarios, and test results. Do not commit automatically;
leave changes in the working tree unless explicitly asked to commit.
