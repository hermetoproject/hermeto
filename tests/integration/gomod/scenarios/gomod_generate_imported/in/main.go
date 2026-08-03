package main

import "github.com/hermetoproject/integration-tests/foobar"

//go:generate go run internal/generate/generatefoobar.go

func main() {
	foobar.Output()
}
