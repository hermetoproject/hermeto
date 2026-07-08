# Multiple RPM architectures test

This test exercises prefetching and reporting of RPMs present in two different architectures:
x86_64 and aarch64.

NOTE: If you need to regenerate the lockfile, please run the lockfile generator as:

```shell
$ rpm-lockfile-prototype \
    --bare \
    --outfile rpms.lock.yaml \
    rpms.in.yaml
```
