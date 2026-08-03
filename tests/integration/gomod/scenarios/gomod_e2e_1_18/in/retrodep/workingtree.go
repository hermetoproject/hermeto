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
	"bufio"
	"bytes"
	"io"
	"io/ioutil"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"syscall"
	"time"

	"github.com/Masterminds/semver"
	"github.com/pkg/errors"
	"golang.org/x/tools/go/vcs"
)

var execCommand = exec.Command

// Describable is the interface which capture the methods required for
// creating a pseudo-version from a revision.
type Describable interface {
	// ReachableTag returns the most recent reachable tag,
	// preferring semver tags. It returns ErrorVersionNotFound if
	// no suitable tag is found.
	ReachableTag(rev string) (string, error)

	// TimeFromRevision returns the commit timestamp from the
	// revision rev.
	TimeFromRevision(rev string) (time.Time, error)
}

// A WorkingTree is a local checkout of Go source code, and methods to
// interact with the version control system it came from.
type WorkingTree interface {
	io.Closer

	// Should be something that supports creating pseudo-versions.
	Describable

	// Should be something that supports hashing files.
	Hasher

	// TagSync syncs the repo to the named tag.
	TagSync(tag string) error

	// VersionTags returns the semantic version tags.
	VersionTags() ([]string, error)

	// Revisions returns all revisions, newest to oldest.
	Revisions() ([]string, error)

	// FileHashesFromRef returns the file hashes for the tag or
	// revision ref. The returned FileHashes will be relative to
	// the subPath, which is itself relative to the repository
	// root.
	FileHashesFromRef(ref, subPath string) (FileHashes, error)

	// RevSync syncs the repo to the named revision.
	RevSync(rev string) error

	// RevisionFromTag returns the revision ID from the tag.
	RevisionFromTag(tag string) (string, error)

	// StripImportComment removes import comments from package
	// declarations in the same way godep does, writing the result
	// (if changed) to w. It returns a boolean indicating whether
	// an import comment was removed.
	//
	// The file content may be written to w even if no change was made.
	StripImportComment(path string, w io.Writer) (bool, error)

	// Diff writes output to out from 'diff -u' comparing the
	// path within the working tree with the localFile. It returns
	// true if changes were found and false if not.
	Diff(out io.Writer, path, localFile string) (bool, error)
}

// anyWorkingTree uses the golang.org/x/tools/go/vcs Cmd type for
// interacting with the working tree. Other types build on this to
// provide methods not handled by vcs.Cmd.
type anyWorkingTree struct {
	Dir    string
	VCS    *vcs.Cmd
	hasher Hasher
}

// NewWorkingTree creates a local checkout of the version control
// system for a Go project.
func NewWorkingTree(project *vcs.RepoRoot) (WorkingTree, error) {
	dir, err := ioutil.TempDir("", "retrodep.")
	if err != nil {
		return nil, err
	}

	err = project.VCS.Create(dir, project.Repo)
	if err != nil {
		os.RemoveAll(dir)
		return nil, err
	}

	wt := anyWorkingTree{
		Dir: dir,
		VCS: project.VCS,
	}
	switch project.VCS.Cmd {
	case vcsGit:
		wt.hasher = &gitHasher{}
		return &gitWorkingTree{anyWorkingTree: wt}, nil
	case vcsHg:
		wt.hasher = &sha256Hasher{}
		return &hgWorkingTree{anyWorkingTree: wt}, nil
	}

	wt.Close()
	return nil, ErrorUnknownVCS
}

// Close removes the local checkout.
func (wt *anyWorkingTree) Close() error {
	return os.RemoveAll(wt.Dir)
}

func (wt *anyWorkingTree) TagSync(tag string) error {
	return wt.VCS.TagSync(wt.Dir, tag)
}

// VersionTags returns the tags that are parseable as semantic tags,
// e.g. v1.1.0.
func (wt *anyWorkingTree) VersionTags() ([]string, error) {
	tags, err := wt.VCS.Tags(wt.Dir)
	if err != nil {
		return nil, err
	}
	versions := make(semver.Collection, 0)
	versionTags := make(map[*semver.Version]string)
	for _, tag := range tags {
		v, err := semver.NewVersion(tag)
		if err != nil {
			continue
		}
		versions = append(versions, v)
		versionTags[v] = tag
	}
	sort.Sort(sort.Reverse(versions))
	strTags := make([]string, len(versions))
	for i, v := range versions {
		strTags[i] = versionTags[v]
	}
	return strTags, nil
}

// run runs the VCS command with the provided args
// and returns stdout and stderr (as bytes.Buffer).
func (wt *anyWorkingTree) run(args ...string) (*bytes.Buffer, *bytes.Buffer, error) {
	p := execCommand(wt.VCS.Cmd, args...)
	var stdout, stderr bytes.Buffer
	p.Stdout = &stdout
	p.Stderr = &stderr
	p.Dir = wt.Dir
	err := p.Run()
	return &stdout, &stderr, err
}

// showOutput writes stdout to os.Stdout and stderr to os.Stderr.
func (wt *anyWorkingTree) showOutput(stdout, stderr *bytes.Buffer) {
	os.Stdout.Write(stdout.Bytes())
	os.Stderr.Write(stderr.Bytes())
}

// PseudoVersion returns a semantic-like comparable version for a
// revision, based on tags reachable from that revision.
func PseudoVersion(d Describable, rev string) (string, error) {
	suffix := "-0." // This commit is *before* some other tag
	var version string
	reachable, err := d.ReachableTag(rev)
	if err == ErrorVersionNotFound {
		version = "v0.0.0"
	} else if err != nil {
		return "", err
	} else {
		ver, err := semver.NewVersion(reachable)
		if err != nil {
			// Not a semantic version. Use a timestamped suffix
			// to indicate this commit is *after* the tag
			version = reachable
			suffix = "-1."
		} else {
			if ver.Prerelease() == "" {
				*ver = ver.IncPatch()
			} else {
				suffix = ".0."
			}

			version = "v" + ver.String()
		}
	}

	t, err := d.TimeFromRevision(rev)
	if err != nil {
		return "", err
	}

	timestamp := t.Format("20060102150405")
	pseudo := version + suffix + timestamp + "-" + rev[:12]
	return pseudo, nil
}

const quotedRE = `(?:"[^"]+"|` + "`[^`]+`)"
const importRE = `\s*import\s+` + quotedRE + `\s*`

var importCommentRE = regexp.MustCompile(
	`^(package\s+\w+)\s+(?://` + importRE + `$|/\*` + importRE + `\*/)(.*)`,
)

func removeImportComment(line []byte) []byte {
	if matches := importCommentRE.FindSubmatch(line); matches != nil {
		return append(
			matches[1],    // package statement
			matches[2]...) // comments after first closing "*/"
	}

	return nil
}

// StripImportComment removes import comments from package
// declarations in the same way godep does, writing the result (if
// changed) to w. It returns a boolean indicating whether an import
// comment was removed.
//
// The file content may be written to w even if no change was made.
func (wt *anyWorkingTree) StripImportComment(path string, w io.Writer) (bool, error) {
	if !strings.HasSuffix(path, ".go") {
		return false, nil
	}
	path = filepath.Join(wt.Dir, path)
	r, err := os.Open(path)
	if err != nil {
		return false, errors.Wrap(err, "StripImportComment")
	}
	defer r.Close()

	b := bufio.NewReader(r)
	changed := false
	eof := false
	for !eof {
		line, err := b.ReadBytes('\n')
		if err != nil {
			if err == io.EOF {
				eof = true
			} else {
				return false, errors.Wrap(err, "StripImportComment")
			}
		}
		if len(line) > 0 {
			nonl := bytes.TrimRight(line, "\n")
			if len(nonl) == len(line) {
				// There was no newline but we'll add one
				changed = true
			}
			repl := removeImportComment(nonl)
			if repl != nil {
				nonl = repl
				changed = true
			}

			if _, err := w.Write(append(nonl, '\n')); err != nil {
				return false, errors.Wrap(err, "StripImportComment")
			}
		}
	}

	return changed, nil
}

// Hash returns the file hash for the filename absPath, hashed as
// though it were in the repository as filename relativePath.
func (wt *anyWorkingTree) Hash(relativePath, absPath string) (FileHash, error) {
	return wt.hasher.Hash(relativePath, absPath)
}

// Diff writes output to stdout from 'diff -u' comparing the
// path within the working tree with the localFile. It returns
// true if changes were found and false if not.
func (wt *anyWorkingTree) Diff(out io.Writer, path, localFile string) (bool, error) {
	if path == "" {
		path = "/dev/null"
	} else if path[0] != '/' {
		path = filepath.Join(wt.Dir, path)
	}

	p := execCommand("diff", "-u", path, localFile)
	p.Stdout = out
	err := p.Run()

	changes := false
	if err != nil {
		// Find the exit code from an exec.ExitError by using
		// the embedded os.ProcessState's Sys method.
		if exitErr, ok := err.(*exec.ExitError); ok {
			if waitStatus, ok := exitErr.Sys().(syscall.WaitStatus); ok {
				// Exit codes for diff are:
				// 0: no differences were found
				// 1: some differences were found
				// >1: trouble
				if waitStatus.Exited() && waitStatus.ExitStatus() == 1 {
					return true, nil
				}
			}
		}
	}

	return changes, err
}
