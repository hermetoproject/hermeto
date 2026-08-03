package main

import (
	"fmt"
	"github.com/hermetoproject/integration-tests/gomod-vendor-non-vendor-mix/vendored-module/utils"
)

func main() {
	sessionID := utils.GenerateSessionID()
	fmt.Printf("Generated Session ID: %s\n", sessionID)

	isValid := utils.ValidateSessionID(sessionID)
	fmt.Printf("Session ID valid: %v\n", isValid)
}
