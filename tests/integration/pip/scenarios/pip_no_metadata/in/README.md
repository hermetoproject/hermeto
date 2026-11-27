# pip no metadata test

This integration test covers a scenario, when a Python project (or multiple projects)
does not contain any supported setup files (setup.py, setup.cfg, pyproject.toml).

Such a directory only holds requirements files, so no component is reported for
the directory itself, only for the packages listed in its requirements files.
