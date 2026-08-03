module github.com/hermetoproject/integration-tests

go 1.15

require github.com/hermetoproject/some-module v0.0.0

replace github.com/hermetoproject/some-module => ./staging/src/some-module
