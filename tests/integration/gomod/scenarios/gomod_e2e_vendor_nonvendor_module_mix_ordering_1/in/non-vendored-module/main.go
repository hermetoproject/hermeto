package main

import (
	"fmt"

	"github.com/hermetoproject/integration-tests/gomod-vendor-non-vendor-mix/vendored-module/utils"
)

func main() {
	fmt.Println("Non-vendored module calling vendored module:")
	sessionID := utils.GenerateSessionID()
	fmt.Printf("Session ID from vendored module: %s\n", sessionID)

	isValid := utils.ValidateSessionID(sessionID)
	fmt.Printf("Validation result: %v\n", isValid)
}
