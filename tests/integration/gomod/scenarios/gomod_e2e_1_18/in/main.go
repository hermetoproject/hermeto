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

package main

import (
	"bufio"
	"flag"
	"fmt"
	"io/ioutil"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"text/template"

	"github.com/op/go-logging"
	"github.com/release-engineering/retrodep/v2/retrodep"
	"golang.org/x/tools/go/vcs"
)

const defaultTemplate string = `
  {{- if .TopPkg -}}
	{{.TopPkg}}:{{or .TopVer "?"}} {{ end -}}
  {{.Pkg}}:{{or .Ver "?"}}`

var log = logging.MustGetLogger("retrodep")

var helpFlag = flag.Bool("help", false, "print help")
var importPath = flag.String("importpath", "", "top-level import path")
var onlyImportPath = flag.Bool("only-importpath", false, "only show the top-level import path")
var depsFlag = flag.Bool("deps", true, "show vendored dependencies")
var diffArg = flag.String("diff", "", "compare with upstream ref (implies -deps=false)")
var excludeFrom = flag.String("exclude-from", "", "ignore directory entries matching globs in `exclusions`")
var debugFlag = flag.Bool("debug", false, "show debugging output")
var outputArg = flag.String("o", "", "output format, one of: go-template=...")
var templateArg = flag.String("template", "", "go template to use for output with Pkg, Repo, Rev, Tag and Ver (deprecated)")
var exitFirst = flag.Bool("x", false, "exit on the first failure")

var errorShown = false
var usage func(string)

func displayUnknown(tmpl *template.Template, topLevelMarker string, ref *retrodep.Reference, projectRoot string) {
	if ref == nil || *templateArg != "" {
		fmt.Printf("%s%s ?\n", topLevelMarker, projectRoot)
	} else {
		display(tmpl, topLevelMarker, ref)
	}
	if !errorShown {
		errorShown = true
		fmt.Fprintln(os.Stderr, "error: not all versions identified")
		if *exitFirst {
			os.Exit(2)
		}
	}
}

func display(tmpl *template.Template, topLevelMarker string, ref *retrodep.Reference) {
	var builder strings.Builder
	builder.WriteString(topLevelMarker)
	err := tmpl.Execute(&builder, ref)
	if err != nil {
		log.Fatalf("Error generating output. %s", err)
	}
	fmt.Println(builder.String())
}

func getProject(src *retrodep.GoSource, importPath string) *retrodep.RepoPath {
	main, err := src.Project(importPath)
	if err != nil {
		if err == retrodep.ErrorNeedImportPath {
			log.Errorf("%s: %s", src.Path, err)
			fmt.Fprintln(os.Stderr,
				"Provide import path with -importpath")
			os.Exit(3)
		}
		log.Fatalf("%s: %s", src.Path, err)
	}

	return main
}

// newWorkingTree creates a new retrodep.WorkingTree for the path.
func newWorkingTree(path string, project *vcs.RepoRoot) (wt retrodep.WorkingTree, err error) {
	wt, err = retrodep.NewWorkingTree(project)
	if err != nil {
		log.Errorf("%s: %s, retrying", path, err)
		wt, err = retrodep.NewWorkingTree(project)
	}
	return
}

func showTopLevel(tmpl *template.Template, src *retrodep.GoSource) *retrodep.Reference {
	var topLevelMarker string
	if *templateArg != "" {
		topLevelMarker = "*"
	}
	main := getProject(src, *importPath)
	if main.Err != nil {
		log.Errorf("%s: %s", *importPath, main.Err)
		displayUnknown(tmpl, topLevelMarker, nil, main.Root)
		return nil
	}

	wt, err := newWorkingTree(src.Path, &main.RepoRoot)
	if err != nil {
		log.Errorf("%s: %s", src.Path, err)

		// Treat this as VersionNotFound.
		project := &retrodep.Reference{
			Pkg:  main.Root,
			Repo: main.Repo,
		}
		displayUnknown(tmpl, topLevelMarker, project, main.Root)
		return project
	}

	defer wt.Close()
	project, err := src.DescribeProject(main, wt, src.Path, nil)
	switch err {
	case retrodep.ErrorVersionNotFound:
		displayUnknown(tmpl, topLevelMarker, project, main.Root)
	case nil:
		display(tmpl, topLevelMarker, project)
	default:
		log.Fatalf("%s: %s", src.Path, err)
	}

	return project
}

func showVendored(tmpl *template.Template, src *retrodep.GoSource, top *retrodep.Reference) {
	vendored, err := src.VendoredProjects()
	if err != nil {
		log.Fatal(err)
	}

	// Sort the projects for predictable output
	var repos []string
	for repo := range vendored {
		repos = append(repos, repo)
	}
	sort.Strings(repos)

	// Describe each vendored project
	for _, repo := range repos {
		project := vendored[repo]
		if project.Err != nil {
			log.Errorf("%s: %s", repo, project.Err)
			ref := &retrodep.Reference{
				TopPkg: top.Pkg,
				TopVer: top.Ver,
				Pkg:    repo,
			}
			displayUnknown(tmpl, "", ref, repo)
			continue
		}

		wt, err := newWorkingTree(project.Root, &project.RepoRoot)
		if err != nil {
			log.Errorf("%s: %s", project.Root, err)

			// Treat this as VersionNotFound.
			vp := &retrodep.Reference{
				TopPkg: top.Pkg,
				TopVer: top.Ver,
				Pkg:    project.Root,
				Repo:   project.Repo,
			}
			displayUnknown(tmpl, "", vp, project.Root)
			continue
		}

		defer wt.Close()
		vp, err := src.DescribeVendoredProject(project, wt, top)
		switch err {
		case retrodep.ErrorVersionNotFound:
			displayUnknown(tmpl, "", vp, project.Root)
		case nil:
			display(tmpl, "", vp)
		default:
			log.Fatalf("%s: %s", project.Root, err)
		}
	}
}

func readExcludeFile() []string {
	if *excludeFrom == "" {
		return nil
	}

	e, err := os.Open(*excludeFrom)
	if err != nil {
		log.Fatal(err)
	}
	defer e.Close()

	excludes := make([]string, 0)
	scanner := bufio.NewScanner(bufio.NewReader(e))
	for scanner.Scan() {
		excludes = append(excludes, strings.TrimSpace(scanner.Text()))
	}
	return excludes
}

func processArgs(args []string) []*retrodep.GoSource {
	progName := filepath.Base(args[0])

	// Stop the default behaviour of printing errors and exiting.
	// Instead, silence the printing and return them.
	cli := flag.CommandLine
	cli.Init("", flag.ContinueOnError)
	cli.SetOutput(ioutil.Discard)
	cli.Usage = func() {}

	usageMsg := fmt.Sprintf("usage: %s [OPTION]... PATH", progName)
	usage = func(flaw string) {
		log.Fatalf("%s: %s\n%s", progName, flaw, usageMsg)
	}
	err := cli.Parse(args[1:])
	if err == flag.ErrHelp || *helpFlag { // Handle ‘-h’.
		fmt.Printf("%s: help requested\n%s\n", progName, usageMsg)
		cli.SetOutput(os.Stdout)
		flag.PrintDefaults()
		os.Exit(0) // Not an error.
	}
	if err != nil {
		usage(err.Error())
	}

	narg := flag.NArg()
	if narg == 0 {
		usage("missing path")
	}
	if narg != 1 {
		usage(fmt.Sprintf("only one path allowed: %q", flag.Arg(1)))
	}

	level := logging.INFO
	if *debugFlag {
		level = logging.DEBUG
	}
	logging.SetLevel(level, "retrodep")

	excludeGlobs := readExcludeFile()
	path := flag.Arg(0)
	sources, err := retrodep.FindGoSources(path, excludeGlobs)
	if err != nil {
		if err == retrodep.ErrorNoGo {
			fmt.Fprintf(os.Stderr,
				"%s: no Go source code at %s\n",
				progName, path)
			os.Exit(4)
		}

		log.Fatal(err)
	}

	return sources
}

func getTemplate() string {
	var customTemplate string
	switch {
	case *outputArg != "":
		customTemplate = strings.TrimPrefix(*outputArg, "go-template=")
		if customTemplate == *outputArg {
			usage("unknown output format")
		}
	case *templateArg != "":
		customTemplate = "{{.Pkg}}" + *templateArg
		log.Warning("-template is deprecated, use -o go-template= instead")
	default:
		customTemplate = defaultTemplate
	}

	return customTemplate
}

func main() {
	srcs := processArgs(os.Args)

	customTemplate := getTemplate()
	tmpl, err := template.New("output").Parse(customTemplate)
	if err != nil {
		log.Fatal(err)
	}
	changes := false
	for _, src := range srcs {
		if *diffArg != "" {
			main := getProject(src, *importPath)

			wt, err := newWorkingTree(src.Path, &main.RepoRoot)
			if err != nil {
				log.Fatal(err)
			}
			defer wt.Close()

			c, err := src.Diff(main, wt, os.Stdout, src.Path, *diffArg)
			if err != nil {
				log.Fatal(err)
			}

			changes = changes || c
		} else if *onlyImportPath {
			main := getProject(src, *importPath)
			fmt.Println("*" + main.Root)
		} else {
			top := showTopLevel(tmpl, src)
			if *depsFlag {
				showVendored(tmpl, src, top)
			}
		}
	}

	if errorShown {
		os.Exit(2)
	}

	if *diffArg != "" && changes {
		os.Exit(5)
	}
}
