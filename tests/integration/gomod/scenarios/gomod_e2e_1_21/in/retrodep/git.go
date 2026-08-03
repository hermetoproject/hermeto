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

// This file contains methods specific to working with git.

import (
	"bufio"
	"bytes"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"time"

	"github.com/pkg/errors"
)

type gitWorkingTree struct {
	anyWorkingTree
}

// Revisions returns all revisions in the git repository, using 'git
// rev-list --all'.
func (g *gitWorkingTree) Revisions() ([]string, error) {
	stdout, stderr, err := g.run("rev-list", "--all")
	if err != nil {
		g.showOutput(stdout, stderr)
		return nil, err
	}
	revisions := make([]string, 0)
	output := bufio.NewScanner(stdout)
	for output.Scan() {
		revisions = append(revisions, strings.TrimSpace(output.Text()))
	}
	return revisions, nil
}

// RevisionFromTag returns the commit hash for the given tag, using
// 'git rev-parse ...'
func (g *gitWorkingTree) RevisionFromTag(tag string) (string, error) {
	stdout, stderr, err := g.run("rev-parse", tag)
	if err != nil {
		g.showOutput(stdout, stderr)
		return "", err
	}
	rev := strings.TrimSpace(stdout.String())
	return rev, nil
}

// RevSync updates the working tree to reflect the revision rev, using
// 'git checkout ...'. The working tree must not have been locally
// modified.
func (g *gitWorkingTree) RevSync(rev string) error {
	stdout, stderr, err := g.run("checkout", rev)
	if err != nil {
		g.showOutput(stdout, stderr)
	}
	return err
}

// TimeFromRevision returns the commit timestamp for the revision
// rev, using 'git show -s --pretty=format:%cI ...'.
func (g *gitWorkingTree) TimeFromRevision(rev string) (time.Time, error) {
	run := g.run
	var t time.Time
	stdout, stderr, err := run("show", "-s", "--pretty=format:%cI", rev)
	if err != nil {
		g.showOutput(stdout, stderr)
		return t, err
	}

	t, err = time.Parse(time.RFC3339, strings.TrimSpace(stdout.String()))
	return t, err
}

// ReachableTag returns the most recent reachable semver tag, using
// 'git describe --tags --match=...', with match globs for tags that
// are likely to be semvers. It returns ErrorVersionNotFound if no
// suitable tag is found.
func (g *gitWorkingTree) ReachableTag(rev string) (string, error) {
	run := g.run
	var tag string
	for _, match := range []string{"v[0-9]*", "[0-9]*"} {
		stdout, stderr, err := run("describe", "--tags", "--match="+match, rev)
		output := strings.TrimSpace(stdout.String() + stderr.String())
		if err == nil {
			tag = output
			break
		}

		// Catch failures due to not finding an appropriate tag
		output = strings.ToLower(output)
		switch {
		// fatal: no tag exactly matches ...
		// fatal: no tags can describe ...
		// fatal: no names found, cannot describe anything.
		// fatal: no annotated tags can describe ...
		case strings.HasPrefix(output, "fatal: no tag"),
			strings.HasPrefix(output, "fatal: no names"),
			strings.HasPrefix(output, "fatal: no annotated tag"):
			err = ErrorVersionNotFound
		default:
			g.showOutput(stdout, stderr)
		}
		return "", err
	}

	if tag == "" {
		return "", ErrorVersionNotFound
	}

	log.Debugf("%s is described as %s", rev, tag)
	fields := strings.Split(tag, "-")
	if len(fields) < 3 {
		// This matches a tag exactly (it must not be a semver tag)
		return tag, nil
	}
	tag = strings.Join(fields[:len(fields)-2], "-")
	return tag, nil
}

// FileHashesFromRef parses the output of 'git ls-tree -r' to
// return the file hashes for the given tag or revision ref.
func (g *gitWorkingTree) FileHashesFromRef(ref, subPath string) (FileHashes, error) {
	args := []string{"ls-tree", "-r", ref}
	if subPath != "" {
		args = append(args, subPath)
	}
	stdout, stderr, err := g.run(args...)
	if err != nil {
		output := strings.ToLower(stdout.String() + stderr.String())
		switch {
		case strings.HasPrefix(output, "fatal: not a valid object name "):
			// This is a branch name, not a tag name
			return nil, ErrorInvalidRef
		case strings.HasPrefix(output, "fatal: not a tree object"):
			// This ref is not present in the repo
			return nil, ErrorInvalidRef
		}

		g.showOutput(stdout, stderr)
		return nil, err
	}
	fh := make(FileHashes)
	scanner := bufio.NewScanner(stdout)
	for scanner.Scan() {
		line := scanner.Text()
		// <mode> SP <type> SP <object> TAB <file>
		ts := strings.SplitN(line, "\t", 2)
		if len(ts) != 2 {
			return nil, fmt.Errorf("expected TAB: %s", line)
		}
		var filename string
		if subPath == "" {
			filename = ts[1]
		} else {
			filename, err = filepath.Rel(subPath, ts[1])
			if err != nil {
				return nil, errors.Wrapf(err, "Rel(%q, %q)",
					subPath, ts[1])
			}
		}
		fields := strings.Fields(ts[0])
		if len(fields) != 3 {
			return nil, fmt.Errorf("expected 3 fields: %s", ts[0])
		}

		fh[filename] = FileHash(fields[2])
	}

	return fh, nil
}

type gitHasher struct{}

// Hash implements the Hasher interface for git.
func (g *gitHasher) Hash(relativePath, absPath string) (FileHash, error) {
	args := []string{"hash-object", "--path", relativePath, absPath}
	cmd := exec.Command(vcsGit, args...)
	var buf bytes.Buffer
	cmd.Stdout = &buf
	cmd.Stderr = &buf
	err := cmd.Run()
	if err != nil {
		os.Stderr.Write(buf.Bytes())
		return FileHash(""), err
	}
	return FileHash(strings.TrimSpace(buf.String())), nil
}
