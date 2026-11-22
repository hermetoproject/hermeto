# Ansible collections design

Related: [hermetoproject/hermeto#1071](https://github.com/hermetoproject/hermeto/issues/1071)

## Overview

Ansible collections are distributed as versioned tarballs from Ansible Galaxy
and Red Hat Automation Hub (both galaxy_ng). Offline / hermetic builds need
those tarballs prefetched with verified checksums and recorded in an SBOM.

Hermeto should not resolve version ranges or transitive dependencies. The user
would supply a fully resolved `ansible.lock.yaml` (same idea as a fully pinned
`requirements.txt` for pip). A prototype lockfile generator lives at
[ansible-lockfile-prototype](https://github.com/fabriziosta/ansible-lockfile-prototype).

### Developer workflow (proposed)

1. Declare collections in `requirements.yml` (FQCN pins and/or path-style
   `.tar.gz` names used for vendoring today).
2. Configure Galaxy servers in `ansible.cfg` (`server_list`, Automation Hub
   `auth_url` + `token_env` as needed).
3. Generate a fully resolved `ansible.lock.yaml` with remote download URLs
   (prototype flag: `--prefer-remote`).
4. Hermeto prefetches from that lockfile, verifies checksums, emits SBOM.
5. Offline install uses rewritten requirements pointing at cached tarballs
   (same inject pattern as other backends).

### How downloads work (Pulp)

galaxy_ng (public Galaxy and Automation Hub) uses Pulp as the content backend.
Clients do not call Pulp APIs for install/prefetch:

1. Metadata via Galaxy/AH v3 APIs (done by the lockfile generator).
2. HTTP GET of `CollectionVersion.download_url` (stored as `url` in the
   lockfile).
3. Follow redirects (often Galaxy artifact URL → `/api/pulp/content/...` →
   object storage).

**Implication for Hermeto:** prefetch should be plain HTTPS to the lockfile URL
plus checksum verification, not Pulp content/management APIs. Pulp is server
infrastructure on galaxy_ng hosts; the client contract remains “GET
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

Lockfile entries should use remote `https://` URLs only so Hermeto can fetch and
verify them. Local `file://` lock entries are not a good fit for this backend.

### Authentication

Credentials would come from `ansible.cfg` (package path or source root), for
example:

```ini
[galaxy_server.automation_hub]
url = https://console.redhat.com/api/automation-hub/content/published/
auth_url = https://sso.redhat.com/auth/realms/redhat-external/protocol/openid-connect/token
token_env = AUTOMATION_HUB_TOKEN
```

- With `auth_url`: refresh-token grant → Bearer access token on artifact
  downloads.
- Without `auth_url`: `Authorization: Token <token>` when a token is present.
- Lockfile `server` selects which `galaxy_server.*` section supplies auth.
- `server_list` is not used to re-resolve versions at prefetch time; lockfile
  URLs are authoritative.

Open question: require that the artifact URL origin matches the configured
server URL before attaching credentials (avoid sending AH tokens to arbitrary
hosts).

### Prefetch flow (proposed)

1. Load lockfile; place artifacts under `deps/ansible/`.
2. Attach auth headers from `ansible.cfg` when appropriate.
3. Download (follow redirects).
4. Verify checksums; fail on mismatch.
5. Emit SBOM components; rewrite requirements for offline install.

### SBOM / purl

There is no official package-url `ansible` type yet. A possible experimental
shape:

```text
pkg:ansible/namespace/name@version?checksum=sha256:…&download_url=…
```

Identity comes from the lockfile. Optional enrichment from `MANIFEST.json`
(e.g. license) can be discussed later.

### Offline install (inject)

Rewrite collection entries in discovered `requirements.yml` /
`requirements.yaml` so `name` points at
`${output_dir}/deps/ansible/<artifact>.tar.gz`, matching the offline pattern
described in #1071.

## Implementation notes

- Suggested package manager type: experimental `x-ansible`.
- Closest existing template: Hermeto `generic` (lockfile → download → checksum →
  component).
- Backend annotation would follow the usual experimental pattern.
