# Supporting package managers proxies in Hermeto

## Background

It might be desirable to pull dependencies through a proxy for a variety of
reasons (builds reproducibility, additional verification, fetch speedup,
etc). This means that Hermeto has to support proxying packages for
individual package managers.

## Proxy usage overview

Most supported package managers (with a notable exception of Yarn v1) make
use of proxy URL and support standard mechanisms for authentication.  Proxy
usage is transparent to users as long as they provide correct proxy
address and authentication credentials. Given that a proxy is correctly
configured a user would just receive their dependencies as usual with an
underlying package manager handling everything seamlessly.  Thus a way to
consume proxy URL and credentials from the environment must be added to
Hermeto.

A proxy might require authentication or not.  Also note that a proxy might only be
set up for a subset of the supported package managers.  This means that each
individual package manager will need to handle checking the proxy setting and
authentication setting. Once these settings are known they must be made
available to the underlying tool used for fetching the actual dependencies
either via environment or via configuration options.


## Additional considerations

### SBOM enhancement

SBOMs must be marked to indicate whether a proxy was used or not. Currently the
origin of an artifact is not recorded in an SBOM at all, it is assumed that
download location is the same as the one reported in the PURL. With addition of
proxies support this won't be true anymore. Both supported SBOM formats provide
means for specifying a component's location:

  1. CyclondeDX provides [externalReferences][];
  1. SPDX provides [PackageDownloadLocation][] and [PackageSourceInfo][] fields.

The most straightforward solution would be to include these fields into SBOMs.
Note, that PackageDownloadLocation allows `None` as value which could be used
to indicate that a package was downloaded from a source referenced in a PURL and
externalReferences, being an array, provides a natural way of indicating this
with an empty array. The downside of this solution is that some package
managers support specifying multiple proxies in the environment and not
reporting which specific proxy was used which means that all proxies must be
reported.  This is not the best possible outcome, but in practice would result
in verification of several proxies instead of one.  Unfortunately
[PackageDownloadLocation][] does not seem to support multiple locations which
is necessary for storing multiple proxy URLs. [PackageSourceInfo][] is a free-form
field which can be omitted, it could be used as a marker for proxy usage: if absent
then no proxy was used, if present it would contain a semicolon-separated list
of proxy URLs. An alternative to this is to add a custom property to SBOMs:

```
   proxy: None | [proxy_url]
```

The extra field will be added to each package/component of an SBOM.
The extra field will become a property for CycloneDX SBOMs and an annotation
for SPDX SBOMs. Having this property will allow any SBOM processing tool make
policy decisions basing on recorded proxy usage: since a proxy could be
opinionated the mere fact that a proxy was used and the sources came
from it would be an indicator that any opinion forming logic on the proxy
side has approved the use of these particular sources. This is less
important in the case of a simple caching proxy, but nevertheless
adds a clear record that a proxy was used (which might become important
when an anomalous build is investigated). Finally, in the rare case when
a package was pulled from a registry, but remained in cache this
property would unambiguously indicate where the sources came from.
The downside of this approach is that the property is custom and will not
be universally understood by third party tools which could be used to
process SBOMs.

Basing on the information provided above it is recommended to use
[PackageSourceInfo][] for SPDX and [externalReferences][] for CycloneDX.


### Fallback option

It might be desired to try and retrieve packages from the original source if
proxy fails for any reason. In other words, with fall-back enabled Hermeto
would try a proxy first, and if the proxy is non-responsive it would then try
fetching the sources as usual, as if no proxy settings were present. With
fall-back disabled Hermeto would fail a fetch if a proxy is not responding. To
achieve that the following option is proposed for addition to Hermeto config:

```
    fall-back-to-standard-source = False
```

It will be called `HERMETO_FALL_BACK_TO_STANDARD_SOURCE` in the environment.
The default value is proposed to be set to False. Fall-back should not happen
if there was an authorization error because it would mask it otherwise.


## Implementation

All configuration is proposed to be done via environment variables and
Hermeto config as a fall-back option. All new environment variables
will be prefixed with `HERMETO_` to disambiguate them from other
variables with same names. In case a variable is meant to be consumed by
a package manager then a secondary prefix is added (`PIP_` or `GOMOD_`).
The resulting variables would look like the following:
`HERMETO_PIP_PROXY_URL`, `HERMETO_GOMOD_PROXY_AUTH_TOKEN` etc. The values
associated with these variables could be the same. This would allow for
fine-grained control of proxy settings for each individual package manager.

Every package manager will receive an instance of a Proxy subclass.
The class will be responsible for checking the override option, for
reading and verifying proxy URL from environment and for reading and
verifying credentials for the package manager:

```python
    class Proxy:
        proxy_url = URL | None  # Must be defined if username is defined
        username  = str | None  # Must be defined if password xor token is defined
        password  = str | None  # Must not be defined if token is defined
        token     = str | None  # Must not be defined if password is defined

        @abstractmethod
        def make_environment_extension(self) -> dict[varname, varvalue]

        @abstractmethod
        @classmethod
        def from_env(Proxy) -> Proxy
```

Each package manager would subclass Proxy and extend it with a translation
table from field names to variable names which are understood by each
individual package manager. A subclass must resolve proxy URL variable and
authentication variables named according to scheme described above to names
used by its underlying native tool.  These names will be used to instantiate
the class from environment and later to populate the environment extension
dictionary. The dictionary will be used by the class' client to populate
environment of a process that would run the individual package manager.

```python
    class FooProxy(Proxy):
        variable_conversion_table = (("HERMETO_FOO_PROXY_URL", "FOO_PROXY), ... )
        ...
```

None of the fields is mandatory, the class provides one method to create
environment extension dictionary and one class method for consuming values from
environment. If a field is None then it would not be added to an extension
dictionary. It is possible to receive an empty extension dictionary when the
variables are not set or when there is an override taking place thus it will be
always safe to extend environment with this dictionary. This would require
making sure that every package manager that relies on native tools always
passes environment to subprocesses. Any package manager that re-implements some
or all aspects of native tools would need to process Proxy properties on its
own.


## Appendix A. Future enhancements: handling of non-registry dependencies

Note, that the text below explores rather rare corner cases and having
this functionality is not necessary for the majority of packages out there.

In some cases a project might have a dependency that is not sourced from a standard
registry for that ecosystem (it could be a git dependency or a dependency
shared via a non-official registry). It might be desirable to  provide a push back
mechanism for such cases, for instance when the goal of using a proxy is to
ensure build reproducibility by storing all intermediate artefacts. Such push back
should be implemented on individual package managers level: it is up to an individual
package manager to distinguish between different types of dependencies and to make
appropriate choices. The following discussion will assume that there is a need for
a storing cache since this is the most complex scenario and everything else could
be reduced to it.

To store anything in a cache one would need a scheme for naming entities, a way to
store the entities and a mechanism for making decisions of whether to try and retrieve
an entity from a cache or to push it first.

It is proposed to identify non-registry entities by purl and to store them as archived
blobs. This way any non-registry entity entry in a corresponding lock file would be
converted to a purl first, then this purl would be used to query a cache pointed to
via an environment variable. In case there is a cache miss the dependency would be
downloaded from the source present in the lock file, archived and pushed to the cache,
then retrieved from the cache again. This double action might be necessary if the
cache is to provide some additional source-processing service like malware scanning
and should be controlled with a pair of environment switches, `HERMETO_REPUBLISH_SOURCES_TO_CACHE`,
and `HERMETO_PULL_BACK_CUSTOM_SOURCES`,
with default values of `False`. These variable will have to be set to `True` alongside with
`HERMETO_FALL_BACK_TO_STANDARD_SOURCE` to activate the double-action mechanism.

The possible combinations of the variables and their effects are presented in the table below:

```
[A] HERMETO_FALL_BACK_TO_STANDARD_SOURCE
[B] HERMETO_REPUBLISH_SOURCES_TO_CACHE
[C] HERMETO_PULL_BACK_CUSTOM_SOURCES
+-------+-------+-------+--------------------------------------------------------------+
|   A   |   B   |   C   | Effect                                                       |
+-------+-------+-------+--------------------------------------------------------------+
| True  | True  | True  | Double-action: sources are downloaded from regular location, |
|       |       |       | then pushed to a cache, then fetched from the cache.         |
+-------+-------+-------+--------------------------------------------------------------+
| True  | True  | False | Simple cache filling, for systems that do not rely on any    |
|       |       |       | cache-side processing.                                       |
+-------+-------+-------+--------------------------------------------------------------+
| True  | False | False | Cache circumvention for custom sources.                      |
+-------+-------+-------+--------------------------------------------------------------+
| True  | False | True  | Forbidden combination.                                       |
+-------+-------+-------+--------------------------------------------------------------+
| False | Any   | Any   | Effectively blocks custom sources from being used.           |
+-------+-------+-------+--------------------------------------------------------------+
```

Implementation-wise that would require decorating corresponding download() methods with
a handler that would do the conversion and dispatch the request to other methods if needed
basing on the state of the environment.

Finally, it might be desirable to exclude certain dependencies from being archived
(for example, because they originate from private repositories). For that case
it is proposed to add  `HERMETO_EXCLUDE_FROM_CACHING` variable containing a semicolon-
separated list of regular expressions which should match package names to exclude.
Any package that matches such regexp will always be downloaded directly without
accessing a cache. Such package will also be always marked accordingly in an SBOM
which would help ensuring that this mechanism is not abused.


[externalReferences]: https://cyclonedx.org/docs/1.6/json/#components_items_externalReferences
[PackageDownloadLocation]: https://spdx.github.io/spdx-spec/v2.3/package-information/#77-package-download-location-field
[PackageSourceInfo]: https://spdx.github.io/spdx-spec/v2.3/package-information/#712-source-information-field
