# Writing integration tests

## Developing the test source

If you are trying to write a cachi2 integration test, and wish to run cachi2
against a local source repo, assuming that

- the image "localhost/cachi2:latest" exists and is a valid cachi2 container
  image
- your test source is in "~/temp/cachi2-test", **and it is a valid git repo**
  (i.e. a valid ".git" directory is present)

executing e.g.

```bash
podman run --rm -ti -v "~/temp/cachi2-test:~/temp/cachi2-test:z" -w "~/temp/cachi2-test" localhost/cachi2:latest
```

*should* give you a way to properly process the test source with cachi2

## Running pytest locally

Once you have working test sources, you'll need to commit and push them
somewhere that pytest can clone them from.

We *strongly* recommend making a fork, specific to your test, from one of the
repos found under the [cachito test repos][] GitHub org (note that there
are *many* repos there, covering all of the package managers which cachi2 and
Cachito support) [^1].

Once you have a fork, push to a new branch named after your scenario - now
pytest will have a proper commit hash in a repo to which you have complete
access (once your test is complete and passing, you can simply open a PR against
the repo in the [cachito-testing][] org).

At this point, you should be able to test locally.

### A walkthrough

1. Fork a test repo under [cachito-testing][] which uses the package manager you
   need to test, and/or is otherwise related
1. Clone the new fork to your local machine
1. Create a new branch in the repo, named to reflect the purpose of your test(s)
1. The goal here is to create what looks like a real
   Go/Javascript/Python/whichever project, but simplified enough to *only* include
   the required files which cachi2 needs to find and resolve dependencies
1. Commit the test code, and push it to your fork in GitHub. Take note of the
   commit hash.

#### Testing your tests

1. Add your pytest scenarios to the appropriate integration test source file
   under 'cachi2/tests/integration'. You'll need to put the following in
   `utils.TestParameters` for your test case

   - `repo="https://github.com/cachito-testing/cachito-pip-without-deps.git"`  # this will be the name of the test repo you forked
   - `ref="3fe2fc3cb8ffa36317cacbd9d356e35e17af2824"`  # this will be the commit hash noted previously
   - `packages=({"path": ".", "type": "pip"},)`  # for pip, for example
   - `check_vendor_checksums=False`  # or `True`, depending on the scenario

1. In your local **cachi2** repo, make sure you have 'pytest' and 'jsonschema'
  pip-installed in the cachi2 venv, so that you can run pytest without tox. Without
  `tox`, it's **much** easier to run pytest with *only* your selected test
  scanarios, and get useful, accessible logs
1. Bonus: pip-install 'pytest-html' in the venv, and add (e.g.)
   `--html=path_to_reports/pytest-report.html` to your `pytest` command line
   for a very nicely formatted HTML report
1. To summarize

   - You're in the cachi2 repo directory
   - Your test scenario's source is in 'tests/integration/test_foo.py'
   - Your test scenario is called `test_foo_package`, and its pytest 'id' is
     `foo_incorrect_checksum`

1. Now run

   ```bash
   CACHI2_IMAGE=localhost/cachi2:latest pytest -rA -vvvv --confcutdir=tests/integration --log-cli-level=DEBUG tests/integration/test_foo.py::test_foo_package[foo_incorrect_checksum]
   ```

   which will run *only* 'tests/integration/test_foo.py::test_foo_package' with
   it's "foo_incorrect_checksum" ID'd parameter set with a pre-built cachi2 container

## Running the test suite

It's a good idea to run the whole cachi2 integration test suite, just to make
sure everything still works properly. The command for this, *from inside the
cachi2 repo* is `tox -e integration`. You can also provide
`CACHI2_IMAGE=localhost/cachi2:latest` if you already have a current cachi2
container. e.g. `CACHI2_IMAGE=localhost/cachi2:latest tox -e integration` - this
will save a lot of time, as otherwise the image will be rebuilt from scratch.

[^1]: If you **really** can't find an existing repo which is related to your test
scenario, ping a maintainer and explain your situation.

[cachito-testing]: https://github.com/orgs/cachito-testing
[cachito test repos]: https://github.com/orgs/cachito-testing/repositories?type%3Dsource
