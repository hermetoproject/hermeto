# Yarnberry Unsupported Protocols Integration Test

This scenario uses an Exec dependency, which Hermeto does not support.
Executing `yarn install` in the presence of such a dependency would lead to
arbitrary code execution.

Git and GitHub dependencies are covered by the dedicated `yarn_git_resolve_*`
scenarios (permissive rewrite vs strict rejection), not this one.

See:

```shell
git grep 'exec:'
```

When processing this scenario, Hermeto should log the locator for each
unsupported dependency and raise an UnsupportedFeature error.
