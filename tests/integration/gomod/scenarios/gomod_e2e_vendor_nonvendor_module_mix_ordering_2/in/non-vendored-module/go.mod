module github.com/hermetoproject/integration-tests/gomod-vendor-non-vendor-mix/non-vendored-module

go 1.23.0

require github.com/hermetoproject/integration-tests/gomod-vendor-non-vendor-mix/vendored-module v0.0.0

require github.com/google/uuid v1.6.0 // indirect

replace github.com/hermetoproject/integration-tests/gomod-vendor-non-vendor-mix/vendored-module => ../vendored-module
