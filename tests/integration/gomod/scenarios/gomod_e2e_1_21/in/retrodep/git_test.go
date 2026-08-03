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
	"os/exec"
	"strings"
	"testing"
	"time"

	"golang.org/x/tools/go/vcs"
)

func TestGitRevisions(t *testing.T) {
	defer mockExecCommand()()

	wt := gitWorkingTree{
		anyWorkingTree: anyWorkingTree{
			Dir: "",
			VCS: vcs.ByCmd(vcsGit),
		},
	}

	expectedRevs := []string{
		"d4c3dbfa77a74ae238e401d5d2197b45f30d8513",
		"a2176f4275f92ceddb47cff1e363313156124bf6",
	}
	mockedStdout = strings.Join(expectedRevs, "\n") + "\n"

	revs, err := wt.Revisions()
	if err != nil {
		t.Fatal(err)
	}
	if len(revs) != len(expectedRevs) {
		t.Fatalf("wrong number of revisions: got %d, want %d",
			len(revs), len(expectedRevs))
	}
	for i, rev := range expectedRevs {
		if revs[i] != rev {
			t.Fatalf("unexpected revisions: got %v, want %v",
				revs, expectedRevs)
		}
	}
}

func TestGitRevisionFromTag(t *testing.T) {
	defer mockExecCommand()()

	wt := gitWorkingTree{
		anyWorkingTree: anyWorkingTree{
			Dir: "",
			VCS: vcs.ByCmd(vcsGit),
		},
	}

	expected := "d4c3dbfa77a74ae238e401d5d2197b45f30d8513"
	mockedStdout = expected + "\n"
	rev, err := wt.RevisionFromTag("v1.0.0")
	if err != nil {
		t.Fatal(err)
	}
	if rev != expected {
		t.Errorf("unexpected revision: got %v, want %v", rev, expected)
	}
}

func TestGitRevSync(t *testing.T) {
	defer mockExecCommand()()

	wt := gitWorkingTree{
		anyWorkingTree: anyWorkingTree{
			Dir: "",
			VCS: vcs.ByCmd(vcsGit),
		},
	}

	revision := "d4c3dbfa77a74ae238e401d5d2197b45f30d8513"
	if err := wt.RevSync(revision); err != nil {
		t.Errorf("unexpected error: RevSync(%q): %s", revision, err)
	}
}

func TestGitTimeFromRevision(t *testing.T) {
	defer mockExecCommand()()

	wt := gitWorkingTree{
		anyWorkingTree: anyWorkingTree{
			Dir: "",
			VCS: vcs.ByCmd(vcsGit),
		},
	}

	for _, stdout := range []string{
		"",
		"warning: inexact rename detection was skipped due to too many files.",
	} {
		timeStr := []byte("2018-09-20T16:47:29+01:00")
		mockedStdout = string(timeStr) + "\n"
		mockedStderr = stdout
		var expected time.Time
		expected.UnmarshalText(timeStr)

		tm, err := wt.TimeFromRevision("d4c3dbfa77a74ae238e401d5d2197b45f30d8513")
		if err != nil {
			t.Error(err)
			continue
		}

		if !tm.Equal(expected) {
			t.Errorf("unexpected time: got %s, want %s", tm, expected)
		}
	}
}

func TestGitReachableTag(t *testing.T) {
	defer mockExecCommand()()

	wt := gitWorkingTree{
		anyWorkingTree: anyWorkingTree{
			Dir: "",
			VCS: vcs.ByCmd(vcsGit),
		},
	}

	type tcase struct {
		name       string
		stdout     string
		stderr     string
		exit       int
		expSuccess bool
		expTag     string
	}
	tcases := []tcase{
		tcase{
			name:   "no-annotated-tags",
			stderr: "fatal: No annotated tags can describe '...'.\n",
			exit:   128,
		},

		tcase{
			name:   "no-tags-can-describe",
			stderr: "fatal: No tags can describe '...'.\n",
			exit:   128,
		},

		tcase{
			name:   "no-names-found",
			stderr: "fatal: No names found, cannot describe anything.\n",
			exit:   128,
		},

		tcase{
			name:       "exact",
			stdout:     "v1.2.0",
			expSuccess: true,
			expTag:     "v1.2.0",
		},

		// TODO: Need support for mocking multiple commands in
		// order to test parsing output like
		// v1.2.0-27-ga0220d4
	}

	revision := "d4c3dbfa77a74ae238e401d5d2197b45f30d8513"
	for _, tc := range tcases {
		mockedStdout = tc.stdout
		mockedStderr = tc.stderr
		mockedExitStatus = tc.exit
		tag, err := wt.ReachableTag(revision)
		if tc.expSuccess {
			if err != nil {
				t.Errorf("unexpected failure: %s: %s", tc.name, err)
			} else if tag != tc.expTag {
				t.Errorf("incorrect tag: %s: %s", tc.name, tag)
			}
		} else if err != ErrorVersionNotFound {
			t.Errorf("unexpected error: %s: %s", tc.name, err)
		}
	}
}

func TestGitFileHashesFromRef(t *testing.T) {
	defer mockExecCommand()()

	wt := gitWorkingTree{
		anyWorkingTree: anyWorkingTree{
			Dir: "",
			VCS: vcs.ByCmd(vcsGit),
		},
	}

	mockedStdout = "what?"
	_, err := wt.FileHashesFromRef("HEAD", "")
	if err == nil {
		t.Error("invalid output not reported as error")
	}

	mockedStdout = strings.Join([]string{
		"100644 blob e69de29bb2d1d6434b8b29ae775ad8c2e48c5391\tignored.go",
		"100644 blob e69de29bb2d1d6434b8b29ae775ad8c2e48c5391\tvendor/github.com/eggs/ham/ham.go",
		"100644 blob e69de29bb2d1d6434b8b29ae775ad8c2e48c5391\tvendor/github.com/foo/bar/bar.go",
		// Test we can parse filenames that include spaces
		"100644 blob e69de29bb2d1d6434b8b29ae775ad8c2e48c5391\tvendor/github.com/foo/bar/bar baz.go",
	}, "\n") + "\n"

	h, err := wt.FileHashesFromRef("HEAD", "")
	if err != nil {
		t.Fatal(err)
	}

	emptyhash := "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"
	expected := map[string]FileHash{
		"ignored.go":                           FileHash(emptyhash),
		"vendor/github.com/eggs/ham/ham.go":    FileHash(emptyhash),
		"vendor/github.com/foo/bar/bar.go":     FileHash(emptyhash),
		"vendor/github.com/foo/bar/bar baz.go": FileHash(emptyhash),
	}

	if len(h) != len(expected) {
		t.Fatalf("wrong number of files: got %d, want %d",
			len(h), len(expected))
	}

	for f, hash := range expected {
		if h[f] != hash {
			t.Fatalf("wrong filehashes: got %v, want %v",
				h, expected)
		}
	}
}

func TestGitErrors(t *testing.T) {
	defer mockExecCommand()()

	wt := gitWorkingTree{
		anyWorkingTree: anyWorkingTree{
			Dir: "",
			VCS: vcs.ByCmd(vcsGit),
		},
	}

	mockedStderr = "fatal: not a git repository\n"
	mockedExitStatus = 128

	_, err := wt.Revisions()
	if err == nil {
		t.Fatal("git failure was not reported")
	}
	_, err = wt.RevisionFromTag("tip")
	if _, ok := err.(*exec.ExitError); !ok {
		t.Error("RevisionsFromTag: git failure was not reported")
	}
	err = wt.RevSync("tip")
	if _, ok := err.(*exec.ExitError); !ok {
		t.Error("RevSync: git failure was not reported")
	}
	_, err = wt.TimeFromRevision("012345")
	if _, ok := err.(*exec.ExitError); !ok {
		t.Error("TimeFromRevision: git failure was not reported")
	}
	_, err = wt.ReachableTag("012345")
	if _, ok := err.(*exec.ExitError); !ok {
		t.Error("ReachableTag: git failure was not reported")
	}
	_, err = wt.FileHashesFromRef("012345", "")
	if _, ok := err.(*exec.ExitError); !ok {
		t.Error("FileHashesFromRef: git failure was not reported")
	}

	mockedStderr = "fatal: Not a valid object name 012345\n"
	_, err = wt.FileHashesFromRef("012345", "")
	if err != ErrorInvalidRef {
		t.Error("FileHashesFromRef: missing ErrorInvalidRef")
	}

	mockedStderr = "fatal: not a tree object\n"
	_, err = wt.FileHashesFromRef("012345", "")
	if err != ErrorInvalidRef {
		t.Error("FileHashesFromRef: missing ErrorInvalidRef")
	}

	hasher := &gitHasher{}
	_, err = hasher.Hash("", "")
	if _, ok := err.(*exec.ExitError); !ok {
		t.Error("Hash: git failure was not reported")
	}
}
