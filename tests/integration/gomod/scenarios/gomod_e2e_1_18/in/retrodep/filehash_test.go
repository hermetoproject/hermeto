// Copyright (C) 2018, 2019 Tim Waugh
//
// This program is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU General Public License for more details.
//
// You should have received a copy of the GNU General Public License
// along with this program.  If not, see <http://www.gnu.org/licenses/>.

package retrodep

import (
	"sort"
	"testing"
)

func TestSha256Hasher(t *testing.T) {
	h := sha256Hasher{}
	// from sha256sum:
	emptysum := FileHash("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")
	fh, err := h.Hash("", "testdata/gosource/ignored.go")
	if err != nil {
		t.Fatal(err)
	}
	if fh != emptysum {
		t.Errorf("unexpected hash: got %s, want %s", fh, emptysum)
	}
}

func TestNewFileHashes(t *testing.T) {
	hashes, err := NewFileHashes(&gitHasher{}, "testdata/gosource", nil)
	if err != nil {
		t.Fatal(err)
	}
	if hashes == nil {
		t.Fatal("NewFileHashes returned nil map")
	}
	emptyhash := FileHash("e69de29bb2d1d6434b8b29ae775ad8c2e48c5391")
	expected := map[string]FileHash{
		"ignored.go":                                 emptyhash,
		"vendor/github.com/foo/bar/bar.go":           emptyhash,
		"vendor/github.com/eggs/ham/ham.go":          emptyhash,
		"vendor/github.com/eggs/ham/spam/ignored.go": emptyhash,
	}
	if len(hashes) != len(expected) {
		t.Fatalf("len(hashes[%v]) != %d", hashes, len(expected))
	}
	for key, value := range expected {
		got, ok := hashes[key]
		if !ok {
			t.Errorf("%s missing", key)
			continue
		}
		if got != value {
			t.Errorf("%s: wrong hash (%s != %s)", key, got, value)
		}
	}
}

func TestNewFileHashesExclude(t *testing.T) {
	excludes := make(map[string]struct{})
	excludes["testdata/gosource/ignored.go"] = struct{}{}
	hashes, err := NewFileHashes(&gitHasher{}, "testdata/gosource", excludes)
	if err != nil {
		t.Fatal(err)
	}
	emptyhash := FileHash("e69de29bb2d1d6434b8b29ae775ad8c2e48c5391")
	expected := map[string]FileHash{
		"vendor/github.com/foo/bar/bar.go":           emptyhash,
		"vendor/github.com/eggs/ham/ham.go":          emptyhash,
		"vendor/github.com/eggs/ham/spam/ignored.go": emptyhash,
	}
	if len(hashes) != len(expected) {
		t.Fatalf("len(hashes[%v]) != %d", hashes, len(expected))
	}
	for key, value := range expected {
		got, ok := hashes[key]
		if !ok {
			t.Errorf("%s missing", key)
			continue
		}
		if got != value {
			t.Errorf("%s: wrong hash (%s != %s)", key, got, value)
		}
	}
}

func TestIsSubsetOf(t *testing.T) {
	hasher := &gitHasher{}
	hashes, err := NewFileHashes(hasher, "testdata/gosource", nil)
	if err != nil {
		t.Fatal(err)
	}

	if !hashes.IsSubsetOf(hashes) {
		t.Fatalf("not subset of self")
	}

	other := make(FileHashes)
	for k, v := range hashes {
		other[k] = v
	}
	hashes["foo"] = FileHash("")
	if hashes.IsSubsetOf(other) {
		t.Fail()
	}
}

func TestMismatches(t *testing.T) {
	hasher := &gitHasher{}
	hashes, err := NewFileHashes(hasher, "testdata/gosource", nil)
	if err != nil {
		t.Fatal(err)
	}

	if hashes.Mismatches(hashes, false) != nil {
		t.Fatalf("mismatches self")
	}

	other := make(FileHashes)
	for k, v := range hashes {
		other[k] = v
	}

	other["foo"] = FileHash("")
	if hashes.Mismatches(hashes, false) != nil {
		t.Fatalf("extra value in s reported as mismatch")
	}

	eq := func(a sort.StringSlice, b sort.StringSlice) bool {
		if len(a) != len(b) {
			return false
		}
		a.Sort()
		b.Sort()
		for i, v := range a {
			if b[i] != v {
				return false
			}
		}
		return true
	}

	hashes["foo"] = FileHash("123")
	hashes["bar"] = FileHash("123")
	mismatches := hashes.Mismatches(other, false)
	if !eq(mismatches, []string{"foo", "bar"}) {
		t.Errorf("got %v, expected {\"foo\", \"bar\"}", mismatches)
	}

	mismatches = hashes.Mismatches(other, true)
	if len(mismatches) != 1 {
		t.Errorf("too many mismatches returned: %v", mismatches)
	}
}
