package gomod

import "testing"

func TestHelloFromDeps(t *testing.T) {
	want := "Hello, world."
	if got := HelloFromDeps(); got != want {
		t.Errorf("HelloFromDeps() = %q, want %q", got, want)
	}
}

