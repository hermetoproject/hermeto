// Copyright (C) 2019 Tim Waugh
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
	"io"
	"io/ioutil"
	"os"
	"syscall"
	"testing"

	"github.com/release-engineering/retrodep/v2/retrodep"
)

func captureStdout(t *testing.T) (r io.Reader, reset func()) {
	stdout := int(os.Stdout.Fd())
	orig, err := syscall.Dup(stdout)
	if err != nil {
		t.Fatal(err)
	}
	r, w, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	err = syscall.Dup2(int(w.Fd()), stdout)
	if err != nil {
		t.Fatal(err)
	}

	reset = func() {
		w.Close()
		err := syscall.Dup2(orig, stdout)
		if err != nil {
			t.Fatal(err)
		}
	}

	return
}

func TestDisplayUnknown(t *testing.T) {
	tcs := []struct {
		name        string
		ref         *retrodep.Reference
		templateArg string
		expected    string
	}{
		{
			"nil ref, empty templateArg",
			nil,
			"",
			"*example.com/foo ?\n",
		},
		{
			"with ref, non-zero templateArg",
			&retrodep.Reference{Pkg: "example.com/foo"},
			"filled templateArg",
			"*example.com/foo ?\n",
		},
	}

	for _, tc := range tcs {
		tc := tc

		t.Run(tc.name, func(t *testing.T) {
			*templateArg = tc.templateArg
			r, reset := captureStdout(t)
			displayUnknown(nil, "*", tc.ref, "example.com/foo")
			reset()
			output, err := ioutil.ReadAll(r)
			if err != nil {
				t.Fatal(err)
			}
			if string(output) != tc.expected {
				t.Errorf("expected %v but got %v",
					tc.expected, string(output))
			}
		})
	}
}

func TestGetTemplate(t *testing.T) {
	tcs := []struct {
		name     string
		args     []string
		expected string
	}{
		{
			"default",
			[]string{"retrodep", "."},
			defaultTemplate,
		},
		{
			"go-template",
			[]string{"retrodep", "-o", "go-template={{.Pkg}}", "."},
			"{{.Pkg}}",
		},
		{
			"compatibility",
			[]string{"retrodep", "-template", "@{{.Rev}}", "."},
			"{{.Pkg}}@{{.Rev}}",
		},
	}

	for _, tc := range tcs {
		tc := tc

		// Reset the flags.
		*templateArg = ""
		*outputArg = ""

		t.Run(tc.name, func(t *testing.T) {
			processArgs(tc.args)
			tmpl := getTemplate()
			if tmpl != tc.expected {
				t.Errorf("expected %v but got %v",
					tc.expected, tmpl)
			}
		})
	}
}
