package glide

import (
	"testing"
)

func TestGlideFalse(t *testing.T) {
	glide, err := LoadGlide("../testdata/glide/")
	if err != nil {
		t.Fatal("failed to load the lock file", err)
	}
	if glide.Imports[0].Name != "github.com/pborman/uuid" {
		t.Fatalf("expected '%v', got '%v'", "github.com/pborman/uuid", glide.Imports[0].Name)
	}
	if glide.Imports[0].Version != "ca53cad383cad2479bbba7f7a1a05797ec1386e4" {
		t.Fatalf("expected '%v', got '%v'", "ca53cad383cad2479bbba7f7a1a05797ec1386e4", glide.Imports[0].Version)
	}
	if len(glide.Imports) != 2 {
		t.Fatalf("expected '%v', got '%v'", 2, len(glide.Imports))
	}
	if glide.Package != "github.com/release-engineering/retrodep/testdata/glide" {
		t.Fatalf("expected '%v', got '%v'", "github.com/release-engineering/retrodep/testdata/glide", glide.Package)
	}
}
