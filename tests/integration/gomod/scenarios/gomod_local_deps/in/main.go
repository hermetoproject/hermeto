package main

import (
	"fmt"
	"github.com/hermetoproject/some-module"
	"github.com/hermetoproject/some-module/some-package"
)


func main() {
	fmt.Println("Hello, local dependencies.")
	some_module.Hi()
	some_package.Hi()
}
