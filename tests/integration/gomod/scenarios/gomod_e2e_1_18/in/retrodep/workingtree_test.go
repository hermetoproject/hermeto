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
	"bytes"
	"io"
	"strings"
	"testing"
	"time"

	"golang.org/x/tools/go/vcs"
)

// mockDescribable is a mock for the Describable interface used by
// PseudoVersion.
type mockDescribable struct {
	// The Test function context
	t *testing.T

	// name of the test
	name string

	// Expected parameter for ReachableTag and TimeFromRevision
	rev string

	// Result from ReachableTag
	tag    string
	tagErr error

	// Whether the TimeFromRevision method was called
	timeFromRevisionCalled bool

	// Result from TimeFromRevision
	time    time.Time
	timeErr error
}

func (d *mockDescribable) ReachableTag(rev string) (string, error) {
	if rev != d.rev {
		d.t.Errorf("%s: ReachableTag called with %q but wanted %q",
			d.name, rev, d.rev)
	}

	return d.tag, d.tagErr
}

func (d *mockDescribable) TimeFromRevision(rev string) (time.Time, error) {
	d.timeFromRevisionCalled = true
	if rev != d.rev {
		d.t.Errorf("%s: TimeFromRevision called with %q but wanted %q",
			d.name, rev, d.rev)
	}

	return d.time, d.timeErr
}

func TestPseudoVersion(t *testing.T) {
	type tcase struct {
		m                      mockDescribable
		pv                     string
		err                    error
		timeFromRevisionCalled bool
	}

	tm := time.Date(2006, 1, 2, 15, 4, 5, 0, time.UTC)
	rev := "d4c3dbfa77a74ae238e401d5d2197b45f30d8513"
	tcases := []tcase{
		tcase{
			m: mockDescribable{
				name:   "reachable-err",
				tagErr: io.EOF, // random error
			},
			err: io.EOF, // should be reported to caller
		},

		tcase{
			m: mockDescribable{
				name:    "time-err",
				tag:     "v1.2.0",
				timeErr: io.EOF,
			},
			timeFromRevisionCalled: true,
			err:                    io.EOF,
		},

		tcase{
			m: mockDescribable{
				name:   "no-reachable",
				tagErr: ErrorVersionNotFound,
			},
			pv:                     "v0.0.0-0.20060102150405-d4c3dbfa77a7",
			timeFromRevisionCalled: true,
		},

		tcase{
			m: mockDescribable{
				name: "reachable-nonsemver",
				tag:  "v1.2.0beta1",
			},
			pv:                     "v1.2.0beta1-1.20060102150405-d4c3dbfa77a7",
			timeFromRevisionCalled: true,
		},

		tcase{
			m: mockDescribable{
				name: "reachable-semver",
				tag:  "v1.2.0",
			},
			pv:                     "v1.2.1-0.20060102150405-d4c3dbfa77a7",
			timeFromRevisionCalled: true,
		},

		tcase{
			m: mockDescribable{
				name: "reachable-presemver",
				tag:  "v1.2.0-pre1",
			},
			pv:                     "v1.2.0-pre1.0.20060102150405-d4c3dbfa77a7",
			timeFromRevisionCalled: true,
		},
	}

	for _, tc := range tcases {
		m := tc.m
		m.t = t
		m.rev = rev
		m.time = tm

		pv, err := PseudoVersion(&m, rev)
		if err != tc.err {
			t.Errorf("%s: got %s, want %s", m.name, err, tc.err)
			continue
		} else if pv != tc.pv {
			t.Errorf("%s: got %q, want %q", m.name, pv, tc.pv)
		}

		if tc.timeFromRevisionCalled != m.timeFromRevisionCalled {
			t.Errorf("%s: TimeFromRevision called: %t (wanted %t)",
				m.name, m.timeFromRevisionCalled, tc.timeFromRevisionCalled)
		}
	}
}

// stubWorkingTree is used to build mocks for WorkingTree.
type stubWorkingTree struct{ anyWorkingTree }

func (wt *stubWorkingTree) TagSync(tag string) error {
	return nil
}

func (wt *stubWorkingTree) VersionTags() ([]string, error) {
	return nil, nil
}

func (wt *stubWorkingTree) Revisions() ([]string, error) {
	return nil, nil
}

func (wt *stubWorkingTree) FileHashesFromRef(ref, subPath string) (FileHashes, error) {
	return make(FileHashes), nil
}

func (wt *stubWorkingTree) RevSync(rev string) error {
	return nil
}

func (wt *stubWorkingTree) RevisionFromTag(tag string) (string, error) {
	return "", nil
}

func (wt *stubWorkingTree) ReachableTag(rev string) (string, error) {
	return "", nil
}

func (wt *stubWorkingTree) TimeFromRevision(rev string) (time.Time, error) {
	return time.Time{}, nil
}

func (wt *stubWorkingTree) Hasher() Hasher {
	return &sha256Hasher{}
}

func TestStripImportCommentPackage(t *testing.T) {
	wt := &gitWorkingTree{
		anyWorkingTree: anyWorkingTree{
			Dir: "testdata/godep",
			VCS: vcs.ByCmd("git"),
		},
	}

	w := bytes.NewBuffer(nil)
	changed, err := wt.StripImportComment("importcomment.go", w)
	if err != nil {
		t.Fatal(err)
	}
	if !changed {
		t.Fatalf("changed is incorrect")
	}

	if w.String() != "package foo\n" {
		t.Fatalf("contents incorrect: %v", w.Bytes())
	}
}

func TestStripImportCommentNewline(t *testing.T) {
	wt := &gitWorkingTree{
		anyWorkingTree: anyWorkingTree{
			Dir: "testdata/godep",
			VCS: vcs.ByCmd("git"),
		},
	}

	w := bytes.NewBuffer(nil)
	changed, err := wt.StripImportComment("nonl.go", w)
	if err != nil {
		t.Fatal(err)
	}
	if !changed {
		t.Fatalf("changed is incorrect")
	}

	b := w.Bytes()
	if b[len(b)-1] != '\n' {
		t.Fatalf("missing newline: %v", w.Bytes())
	}

	w.Reset()
	changed, err = wt.StripImportComment("nl.go", w)
	if err != nil {
		t.Fatal(err)
	}
	if changed {
		t.Fatalf("changed is incorrect")
	}

	w.Reset()
	changed, err = wt.StripImportComment("nonl.txt", w)
	if err != nil {
		t.Fatal(err)
	}
	if changed {
		t.Fatalf("changed is incorrect")
	}
}

func TestDiff(t *testing.T) {
	defer mockExecCommand()()

	wt := &gitWorkingTree{
		anyWorkingTree: anyWorkingTree{
			Dir: "testdata/gosource",
			VCS: vcs.ByCmd("git"),
		},
	}

	// This will be the file contents *and* the output of 'diff -u'.
	mockedStdout = "--- ignored.go\n+++ignored.go\n@@ -0,0 +1 @@\n+foo\n"
	mockedExitStatus = 1

	captured := &strings.Builder{}
	changes, err := wt.Diff(captured, "ignored.go", "ignored.go")
	if err != nil {
		t.Fatal(err)
	}

	if changes != true {
		t.Errorf("changes: got %t, expected %t", changes, true)
	}

	if captured.String() != mockedStdout {
		t.Errorf("got %q, wanted %q", captured.String(), mockedStdout)
	}
}
