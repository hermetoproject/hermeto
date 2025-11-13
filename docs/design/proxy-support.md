# Supporting package managers proxies in Hermeto

## Background

It might be desirable to pipe dependencies through a proxy for a variety of
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

A proxy might require authentication or not.  A proxy might exist for just
some package managers and do not exist for others. This means that each
individual package manager will need to handle checking the proxy setting
and authentication setting. Once these settings are known they must be
made available to the underlying tool used for fetching the actual
dependencies either via environment or via a configuration option.


## Additional considerations

### SBOM enhancement

SBOM must be marked to indicate whether a proxy was used or not:

```
   used_proxy: None | proxy_url
```

The extra field will be added to each package/component of SBOM.
The extra field will become a property for CycloneDX SBOMs and an annotation
for SPDX SBOMs. This will allow any SBOM processing tool make policy decisions
basing on recorded proxy usage.


### Override option

An overriding option to ignore proxy settings must be provided via
environment and/or config file. It will be set to false by default and will
override any proxy settings when set to true.  This will allow quick and
transparent switching the feature off.

The following option is proposed for addition to Hermeto config:

```
    ignore-proxy-settings = False
```

### Fallback option

It might be desired to try and retrieve packages from the standard source if
proxy fails for any reason:

```
    fall-back-to-standard-source = False
```

The default value is proposed to be set to False.

## Implementation

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
individual package manager. A subclass must provide the same name for proxy URL
variable and authentication variables as used by its underlying native tool.
These names will be used to instantiate the class from environment and later to
populate the environment extension dictionary.

```python
    class FooProxy(Proxy):
        proxy_url_known_as = "FOO_PROXY"
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
