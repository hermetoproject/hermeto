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

// Package retrodep provides a way to represent Go source code in a
// filesystem, and taken from a source code repository. It allows
// mapping vendored packages back to the original versions they came
// from.
//
// A GoSource represents a filesystem tree containing Go source
// code. Create it using NewGoSource or FindGoSources. The Project and
// VendoredProjects methods return information about the top-level
// project and the vendored projects it has.
//
//     src := retrodep.NewGoSource(path, nil)
//     proj, perr := src.Project(importPath)
//     vendored, verr := src.VendoredProjects()
//
// Both of these methods use RepoPath to describe the projects. If a
// glide configuration file is found, Version will be filled in for
// each vendored dependency.
//
// The FindGoSources function looks for Go source code in the provided
// path. If it is not found there, the immediate subdirectories are
// searched. This function allows for repositories which are
// collections of independently-vendored projects.
//
// The NewWorkingTree function makes a temporary local copy of the
// upstream repository.
//
//    wt, err := retrodep.NewWorkingTree(&proj.RepoRoot)
//
// The DescribeProject function takes a RepoPath, a WorkingTree, and
// path within the tree, and returns a Representation, indicating the
// upstream version of the project or vendored project, e.g.
//
//     ref, rerr := retrodep.DescribeProject(proj, wt, src.Path)
//
// It does this by comparing file hashes of the local files with those
// from commits in the upstream repository.
package retrodep
