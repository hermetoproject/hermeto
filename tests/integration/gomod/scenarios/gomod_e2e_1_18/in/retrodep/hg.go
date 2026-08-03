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
	"encoding/xml"
	"fmt"
	"io/ioutil"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/Masterminds/semver"
	"github.com/pkg/errors"
)

// This file contains methods specific to working with hg.

type hgWorkingTree struct {
	anyWorkingTree
}

type hgLogEntry struct {
	Node string `xml:"node,attr"`
	Date []byte `xml:"date"`
	Tag  string `xml:"tag"`
}
type hgLogs struct {
	XMLName    xml.Name     `xml:"log"`
	LogEntries []hgLogEntry `xml:"logentry"`
}

/// log runs 'hg log --template xml', with the additional args if args
/// is not nil, and returns the log entries. If expect is not 0, an
/// error is returned if the number of log entries is different.
func (h *hgWorkingTree) log(args []string, expect int) ([]hgLogEntry, error) {
	logArgs := []string{"log", "--encoding", "utf-8", "--template", "xml"}
	if args != nil {
		logArgs = append(logArgs, args...)
	}
	stdout, stderr, err := h.run(logArgs...)
	if err != nil {
		h.showOutput(stdout, stderr)
		return nil, err
	}
	var logs hgLogs
	err = xml.Unmarshal(stdout.Bytes(), &logs)
	if err != nil {
		return nil, err
	}
	entries := logs.LogEntries
	if expect != 0 && len(entries) != expect {
		return nil, fmt.Errorf(
			"unexpected log output: %s: %d logentry elements (expected %d)",
			strings.Join(logArgs, " "), len(entries), expect)
	}
	return entries, nil
}

// Revisions returns all revisions in the hg repository, using 'hg log'.
func (h *hgWorkingTree) Revisions() ([]string, error) {
	entries, err := h.log(nil, 0)
	if err != nil {
		return nil, err
	}
	revisions := make([]string, 0)
	for _, entry := range entries {
		revisions = append(revisions, entry.Node)
	}
	return revisions, nil
}

// RevisionFromTag returns the revision for the given tag, using 'hg
// log -r "tag(...)"'.
func (h *hgWorkingTree) RevisionFromTag(tag string) (string, error) {
	entries, err := h.log([]string{"-r", "tag(" + tag + ")"}, 1)
	if err != nil {
		return "", err
	}
	return entries[0].Node, nil
}

// RevSync updates the working tree to reflect the revision rev, using
// 'hg update -r ...'. The working tree must not have been locally
// modified.
func (h *hgWorkingTree) RevSync(rev string) error {
	return h.anyWorkingTree.TagSync(rev)
}

// TimeFromRevision returns the commit timestamp for the revision
// rev, using 'hg log -r ...'.
func (h *hgWorkingTree) TimeFromRevision(rev string) (time.Time, error) {
	var t time.Time
	entries, err := h.log([]string{"-r", rev}, 1)
	if err != nil {
		return t, err
	}
	err = t.UnmarshalText(entries[0].Date)
	return t, err
}

// ReachableTag returns the most recent reachable semver tag, using hg
// log -r "ancestors(...) & tag(r're:...')". It fails with
// ErrorVersionNotFound if no suitable tag is found.
func (h *hgWorkingTree) ReachableTag(rev string) (string, error) {
	// Find up to 10 reachable tags from the revision that might be semver tags
	revset := "ancestors(" + rev + ") & tag(r're:v?[0-9]')"
	entries, err := h.log([]string{"-r", revset, "--limit", "10"}, 0)
	if err != nil {
		return "", err
	}

	if len(entries) == 0 {
		return "", ErrorVersionNotFound
	}

	// If any is a semver tag, use that
	for _, entry := range entries {
		_, err := semver.NewVersion(entry.Tag)
		if err == nil {
			return entry.Tag, nil
		}
	}

	// Otherwise just take the first one
	return entries[0].Tag, nil
}

// FileHashesFromRef returns the file hashes for the given tag or
// revision ref.
func (h *hgWorkingTree) FileHashesFromRef(ref, subPath string) (FileHashes, error) {
	dir, err := ioutil.TempDir("", "retrodep.")
	if err != nil {
		return nil, errors.Wrapf(err, "FileHashesFromRef(%s)", ref)
	}
	defer os.RemoveAll(dir)

	args := []string{"archive", "-r", ref, "--type", "files"}
	if subPath != "" {
		args = append(args, "--prefix", subPath)
	}
	args = append(args, dir)
	stdout, stderr, err := h.run(args...)
	if err != nil {
		h.showOutput(stdout, stderr)
		return nil, err
	}
	return NewFileHashes(&sha256Hasher{}, filepath.Join(dir, subPath), nil)
}
