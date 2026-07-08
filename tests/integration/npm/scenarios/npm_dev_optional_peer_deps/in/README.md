# Npm dev, optional and peer dependencies test

Verify Cachi2's handling of dev, optional and peer dependencies. These dependencies should be downloaded and reported in the SBOM in the same way as regular dependencies.

This repository consists of a main package with one of each type of dependency, plus one additional dependency from Github and one from a file.

The `foo` folder is meant to test dev and optional dependency processing in a workspace.
