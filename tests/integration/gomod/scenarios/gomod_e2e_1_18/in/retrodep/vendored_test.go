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
	"testing"
)

func TestVendoredProjects(t *testing.T) {
	src, err := NewGoSource("testdata/gosource", nil)
	if err != nil {
		t.Fatal(err)
	}
	expected := []string{
		"github.com/eggs/ham",
		"github.com/foo/bar",
	}
	got, err := src.VendoredProjects()
	if err != nil {
		t.Fatal(err)
	}
	matched := len(got) == len(expected)
	if !matched {
		t.Errorf("%d != %d", len(got), len(expected))
	}
	if matched {
		for _, repo := range expected {
			if _, ok := got[repo]; !ok {
				t.Errorf("%s not returned", repo)
				matched = false
				break
			}
		}
	}
	if !matched {
		t.Errorf("%v != %v", got, expected)
	}
}

func TestChooseBestTag(t *testing.T) {
	tags := []string{
		"1.2.3-beta1",
		"1.2.2",
		"1.2.2-beta2",
	}
	best := chooseBestTag(tags)
	if best != "1.2.2" {
		t.Errorf("wrong best tag (%s)", best)
	}
}

type dummyHasher struct{}

func (h *dummyHasher) Hash(abs, rel string) (FileHash, error) {
	return "foo", nil
}

type mockVendorWorkingTree struct {
	stubWorkingTree

	localHashes FileHashes
}

const matchVersion = "v1.0.0"
const matchRevision = "0123456789abcdef"

func (wt *mockVendorWorkingTree) FileHashesFromRef(ref, _ string) (FileHashes, error) {
	if ref == matchRevision || ref == matchVersion {
		// Pretend v1.0.0 is an exact copy of the local files.
		return wt.localHashes, nil
	}

	// Pretend all other refs have no content at all.
	return make(FileHashes), nil
}

func (wt *mockVendorWorkingTree) RevisionFromTag(tag string) (string, error) {
	if tag != matchVersion {
		return "", ErrorVersionNotFound
	}
	return matchRevision, nil
}

func (wt *mockVendorWorkingTree) ReachableTag(rev string) (tag string, err error) {
	if rev == matchVersion {
		tag = rev
	} else {
		err = ErrorVersionNotFound
	}
	return
}

func (wt *mockVendorWorkingTree) VersionTags() ([]string, error) {
	return []string{"v2.0.0", "v1.0.0"}, nil
}

func TestDescribeProject(t *testing.T) {
	src, err := NewGoSource("testdata/gosource", nil)
	if err != nil {
		t.Fatal(err)
	}

	proj, err := src.Project("github.com/foo/bar")
	if err != nil {
		t.Fatal(err)
	}

	wt := &mockVendorWorkingTree{}
	wt.hasher = &dummyHasher{}

	// Make a copy of the local file hashes, so we can mock them
	// for "v1.0.0" in the working tree.
	wt.localHashes, err = src.hashLocalFiles(wt, proj, src.Path)
	if err != nil {
		t.Fatal(err)
	}

	ref, err := src.DescribeProject(proj, wt, src.Path, nil)
	if err != nil {
		t.Fatal(err)
	}

	if ref.Ver != matchVersion {
		t.Errorf("Version: got %s but expected %s", ref.Ver, matchVersion)
	}
	if ref.Rev != matchRevision {
		t.Errorf("Revision: got %s but expected %s", ref.Rev, matchRevision)
	}
}
