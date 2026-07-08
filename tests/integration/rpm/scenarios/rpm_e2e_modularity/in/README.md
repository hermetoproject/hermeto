# Cachi2 RPMs E2E test for modules

This scenario covers the prefetch and building of a ubi8 based container image that has `httpd-tools` installed from a set of RPMs (including modular packages).

The test steps are as follow:
- prefetch the RPMs and module metadata with Cachi2
- validate the prefetched output and the resulting SBOM
- build an image hermetically with `httpd-tools` and modular dependency packages installed
