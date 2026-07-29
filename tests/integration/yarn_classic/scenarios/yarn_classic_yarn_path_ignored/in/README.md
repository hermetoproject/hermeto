# Corepack installs Yarn 1.x Integration Test

This repository provides an extremely simple Yarn 1.x project which can
be used to test whether the version of Yarn specified by [yarn-path](https://classic.yarnpkg.com/lang/en/docs/yarnrc/#toc-yarn-path)
in .yarnrc is used.

In the current implementation of Yarn 1.x support in Cachi2, this field should be ignored. A version of Yarn 3.x
is specified and checked-in to the repo here even though the project uses Yarn 1.x in order to aid detection of
an incorrect outcome in the test.
