# Multiple packages test

This test covers a scenario with multiple (two) pip packages or projects in one git repository
processed in a single request. Each package has its own `pyproject.toml` file and generated
`requirements.txt` file with `uv` tool.
