# pip e2e test

End-to-end test for pip dependency prefetching. Exercises
both a PyPI registry dependency as well as a VCS URL dependency.

The built image runs a script that imports both packages to
verify they were installed correctly.

## Updating dependencies

Runtime dependencies are declared in requirements.in and
build dependencies in requirements-build.in. After editing
either file, regenerate the corresponding lockfile:

    uv pip compile --generate-hashes requirements.in -o requirements.txt
    uv pip compile --generate-hashes --allow-unsafe requirements-build.in -o requirements-build.txt
