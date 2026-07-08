# npm-cachi2-multiple-packages 

Repo to test cachi2 functionality with multiple packages within repo.


## Expected behavior
* Both packages should be handled the same way.
* Both should be installable.

* The SBOM should report the union of dependencies from both packages (with no duplication). Custom properties need special care:
  * a dependency which is both dev and non-dev is non-dev
  * a dependency which is both bundled and not bundled is not bundled

## Package creation
Create two different packages in one repo, make sure one package depends on something as dev:

`npm add --save-dev name@version`

the other package depends on the same thing as non-dev:

`npm add same-name@same-version`
