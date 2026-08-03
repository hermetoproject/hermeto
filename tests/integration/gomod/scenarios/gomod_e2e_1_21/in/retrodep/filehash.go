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
	"crypto/sha256"
	"encoding/hex"
	"io"
	"os"
	"path"
	"path/filepath"
	"strings"

	"github.com/pkg/errors"
)

// FileHash records the hash of a file in the format preferred by the
// version control system that tracks it.
type FileHash string

// Hasher is the interface that wraps the Hash method.
type Hasher interface {
	// Hash returns the file hash for the filename absPath, hashed
	// as though it were in the repository as filename
	// relativePath.
	Hash(relativePath, absPath string) (FileHash, error)
}

type sha256Hasher struct{}

// Hash implements the Hasher interface generically using sha256.
func (h sha256Hasher) Hash(relativePath, absPath string) (FileHash, error) {
	f, err := os.Open(absPath)
	if err != nil {
		return FileHash(""), errors.Wrapf(err, "hashing %s", absPath)
	}
	defer f.Close()

	hash := sha256.New()
	_, err = io.Copy(hash, f)
	if err != nil {
		return FileHash(""), errors.Wrapf(err, "hashing %s", absPath)
	}

	return FileHash(hex.EncodeToString(hash.Sum(nil))), nil
}

// FileHashes is a map of paths, relative to the top-level of the
// version control system, to their hashes.
type FileHashes map[string]FileHash

// NewFileHashes returns a new FileHashes from a filesystem tree at root,
// whose files belong to the version control system named in vcsCmd. Keys in
// the excludes map are filenames to ignore.
func NewFileHashes(h Hasher, root string, excludes map[string]struct{}) (FileHashes, error) {
	hashes := make(FileHashes)
	root = path.Clean(root)

	// Make a local copy of excludes we can safely modify
	excl := make(map[string]struct{})
	if excludes != nil {
		for k, v := range excludes {
			excl[k] = v
		}
	}

	walkfn := func(path string, info os.FileInfo, err error) error {
		if err != nil {
			return err
		}
		if _, skip := excl[path]; skip {
			// This pathname has been ignored, either by caller
			// request or due to .gitattributes
			if info.IsDir() {
				return filepath.SkipDir
			}
			return nil
		}
		if info.IsDir() {
			// Check for .gitattributes in this directory
			// FIXME: gitattributes(5) describes a more complex file
			// format than handled here.  Can git-check-attr(1) help?
			ga, err := os.Open(filepath.Join(path, ".gitattributes"))
			if err != nil {
				if os.IsNotExist(err) {
					err = nil
				}
				return err
			}
			defer ga.Close()

			scanner := bufio.NewScanner(bufio.NewReader(ga))
			for scanner.Scan() {
				fields := strings.Fields(scanner.Text())
				if len(fields) < 2 {
					continue
				}
				for _, field := range fields[1:] {
					if field == "export-subst" {
						// Not expected to have matching hash
						fn := filepath.Join(path, fields[0])
						excl[fn] = struct{}{}
						break
					}
				}
			}

			return nil
		}
		if !info.Mode().IsRegular() {
			return nil
		}
		relativePath, err := filepath.Rel(root, path)
		if err != nil {
			return err
		}

		fileHash, err := h.Hash(relativePath, path)
		if err != nil {
			return err
		}
		hashes[relativePath] = fileHash
		return nil
	}
	err := filepath.Walk(root, walkfn)
	if err != nil {
		return nil, err
	}
	return hashes, nil
}

// IsSubsetOf returns true if these file hashes are a subset of s.
func (h FileHashes) IsSubsetOf(s FileHashes) bool {
	return h.Mismatches(s, true) == nil
}

// Mismatches returns a slice of filenames from h whose hashes
// mismatch those in s. If failFast is true at most one mismatch will
// be returned.
func (h FileHashes) Mismatches(s FileHashes, failFast bool) []string {
	var mismatches []string
	for path, fileHash := range h {
		sh, ok := s[path]
		if !ok {
			// File not present in s
			log.Debugf("%s: not present", path)
			mismatches = append(mismatches, path)
		} else if fileHash != sh {
			// Hash does not match
			log.Debugf("%s: hash mismatch", path)
			mismatches = append(mismatches, path)
		}

		if failFast && mismatches != nil {
			return mismatches
		}
	}

	return mismatches
}
