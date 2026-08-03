# go-generate-imported

Repo for testing how Hermeto handles packages, specifically for repos which use `go generate`. See [go generate](https://go.dev/blog/generate). <br/>
Such repos can be identified with a `//go:generate ...` comment in the `main.go` file. <br/>

This is the case where directory foobar is empty and `main.go` imports package `foobar`. Hermeto request recognizes "main" as a package and "foobar" as a dependency (1pkg, 1dep) <br/>
├── foobar <br/>
│   └── \<empty> <br/>
└── main.go ("package main") *IMPORTS FOOBAR <br/>
