package glide

import (
	"os"
	"path/filepath"

	"gopkg.in/yaml.v2"
)

type glideLock struct {
	Imports []Import `json:"imports"`
}

type glideConf struct {
	Package string `json:"package"`
	Import  []struct {
		Package string
		Repo    string `json:"omitempty"`
	}
}

// Import represents an imported package.
type Import struct {
	Name    string `json:"name"`
	Version string `json:"version"`
	Repo    string `json:"repo"`
}

// Glide represents the glide configuration.
type Glide struct {
	Package string
	Imports []Import
}

// LoadGlide tries to load glide.lock and glide.yaml and extract
// import information.  In case no glide.lock is present, it will use
// the import information from glide.yaml.
func LoadGlide(projectRoot string) (*Glide, error) {
	lockImports := []Import{}
	lockFile, err := os.Open(filepath.Join(projectRoot, "glide.lock"))
	if err == nil {
		defer lockFile.Close()
		lock := glideLock{}
		if err != nil {
			return nil, err
		}
		err = yaml.NewDecoder(lockFile).Decode(&lock)
		if err != nil {
			return nil, err
		}
		lockImports = lock.Imports
	} else if !os.IsNotExist(err) {
		return nil, err
	}

	confFile, err := os.Open(filepath.Join(projectRoot, "glide.yaml"))
	if err != nil {
		return nil, err
	}
	defer confFile.Close()
	conf := glideConf{}
	err = yaml.NewDecoder(confFile).Decode(&conf)
	if err != nil {
		return nil, err
	}

	if len(lockImports) == 0 {
		for _, imp := range conf.Import {
			lockImports = append(lockImports, Import{Name: imp.Package, Repo: imp.Repo})
		}
	}

	return &Glide{Imports: lockImports, Package: conf.Package}, nil
}
