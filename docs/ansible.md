# Ansible collections (experimental)

!!! warning
    The `x-ansible` package manager is experimental. Enable it by requesting
    package type `x-ansible`. Behavior and lockfile schema may change.

Hermeto can prefetch Ansible Galaxy / Automation Hub **collections** from a
fully resolved `ansible.lock.yaml` lockfile, verify checksums, emit SBOM
components, and rewrite `requirements.yml` to point at cached local tarballs
for offline installs.

Hermeto does **not** resolve version ranges or transitive dependencies. Generate
the lockfile first with
[ansible-lockfile-prototype](https://github.com/fabriziosta/ansible-lockfile-prototype)
(or an equivalent tool).

## Quick start

```bash
# In your project (with ansible.cfg + requirements + ansible.lock.yaml):
hermeto fetch-deps '{
  "packages": [{"type": "x-ansible", "path": "."}]
}'
```

Optional lockfile override:

```json
{"type": "x-ansible", "path": ".", "lockfile": "ansible.lock.yaml"}
```

## Inputs

| File | Role |
|------|------|
| `ansible.lock.yaml` | Fully resolved collections (`name`, `version`, `url`, `checksum`, optional `server`) |
| `ansible.cfg` | Credentials for private hubs (`token_env`, `auth_url`, `token=`); **not** used to re-resolve versions |

Example `ansible.cfg` for Automation Hub:

```ini
[galaxy]
server_list = automation_hub, galaxy

[galaxy_server.automation_hub]
url = https://console.redhat.com/api/automation-hub/content/published/
auth_url = https://sso.redhat.com/auth/realms/redhat-external/protocol/openid-connect/token
token_env = AUTOMATION_HUB_TOKEN

[galaxy_server.galaxy]
url = https://galaxy.ansible.com
```

```bash
export AUTOMATION_HUB_TOKEN=...   # refresh / offline token
```

## Prefetch behavior

1. Read `ansible.lock.yaml` (must contain only `http(s)` collection URLs).
2. Download each URL into `deps/ansible/` (HTTP redirects through galaxy_ng / Pulp
   content are followed transparently; Hermeto does not call Pulp APIs).
3. Verify `sha256` against the lockfile (hard fail on mismatch).
4. Emit SBOM components with experimental `pkg:ansible/…` purls.
5. Rewrite discovered `requirements.yml` / `requirements.yaml` so collection
   `name` values point at `${output_dir}/deps/ansible/<artifact>.tar.gz`.

`file://` (and other non-remote) URLs are **rejected**. Generate the lockfile with
`ansible-lockfile-prototype --prefer-remote` so every entry has a remote download
URL Hermeto can fetch and verify.

## Interim alternative

Until `x-ansible` is stable, you can export a Hermeto generic lockfile from the
prototype (`--export-generic=artifacts.lock.yaml`) and use package type
`generic`.
