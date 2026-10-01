# Ansible collections (experimental) design

**Contributors**: Fabrizio Sota / AAP Konflux

Related: [hermetoproject/hermeto#1071](https://github.com/hermetoproject/hermeto/issues/1071)

## Overview

Ansible collections are distributed as versioned tarballs from Ansible Galaxy
and Red Hat Automation Hub (both galaxy_ng). Builds that must run offline need
those tarballs prefetched with verified checksums and recorded in an SBOM.

Hermeto does **not** resolve version ranges or transitive dependencies. The
user supplies a fully resolved [`ansible.lock.yaml`](../ansible.md) (analogous
to a fully pinned `requirements.txt` for pip), typically produced by
[ansible-lockfile-prototype](https://github.com/fabriziosta/ansible-lockfile-prototype).

### Developer workflow

1. Declare collections in `requirements.yml` (FQCN pins and/or vendored
   path-style `.tar.gz` names).
2. Configure Galaxy servers in `ansible.cfg` (`server_list`, Automation Hub
   `auth_url` + `token_env`).
3. Generate `ansible.lock.yaml` with the lockfile tool using `--prefer-remote`
   (Hermeto rejects `file://` lock entries).
4. Run Hermeto with package type `x-ansible`.
5. Install offline from rewritten requirements pointing at cached tarballs.

### How downloads work (Pulp)

galaxy_ng (public Galaxy and Automation Hub) uses **Pulp as the content
backend**. Clients never call Pulp APIs for install/prefetch:

1. Metadata via Galaxy/AH v3 APIs (done by the lockfile generator).
2. HTTP GET of `CollectionVersion.download_url` (stored as `url` in the
   lockfile).
3. Follow redirects (often Galaxy artifact URL → `/api/pulp/content/...` →
   object storage).

**Implication for Hermeto:** prefetch is plain HTTP to the lockfile URL plus
checksum verification. Do **not** implement against Pulp content or management
APIs. Pulp is always involved as server infrastructure on galaxy_ng hosts, not
as an optional CI-only proxy—but the client contract remains “GET
`download_url`”.

## Design

### Scope

**In scope**

- Prefetch collections listed in `ansible.lock.yaml`
- Auth via project `ansible.cfg` (`token_env`, `auth_url`, `token=`) mapped by
  lockfile `server` name
- Checksum verification (sha256 from lockfile)
- SBOM components per collection
- Rewrite `requirements.yml` / `requirements.yaml` to local cached paths

**Out of scope (first cut)**

- Resolving ranges / transitive deps inside Hermeto
- Roles; `type: git|dir|url` sources
- Calling `ansible-galaxy` or Pulp APIs
- Lockfile generation (external tool)

### Dependency list

| Item | Detail |
|------|--------|
| File | `ansible.lock.yaml` (default) |
| Format | YAML; `lockfileVersion`, `lockfileVendor: ansible`, `collections[]` |
| Required fields | `name` (FQCN), `version`, `url`, `checksum` (`sha256:…`) |
| Optional | `size`, `server`, top-level `auth` |

Example snippet:

```yaml
lockfileVersion: 1
lockfileVendor: ansible
collections:
  - name: amazon.aws
    version: "10.3.0"
    url: https://console.redhat.com/api/automation-hub/v3/plugin/ansible/content/published/collections/artifacts/amazon-aws-10.3.0.tar.gz
    checksum: sha256:6496fc315513cba8c48125cfda4e464d74e3fb43616df784b1ceb14b1290b951
    size: 1314805
    server: automation_hub
```

`file://` (and other non-http(s)) URLs are **rejected**. Regenerate the lockfile
with `ansible-lockfile-prototype --prefer-remote` so Hermeto always fetches and
verifies remote artifacts.

### Authentication

Prefer credentials from `ansible.cfg` next to the package path (same discovery
order as ansible-galaxy is not required; Hermeto looks for `ansible.cfg` under
the package path, then the source root):

```ini
[galaxy_server.automation_hub]
url = https://console.redhat.com/api/automation-hub/content/published/
auth_url = https://sso.redhat.com/auth/realms/redhat-external/protocol/openid-connect/token
token_env = AUTOMATION_HUB_TOKEN
```

- With `auth_url`: refresh-token grant → `Authorization: Bearer <access_token>`
  on artifact downloads.
- Without `auth_url`: `Authorization: Token <token>` when a token is present.
- Lockfile `server` selects which `galaxy_server.*` section supplies auth.
- `server_list` is **not** used to re-resolve versions at prefetch time.

### Prefetch flow

1. Load lockfile; resolve output paths under `deps/ansible/`.
2. Build auth headers per URL from `ansible.cfg` / lockfile `server`.
3. `async_download_files` (follow redirects).
4. `must_match_any_checksum` — hard fail on mismatch.
5. Emit SBOM components; rewrite requirements files as `ProjectFile`s.

### SBOM / purl

There is no official package-url `ansible` type yet. Components use an
experimental purl:

```text
pkg:ansible/namespace/name@version?checksum=sha256:…&download_url=…
```

`type` is `library`. Optional enrichment from `MANIFEST.json` (e.g. license)
may be added later; identity comes from the lockfile.

### Offline install (inject)

Hermeto rewrites collection entries in discovered `requirements.yml` /
`requirements.yaml` (package path and common locations) so `name` points at
`${output_dir}/deps/ansible/<artifact>.tar.gz`, matching the offline pattern
described in #1071.

## Implementation notes

- Package manager type: **`x-ansible`** (experimental).
- Template: Hermeto `generic` package manager (lockfile → download → checksum →
  component).
- Backend annotation: `hermeto:backend:experimental:x-ansible`.
