GO ?= go
VERSION ?= dev
LDFLAGS = -s -w -X main.version=$(VERSION)

.PHONY: build test vet fmt-check dist
build:
	CGO_ENABLED=0 $(GO) build -trimpath -buildvcs=false -ldflags '$(LDFLAGS)' -o bin/hmctl ./cmd/hmctl

test:
	$(GO) test -race ./...

vet:
	$(GO) vet ./...

fmt-check:
	@test -z "$$(gofmt -l cmd/hmctl internal)" || { echo 'Run go fmt ./...' >&2; exit 1; }

dist:
	@mkdir -p dist
	@set -eu; for target in darwin/amd64 darwin/arm64 linux/amd64 linux/arm64; do \
	  target_os=$${target%/*}; target_arch=$${target#*/}; \
	  CGO_ENABLED=0 GOOS=$$target_os GOARCH=$$target_arch $(GO) build -trimpath -buildvcs=false -ldflags '$(LDFLAGS)' -o dist/hmctl_$${target_os}_$${target_arch} ./cmd/hmctl; \
	done
	@cd dist && if command -v sha256sum >/dev/null; then sha256sum hmctl_* > SHA256SUMS; else shasum -a 256 hmctl_* > SHA256SUMS; fi
