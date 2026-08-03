package utils

import (
	"github.com/google/uuid"
)

// GenerateSessionID creates a unique identifier
func GenerateSessionID() string {
	return uuid.New().String()
}

// ValidateSessionID checks if a string is a valid UUID
func ValidateSessionID(id string) bool {
	_, err := uuid.Parse(id)
	return err == nil
}
