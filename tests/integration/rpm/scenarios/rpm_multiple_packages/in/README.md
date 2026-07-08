# Multiple RPM projects on the input test

This test tests prefetching and SBOM reporting of RPMs based on two different projects passed
in the input request.

following scenarios are covered:
- Identical package rpms.lock.yaml entry (alternatives)
- Identical package, but one rpms.lock.yaml instance is missing checksum (centos-stream-repos)
- Identical package, two different versions (libcom_err)

NOTE: If you need to regenerate the lockfile, please run the lockfile generator as:

```shell
$ rpm-lockfile-prototype \
    --bare \
    --outfile rpms.lock.yaml \
    rpms.in.yaml
```
