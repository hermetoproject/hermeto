# [npm][]

See also the [npm docs][]

- [Specifying packages to process](#specifying-packages-to-process)
- [Project files](#project-files)
  - [Dependencies](#dependencies)
  - [Project example](#project-example)
- [Using fetched dependencies](#using-fetched-dependencies)
  - [Changes made by the inject-files command](#changes-made-by-the-inject-files-command)
  - [Updated project example](#updated-project-example)

## Specifying packages to process

A package is a file or directory that is described by a package.json file.

- The project files for npm are package.json and one of package-lock.json or
  npm-shrinkwrap.json. See [Project files](#project-files) and npm
  documentation

  - See [package.json][]
  - See [package-lock.json][]

Notice that the package-lock.json version must be **higher than v1** (Node.js 15
or higher)! Package-lock.json v1 is not supported in Hermeto.

Hermeto can be run as follows

```shell
hermeto fetch-deps \
  --source ./my-repo \
  --output ./hermeto-output \
  '<JSON input>'
```

where 'JSON input' is

```jsonc
{
  // "npm" tells Hermeto to process npm packages
  "type": "npm",
  // path to the package (relative to the --source directory)
  // defaults to "."
  "path": ".",
}
```

or more simply by just invoking `hermeto fetch-deps npm`.

## Project files

Hermeto downloads dependencies explicitly declared in project files -
package.json and package-lock.json. The npm CLI manages the package-lock.json
file automatically. To make sure the file is up to date, you can use
[npm install][].

Possible dependency types in the above-mentioned files are described in the
following section.

### Dependencies

The "npm package" formats that Hermeto can process are the following

1. A folder containing a program described by a 'package.json' file.
1. A gzipped tarball containing the previous.
1. A URL that resolves to the previous.
1. A `<name>@<version>` that is published on the registry with the previous.
1. A `<name>@<tag>` that points to the previous.
1. A `<name>` that has a latest tag satisfying the previous.
1. A git url that, when cloned, results in... the first item in this list.

Examples of (package.json) dependency formats

(For the full list of dependency formats with explanation,
see the [npm documentation][])

<details>
  <summary>Dependencies from npm registries</summary>

```jsonc
{
  "dependencies": {
    "foo": "1.0.0 - 2.9999.9999",
    "bar": ">=1.0.2 <2.1.2",
    "baz": ">1.0.2 <=2.3.4",
    "boo": "2.0.1",
    ...
  }
}
```

</details>

<details>
  <summary>URLs as dependencies</summary>

```jsonc
{
  "dependencies": {
    "cli_bar": git+ssh://git@github.com:npm/cli.git#v1.0.27,
    "cli_foo": git://github.com/npm/cli.git#v1.0.1
  }
}
```

</details>

<details>
  <summary>GitHub URLs</summary>

```jsonc
{
  "dependencies": {
    "express": "expressjs/express",
    "mocha": "mochajs/mocha#4727d357ea",
    "module": "user/repo#feature/branch"
  }
}
```

</details>

<details>
  <summary>Local paths</summary>

```jsonc
{
  "name": "baz",
  "dependencies": {
    "bar": "file:../foo/bar"
  }
}
```

</details>

### Project example

<details>
  <summary>package.json</summary>

```jsonc
{
  "name": "npm-demo",
  "version": "1.0.0",
  "description": "",
  "main": "index.js",
  "scripts": {
    "test": "echo \"Error: no test specified\" && exit 1"
  },
  "author": "",
  "license": "ISC",
  "dependencies": {
    "react-dom": "^18.0.1",
        "@types/react-dom": "^18.0.1",
        "bitbucket-cachi2-npm-without-deps-second": "git+https://bitbucket.org/cachi-testing/cachi2-without-deps-second.git",
        "cachito-npm-without-deps": "https://github.com/cachito-testing/cachito-npm-without-deps/raw/tarball/cachito-npm-without-deps-1.0.0.tgz",
        "fecha": "file:fecha-4.2.3.tgz"
  },
  "workspaces": [
    "foo"
  ]
}
```

</details>

<details>
    <summary>package-lock.json</summary>

```jsonc
{
  "name": "cachi2-npm-demo",
  "version": "1.0.0",
  "lockfileVersion": 3,
  "requires": true,
  "packages": {
    "": {
      "name": "cachi2-npm-demo",
      "version": "1.0.0",
      "license": "ISC",
      "workspaces": [
        "foo"
      ],
      "dependencies": {
        "@types/react-dom": "^18.0.1",
        "bitbucket-cachi2-npm-without-deps-second": "git+https://bitbucket.org/cachi-testing/cachi2-without-deps-second.git",
        "cachito-npm-without-deps": "https://github.com/cachito-testing/cachito-npm-without-deps/raw/tarball/cachito-npm-without-deps-1.0.0.tgz",
        "fecha": "file:fecha-4.2.3.tgz",
        "react-dom": "^18.0.1"
      }
    },
    "foo": {
      "version": "1.0.0",
      "license": "ISC",
      "dependencies": {
        "is-positive": "github:kevva/is-positive"
      },
      "devDependencies": {}
    },
    "node_modules/@types/prop-types": {
      "version": "15.7.5",
      "resolved": "https://registry.npmjs.org/@types/prop-types/-/prop-types-15.7.5.tgz",
      "integrity": "sha512-JCB8C6SnDoQf0cNycqd/35A7MjcnK+ZTqE7judS6o7utxUCg6imJg3QK2qzHKszlTjcj2cn+NwMB2i96ubpj7w=="
    },
    "node_modules/@types/react": {
      "version": "18.2.18",
      "resolved": "https://registry.npmjs.org/@types/react/-/react-18.2.18.tgz",
      "integrity": "sha512-da4NTSeBv/P34xoZPhtcLkmZuJ+oYaCxHmyHzwaDQo9RQPBeXV+06gEk2FpqEcsX9XrnNLvRpVh6bdavDSjtiQ==",
      "dependencies": {
        "@types/prop-types": "*",
        "@types/scheduler": "*",
        "csstype": "^3.0.2"
      }
    },
    "node_modules/@types/react-dom": {
      "version": "18.2.7",
      "resolved": "https://registry.npmjs.org/@types/react-dom/-/react-dom-18.2.7.tgz",
      "integrity": "sha512-GRaAEriuT4zp9N4p1i8BDBYmEyfo+xQ3yHjJU4eiK5NDa1RmUZG+unZABUTK4/Ox/M+GaHwb6Ow8rUITrtjszA==",
      "dependencies": {
        "@types/react": "*"
      }
    },
    "node_modules/@types/scheduler": {
      "version": "0.16.3",
      "resolved": "https://registry.npmjs.org/@types/scheduler/-/scheduler-0.16.3.tgz",
      "integrity": "sha512-5cJ8CB4yAx7BH1oMvdU0Jh9lrEXyPkar6F9G/ERswkCuvP4KQZfZkSjcMbAICCpQTN4OuZn8tz0HiKv9TGZgrQ=="
    },
    "node_modules/bitbucket-cachi2-npm-without-deps-second": {
      "version": "2.0.0",
      "resolved": "git+ssh://git@bitbucket.org/cachi-testing/cachi2-without-deps-second.git#09992d418fc44a2895b7a9ff27c4e32d6f74a982"
    },
    "node_modules/cachito-npm-without-deps": {
      "version": "1.0.0",
      "resolved": "https://github.com/cachito-testing/cachito-npm-without-deps/raw/tarball/cachito-npm-without-deps-1.0.0.tgz",
      "integrity": "sha512-Q+cfkK1fnrNJqxiig/iVSZTe83OWLdxhuGa96k1IJJ5nkTxrhNyh6MUZ6YHKH8xitDgpIQSojuntctt2pB7+3g=="
    },
    "node_modules/csstype": {
      "version": "3.1.2",
      "resolved": "https://registry.npmjs.org/csstype/-/csstype-3.1.2.tgz",
      "integrity": "sha512-I7K1Uu0MBPzaFKg4nI5Q7Vs2t+3gWWW648spaF+Rg7pI9ds18Ugn+lvg4SHczUdKlHI5LWBXyqfS8+DufyBsgQ=="
    },
    "node_modules/fecha": {
      "version": "4.2.3",
      "resolved": "file:fecha-4.2.3.tgz",
      "integrity": "sha512-OP2IUU6HeYKJi3i0z4A19kHMQoLVs4Hc+DPqqxI2h/DPZHTm/vjsfC6P0b4jCMy14XizLBqvndQ+UilD7707Jw==",
      "license": "MIT"
    },
    "node_modules/foo": {
      "resolved": "foo",
      "link": true
    },
    "node_modules/is-positive": {
      "version": "3.1.0",
      "resolved": "git+ssh://git@github.com/kevva/is-positive.git#97edff6f525f192a3f83cea1944765f769ae2678",
      "license": "MIT",
      "engines": {
        "node": ">=0.10.0"
      }
    },
    "node_modules/js-tokens": {
      "version": "4.0.0",
      "resolved": "https://registry.npmjs.org/js-tokens/-/js-tokens-4.0.0.tgz",
      "integrity": "sha512-RdJUflcE3cUzKiMqQgsCu06FPu9UdIJO0beYbPhHN4k6apgJtifcoCtT9bcxOpYBtpD2kCM6Sbzg4CausW/PKQ=="
    },
    "node_modules/loose-envify": {
      "version": "1.4.0",
      "resolved": "https://registry.npmjs.org/loose-envify/-/loose-envify-1.4.0.tgz",
      "integrity": "sha512-lyuxPGr/Wfhrlem2CL/UcnUc1zcqKAImBDzukY7Y5F/yQiNdko6+fRLevlw1HgMySw7f611UIY408EtxRSoK3Q==",
      "dependencies": {
        "js-tokens": "^3.0.0 || ^4.0.0"
      },
      "bin": {
        "loose-envify": "cli.js"
      }
    },
    "node_modules/react": {
      "version": "18.2.0",
      "resolved": "https://registry.npmjs.org/react/-/react-18.2.0.tgz",
      "integrity": "sha512-/3IjMdb2L9QbBdWiW5e3P2/npwMBaU9mHCSCUzNln0ZCYbcfTsGbTJrU/kGemdH2IWmB2ioZ+zkxtmq6g09fGQ==",
      "peer": true,
      "dependencies": {
        "loose-envify": "^1.1.0"
      },
      "engines": {
        "node": ">=0.10.0"
      }
    },
    "node_modules/react-dom": {
      "version": "18.2.0",
      "resolved": "https://registry.npmjs.org/react-dom/-/react-dom-18.2.0.tgz",
      "integrity": "sha512-6IMTriUmvsjHUjNtEDudZfuDQUoWXVxKHhlEGSk81n4YFS+r/Kl99wXiwlVXtPBtJenozv2P+hxDsw9eA7Xo6g==",
      "dependencies": {
        "loose-envify": "^1.1.0",
        "scheduler": "^0.23.0"
      },
      "peerDependencies": {
        "react": "^18.2.0"
      }
    },
    "node_modules/scheduler": {
      "version": "0.23.0",
      "resolved": "https://registry.npmjs.org/scheduler/-/scheduler-0.23.0.tgz",
      "integrity": "sha512-CtuThmgHNg7zIZWAXi3AsyIzA3n4xx7aNyjwC2VJldO2LMVDhFK+63xGqq6CsJH4rTAt6/M+N4GhZiDYPx9eUw==",
      "dependencies": {
        "loose-envify": "^1.1.0"
      }
    }
  }
}
```

</details>

<details>
  <summary>foo/package.json (workspace)</summary>

```jsonc
{
  "name": "foo",
  "version": "1.0.0",
  "description": "",
  "main": "index.js",
  "devDependencies": {},
  "scripts": {
    "test": "echo \"Error: no test specified\" && exit 1"
  },
  "author": "",
  "license": "ISC",
  "dependencies": {
      "is-positive": "github:kevva/is-positive"
  }
}
```

</details>

## Using fetched dependencies

See also [usage.md][] for a complete example of Hermeto usage.

Hermeto downloads the npm dependencies as tar archives into the `deps/npm/`
subpath of the output directory.

1. Dependencies fetched from npm registries are placed directly to this
   directory (array-flatten in the following example).
1. Dependencies downloaded from other HTTPS URL are placed to subdirectory
   `external-<tarball_name>` (bar-project in the following example).
1. Dependencies retrieved from Git repository are placed to `host, namespace,
   repo` subdirectories (foo-project in the following example).

```text
hermeto-output/deps/npm
├── array-flatten-1.1.1.tgz
├── bitbucket.org
│        └── foo-testing
│             └── foo-project
│                       └── foo-project-external-gitcommit-9e164b97043a2d91bbeb992f6cc68a3d1015086a.tgz
├── body-parser-1.20.1.tgz
├── bytes-3.1.2.tgz
│   ...
├── external-bar-project
│        └── bar-project-external-sha512-43e71f90ad5YOLO.tgz
│   ...
```

In order for the `npm install` command to use the fetched dependencies instead
of reaching for the npm registry, Hermeto needs to update
[project files](#project-files). These updates happen **automatically** when we
call Hermeto's [inject-files command][].

### Changes made by the inject-files command

The root 'package.json' file is updated together with 'package.json' files for
each [workspace][] with changes

- For git repositories and HTTPS URLs in dependencies update their value to an
  empty string

Hermeto command updates the following in the `package-lock.json` file

- Replace URLs found in resolved items with local paths to
  [fetched dependencies](#using-fetched-dependencies)
- Similarly to the above package.json changes, for git repositories and HTTPS
  URLs in package dependencies update their value to an empty string
- There is a corner case [bug][] which happens in older npm versions (spotted in
  8.12.1 version and lower) where npm mistakenly adds integrity checksum to git
  sources. To avoid errors while recreating git repository content as a tar
  archive and changing the integrity checksum, Hermeto deletes integrity items,
  which should not be there in the first place

### Updated project example

<details>
  <summary>package.json</summary>

```jsonc
{
  "name": "cachi2-npm-demo",
  "version": "1.0.0",
  "description": "",
  "main": "index.js",
  "scripts": {
    "test": "echo \"Error: no test specified\" && exit 1"
  },
  "author": "",
  "license": "ISC",
  "dependencies": {
    "react-dom": "^18.0.1",
    "@types/react-dom": "^18.0.1",
    "bitbucket-cachi2-npm-without-deps-second": "",
    "cachito-npm-without-deps": "",
    "fecha": "file:fecha-4.2.3.tgz"
  },
  "workspaces": [
    "foo"
  ]
}
```

</details>

<details>
  <summary>package-lock.json</summary>

```jsonc
{
  "name": "cachi2-npm-demo",
  "version": "1.0.0",
  "lockfileVersion": 3,
  "requires": true,
  "packages": {
    "": {
      "name": "cachi2-npm-demo",
      "version": "1.0.0",
      "license": "ISC",
      "workspaces": [
        "foo"
      ],
      "dependencies": {
        "@types/react-dom": "^18.0.1",
        "bitbucket-cachi2-npm-without-deps-second": "",
        "cachito-npm-without-deps": "",
        "fecha": "file:fecha-4.2.3.tgz",
        "react-dom": "^18.0.1"
      }
    },
    "foo": {
      "version": "1.0.0",
      "license": "ISC",
      "dependencies": {
        "is-positive": ""
      },
      "devDependencies": {}
    },
    "node_modules/@types/prop-types": {
      "version": "15.7.5",
      "resolved": "file:///tmp/deps/npm/types-prop-types-15.7.5.tgz",
      "integrity": "sha512-JCB8C6SnDoQf0cNycqd/35A7MjcnK+ZTqE7judS6o7utxUCg6imJg3QK2qzHKszlTjcj2cn+NwMB2i96ubpj7w=="
    },
    "node_modules/@types/react": {
      "version": "18.2.18",
      "resolved": "file:///tmp/deps/npm/types-react-18.2.18.tgz",
      "integrity": "sha512-da4NTSeBv/P34xoZPhtcLkmZuJ+oYaCxHmyHzwaDQo9RQPBeXV+06gEk2FpqEcsX9XrnNLvRpVh6bdavDSjtiQ==",
      "dependencies": {
        "@types/prop-types": "*",
        "@types/scheduler": "*",
        "csstype": "^3.0.2"
      }
    },
    "node_modules/@types/react-dom": {
      "version": "18.2.7",
      "resolved": "file:///tmp/deps/npm/types-react-dom-18.2.7.tgz",
      "integrity": "sha512-GRaAEriuT4zp9N4p1i8BDBYmEyfo+xQ3yHjJU4eiK5NDa1RmUZG+unZABUTK4/Ox/M+GaHwb6Ow8rUITrtjszA==",
      "dependencies": {
        "@types/react": "*"
      }
    },
    "node_modules/@types/scheduler": {
      "version": "0.16.3",
      "resolved": "file:///tmp/deps/npm/types-scheduler-0.16.3.tgz",
      "integrity": "sha512-5cJ8CB4yAx7BH1oMvdU0Jh9lrEXyPkar6F9G/ERswkCuvP4KQZfZkSjcMbAICCpQTN4OuZn8tz0HiKv9TGZgrQ=="
    },
    "node_modules/bitbucket-cachi2-npm-without-deps-second": {
      "version": "2.0.0",
      "resolved": "file:///tmp/deps/npm/bitbucket.org/cachi-testing/cachi2-without-deps-second/cachi2-without-deps-second-external-gitcommit-09992d418fc44a2895b7a9ff27c4e32d6f74a982.tgz"
    },
    "node_modules/cachito-npm-without-deps": {
      "version": "1.0.0",
      "resolved": "file:///tmp/deps/npm/external-cachito-npm-without-deps/cachito-npm-without-deps-external-sha512-43e71f90ad5f9eb349ab18a283f8954994def373962ddc61b866bdea4d48249e67913c6b84dca1e8c519e981ca1fcc62b438292104a88ee9ed72db76a41efede.tgz",
      "integrity": "sha512-Q+cfkK1fnrNJqxiig/iVSZTe83OWLdxhuGa96k1IJJ5nkTxrhNyh6MUZ6YHKH8xitDgpIQSojuntctt2pB7+3g=="
    },
    "node_modules/csstype": {
      "version": "3.1.2",
      "resolved": "file:///tmp/deps/npm/csstype-3.1.2.tgz",
      "integrity": "sha512-I7K1Uu0MBPzaFKg4nI5Q7Vs2t+3gWWW648spaF+Rg7pI9ds18Ugn+lvg4SHczUdKlHI5LWBXyqfS8+DufyBsgQ=="
    },
    "node_modules/fecha": {
      "version": "4.2.3",
      "resolved": "file:fecha-4.2.3.tgz",
      "integrity": "sha512-OP2IUU6HeYKJi3i0z4A19kHMQoLVs4Hc+DPqqxI2h/DPZHTm/vjsfC6P0b4jCMy14XizLBqvndQ+UilD7707Jw==",
      "license": "MIT"
    },
    "node_modules/foo": {
      "resolved": "foo",
      "link": true
    },
    "node_modules/is-positive": {
      "version": "3.1.0",
      "resolved": "file:///tmp/deps/npm/github.com/kevva/is-positive/is-positive-external-gitcommit-97edff6f525f192a3f83cea1944765f769ae2678.tgz",
      "license": "MIT",
      "engines": {
        "node": ">=0.10.0"
      }
    },
    "node_modules/js-tokens": {
      "version": "4.0.0",
      "resolved": "file:///tmp/deps/npm/js-tokens-4.0.0.tgz",
      "integrity": "sha512-RdJUflcE3cUzKiMqQgsCu06FPu9UdIJO0beYbPhHN4k6apgJtifcoCtT9bcxOpYBtpD2kCM6Sbzg4CausW/PKQ=="
    },
    "node_modules/loose-envify": {
      "version": "1.4.0",
      "resolved": "file:///tmp/deps/npm/loose-envify-1.4.0.tgz",
      "integrity": "sha512-lyuxPGr/Wfhrlem2CL/UcnUc1zcqKAImBDzukY7Y5F/yQiNdko6+fRLevlw1HgMySw7f611UIY408EtxRSoK3Q==",
      "dependencies": {
        "js-tokens": "^3.0.0 || ^4.0.0"
      },
      "bin": {
        "loose-envify": "cli.js"
      }
    },
    "node_modules/react": {
      "version": "18.2.0",
      "resolved": "file:///tmp/deps/npm/react-18.2.0.tgz",
      "integrity": "sha512-/3IjMdb2L9QbBdWiW5e3P2/npwMBaU9mHCSCUzNln0ZCYbcfTsGbTJrU/kGemdH2IWmB2ioZ+zkxtmq6g09fGQ==",
      "peer": true,
      "dependencies": {
        "loose-envify": "^1.1.0"
      },
      "engines": {
        "node": ">=0.10.0"
      }
    },
    "node_modules/react-dom": {
      "version": "18.2.0",
      "resolved": "file:///tmp/deps/npm/react-dom-18.2.0.tgz",
      "integrity": "sha512-6IMTriUmvsjHUjNtEDudZfuDQUoWXVxKHhlEGSk81n4YFS+r/Kl99wXiwlVXtPBtJenozv2P+hxDsw9eA7Xo6g==",
      "dependencies": {
        "loose-envify": "^1.1.0",
        "scheduler": "^0.23.0"
      },
      "peerDependencies": {
        "react": "^18.2.0"
      }
    },
    "node_modules/scheduler": {
      "version": "0.23.0",
      "resolved": "file:///tmp/deps/npm/scheduler-0.23.0.tgz",
      "integrity": "sha512-CtuThmgHNg7zIZWAXi3AsyIzA3n4xx7aNyjwC2VJldO2LMVDhFK+63xGqq6CsJH4rTAt6/M+N4GhZiDYPx9eUw==",
      "dependencies": {
        "loose-envify": "^1.1.0"
      }
    }
  }
}
```

</details>

<details>
  <summary>foo/package.json (workspace)</summary>

```jsonc
{
  "name": "foo",
  "version": "1.0.0",
  "description": "",
  "main": "index.js",
  "devDependencies": {},
  "scripts": {
    "test": "echo \"Error: no test specified\" && exit 1"
  },
  "author": "",
  "license": "ISC",
  "dependencies": {
      "is-positive": ""
  }
}
```

</details>

[usage.md]: usage.md
[inject-files command]: usage.md#inject-project-files-npm

[bug]: https://github.com/npm/cli/issues/2846
[npm]: https://www.npmjs.com
[npm docs]: https://docs.npmjs.com
[npm documentation]: https://docs.npmjs.com/cli/v9/configuring-npm/package-json#dependencies
[npm install]: https://docs.npmjs.com/cli/v9/commands/npm-install?v=true
[package-lock.json]: https://docs.npmjs.com/cli/v9/configuring-npm/package-lock-json
[package.json]: https://docs.npmjs.com/cli/v9/configuring-npm/package-json
[workspace]: https://docs.npmjs.com/cli/v9/using-npm/workspaces?v=true
