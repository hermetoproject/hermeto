# uv e2e test

End-to-end test for the uv backend. The project depends on `idna` from a
registry, `packaging` from a direct URL and `itsdangerous` from git, so the
build exercises every source kind whose `uv.lock` entry Hermeto rewrites.

The built image runs `uv sync` with no network access, then a script that
reports the installed version of each dependency. That is what proves the
rewritten lockfile and the generated environment variables are enough to
install offline -- fetching the artifacts is only half the job.

`flit_core` is the build backend everywhere here, and it has no build
dependencies of its own, which keeps `requirements-build.txt` small. Two
versions of it are pinned because `packaging` allows `flit_core<5` while the
others cap at `<4`; uv resolves an isolated build environment per package, so
both are needed.

## Updating dependencies

After editing `pyproject.toml`, regenerate the lockfile and the build
requirements:

    uv lock
    uv export --format requirements-txt --no-emit-project -o requirements.txt
    pybuild-deps compile --generate-hashes requirements.txt -o requirements-build.txt
    rm requirements.txt

`uv export` omits the project itself because `pybuild-deps` cannot resolve an
editable requirement. `uv sync` still builds the project, so make sure
`requirements-build.txt` covers this `pyproject.toml`'s own
`[build-system].requires` -- here `flit_core` is already pulled in by the
dependencies.
