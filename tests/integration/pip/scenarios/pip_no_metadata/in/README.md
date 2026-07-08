# cachi2-pip-extra

This integration test covers a scenario, when a Python project (or multiple projects)
does not contain any supported setup files (setup.py, setup.cfg, pyproject.toml).

In such cases, the project name will be resolved from the repository origin url.
If the package is at a subpath, the normalized subpath will be appended
to the resolved project name (the version will be omited).
