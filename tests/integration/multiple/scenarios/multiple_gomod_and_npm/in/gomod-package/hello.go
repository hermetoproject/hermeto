package main

import (
	"fmt"

	"github.com/sirupsen/logrus"
)

func main() {
	logrus.SetLevel(logrus.DebugLevel)
	fmt.Println("Hello, world.")
}
