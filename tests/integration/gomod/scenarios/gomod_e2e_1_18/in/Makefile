BIN_DIR = bin
BIN_NAME = retrodep
BIN = $(BIN_DIR)/$(BIN_NAME)
PREFIX = /usr/local/bin
TOOLS = golang.org/x/tools/cmd/goimports golang.org/x/lint/golint github.com/mattn/goveralls
COVERPROFILE = profile.cov

GOBIN = $$GOPATH/bin
GOVERALLS = $(GOBIN)/goveralls
GOIMPORTS = $(GOBIN)/goimports
GOFMT = gofmt
GOLINT = $(GOBIN)/golint

DEFAULT_BRANCH = master
FILES_TO_CHECK = $(shell git diff --no-renames --name-status $(DEFAULT_BRANCH) -- | grep '\.go$$' | grep -v D | cut -f 2)

.PHONY: all clean deps test build fmt lint install check coveralls

all: build

clean:
	@echo '\033[0;31mRemoving generated binaries\033[0m'; \
	rm -rf $(BIN_DIR)

build:
	@echo '\033[0;32mBuilding\033[0m'; \
	go build -o $(BIN) ./main.go

install:
	@echo 'Installing retrodep to \033[0;32m$(PREFIX)/$(BIN_NAME)\033[0m'; \
	install $(BIN) $(PREFIX)/$(BIN_NAME)

tools:
	@echo 'Installing \033[0;32m$(TOOLS)\033[0m'; \
	for tool in $(TOOLS); do \
		go get -u $$tool; \
	done

test:
	@echo 'Running \033[0;32mtests\033[0m'; \
	go test . -v; \
	go test ./retrodep/glide -v; \
	go test ./retrodep -v -covermode=count -coverprofile=$(COVERPROFILE)

fmt:
	@if test -n "$(FILES_TO_CHECK)"; then \
		echo 'Running \033[0;32mgofmt\033[0m'; \
		out=$$($(GOFMT) -l $(FILES_TO_CHECK)); \
		echo $$out; \
		test -z "$$out"; \
	fi

lint:
	@if test -n "$(FILES_TO_CHECK)"; then \
		echo 'Running \033[0;32mgolint\033[0m'; \
		out=$$($(GOLINT) $(FILES_TO_CHECK)); \
		echo $$out; \
		test -z "$$out"; \
	fi

imports:
	@if test -n "$(FILES_TO_CHECK)"; then \
		echo 'Running \033[0;32mgoimports\033[0m'; \
		out=$$($(GOIMPORTS) -l $(FILES_TO_CHECK)); \
		echo $$out; \
		test -z "$$out"; \
	fi

check: fmt imports lint

coveralls:
	@echo '\033[0;32mPublishing coverage\033[0m'; \
	$(GOVERALLS) -coverprofile=$(COVERPROFILE) -service=travis-ci
