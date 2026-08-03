// Copyright (C) 2018 Tim Waugh
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

func TestHgLog(t *testing.T) {
	defer mockExecCommand()()

	h := hgWorkingTree{
		anyWorkingTree: anyWorkingTree{
			Dir: "",
			VCS: vcs.ByCmd(vcsHg),
		},
	}

	mockedStdout = strings.TrimSpace(`
		<?xml version="1.0"?>
		<log>
		</log>
	`) + "\n"
	_, err := h.log(nil, 1)
	if err == nil {
		t.Error("incorrect err for unexpected log output")
	}

	mockedStdout = strings.TrimSpace(`
		<?xml version="1.0"?>
		<log>
	`) + "\n"
	_, err = h.log(nil, 1)
	if err == nil {
		t.Error("incorrect err for invalid log output")
	}
}

func TestHgRevisions(t *testing.T) {
	defer mockExecCommand()()

	wt := hgWorkingTree{
		anyWorkingTree: anyWorkingTree{
			Dir: "",
			VCS: vcs.ByCmd(vcsHg),
		},
	}

	expectedRevs := []string{
		"d4c3dbfa77a74ae238e401d5d2197b45f30d8513",
		"a2176f4275f92ceddb47cff1e363313156124bf6",
	}
	mockedStdout = strings.TrimSpace(`
		<?xml version="1.0"?>
		<log>
		<logentry revision="1" node="d4c3dbfa77a74ae238e401d5d2197b45f30d8513">
		<tag>tip</tag>
		<author email="example@example.com">Example</author>
		<date>2018-09-20T12:00:00+00:00</date>
		<msg xml:space="preserve">example</msg>
		</logentry>
		<logentry revision="0" node="a2176f4275f92ceddb47cff1e363313156124bf6">
		<tag>tip</tag>
		<author email="example@example.com">Example</author>
		<date>2018-09-20T12:00:00+00:00</date>
		<msg xml:space="preserve">example</msg>
		</logentry>
		</log>
	`) + "\n"

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

func TestHgRevisionFromTag(t *testing.T) {
	defer mockExecCommand()()

	wt := hgWorkingTree{
		anyWorkingTree: anyWorkingTree{
			Dir: "",
			VCS: vcs.ByCmd(vcsHg),
		},
	}

	expected := "d4c3dbfa77a74ae238e401d5d2197b45f30d8513"
	mockedStdout = strings.TrimSpace(`
		<?xml version="1.0"?>
		<log>
		<logentry revision="1" node="d4c3dbfa77a74ae238e401d5d2197b45f30d8513">
		<tag>tip</tag>
		<author email="example@example.com">Example</author>
		<date>2018-09-20T12:00:00+00:00</date>
		<msg xml:space="preserve">example</msg>
		</logentry>
		</log>
	`) + "\n"
	rev, err := wt.RevisionFromTag("tip")
	if err != nil {
		t.Fatal(err)
	}

	if rev != expected {
		t.Errorf("unexpected revision: got %v, want %v", rev, expected)
	}
}

func TestHgTimeFromRevision(t *testing.T) {
	defer mockExecCommand()()

	wt := hgWorkingTree{
		anyWorkingTree: anyWorkingTree{
			Dir: "",
			VCS: vcs.ByCmd(vcsHg),
		},
	}

	revision := "d4c3dbfa77a74ae238e401d5d2197b45f30d8513"
	mockedStdout = strings.TrimSpace(`
		<?xml version="1.0"?>
		<log>
		<logentry revision="1" node="d4c3dbfa77a74ae238e401d5d2197b45f30d8513">
		<tag>tip</tag>
		<author email="example@example.com">Example</author>
		<date>2018-09-20T12:00:00+00:00</date>
		<msg xml:space="preserve">example</msg>
		</logentry>
		</log>
	`) + "\n"
	tm, err := wt.TimeFromRevision(revision)
	if err != nil {
		t.Fatal(err)
	}

	var expected time.Time
	expected.UnmarshalText([]byte("2018-09-20T12:00:00+00:00"))
	if !tm.Equal(expected) {
		t.Errorf("unexpected time: got %s, want %s", tm, expected)
	}
}

func TestHgReachableTag(t *testing.T) {
	defer mockExecCommand()()

	wt := hgWorkingTree{
		anyWorkingTree: anyWorkingTree{
			Dir: "",
			VCS: vcs.ByCmd(vcsHg),
		},
	}

	type tcase struct {
		name       string
		stdout     string
		expSuccess bool
		expTag     string
	}
	tcases := []tcase{
		tcase{
			name: "no-tags",
			stdout: `
	<?xml version="1.0"?>
	<log>
	</log>
			`,
			expSuccess: false,
		},

		tcase{
			name: "no-semver",
			stdout: `
	<?xml version="1.0"?>
	<log>
	<logentry revision="1" node="67639326a575e4f7db1f3f70697bf096af1cbe8d">
	<tag>v1.0.1beta1</tag>
	<author email="example@example.com">Example</author>
	<date>2018-09-20T12:00:00+00:00</date>
	<msg xml:space="preserve">example</msg>
	</logentry>
	</log>
	`,
			expSuccess: true,
			expTag:     "v1.0.1beta1",
		},

		tcase{
			name: "semver-after-non-semver",
			stdout: `
	<?xml version="1.0"?>
	<log>
	<logentry revision="1" node="67639326a575e4f7db1f3f70697bf096af1cbe8d">
	<tag>v1.0.1beta1</tag>
	<author email="example@example.com">Example</author>
	<date>2018-09-20T12:00:00+00:00</date>
	<msg xml:space="preserve">example</msg>
	</logentry>
	<logentry revision="1" node="67639326a575e4f7db1f3f70697bf096af1cbe8d">
	<tag>v1.0.0</tag>
	<author email="example@example.com">Example</author>
	<date>2018-09-20T12:00:00+00:00</date>
	<msg xml:space="preserve">example</msg>
	</logentry>
	</log>
	`,
			expSuccess: true,
			expTag:     "v1.0.0",
		},
	}

	revision := "d4c3dbfa77a74ae238e401d5d2197b45f30d8513"
	for _, tc := range tcases {
		mockedStdout = strings.TrimSpace(tc.stdout) + "\n"
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

func TestHgErrors(t *testing.T) {
	defer mockExecCommand()()

	wt := hgWorkingTree{
		anyWorkingTree: anyWorkingTree{
			Dir: "",
			VCS: vcs.ByCmd(vcsHg),
		},
	}

	mockedStderr = "abort: no repository found in '...' (.hg not found)!\n"
	mockedExitStatus = 255

	_, err := wt.Revisions()
	if _, ok := err.(*exec.ExitError); !ok {
		t.Error("Revisions: hg failure was not reported")
	}
	_, err = wt.RevisionFromTag("tip")
	if _, ok := err.(*exec.ExitError); !ok {
		t.Error("RevisionsFromTag: hg failure was not reported")
	}
	_, err = wt.TimeFromRevision("012345")
	if _, ok := err.(*exec.ExitError); !ok {
		t.Error("TimeFromRevision: hg failure was not reported")
	}
	_, err = wt.ReachableTag("012345")
	if _, ok := err.(*exec.ExitError); !ok {
		t.Error("ReachableTag: hg failure was not reported")
	}
	_, err = wt.FileHashesFromRef("012345", "")
	if _, ok := err.(*exec.ExitError); !ok {
		t.Error("FileHashesFromRef: hg failure was not reported")
	}
}
