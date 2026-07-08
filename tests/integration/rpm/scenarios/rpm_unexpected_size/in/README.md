# Incorrect RPM size test

This scenario validates that Hermeto will refuse to proceed the prefetch of RPMs when an incorrect
file size is found.

The test steps are as follow:
- prefetch the dependencies
- validate that the prefetch fails with the appropriate error

NOTE: If you need to regenerate the lockfile, please run the lockfile generator as:

```shell
$ rpm-lockfile-prototype \
    --bare \
    --outfile rpms.lock.yaml \
    rpms.in.yaml
```
