# Multiple RPM projects on the input test

This test is a clone of the multiple RPM packages on the input test. The only difference is that
we're testing RPM summary addition in the generated SBOM.

following scenarios are covered:
- Identical package rpms.lock.yaml entry (alternatives)
- Identical package, but one rpms.lock.yaml instance is missing checksum (libcom_err)
- Identical package, two different versions (glibc-minimal-langpack)

NOTE: If you need to regenerate the lockfile, please run the lockfile generator as:

```shell
$ rpm-lockfile-prototype \
    --bare \
    --outfile rpms.lock.yaml \
    rpms.in.yaml
```
