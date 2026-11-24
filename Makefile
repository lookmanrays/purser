FUZZ_PACKAGE ?= ./internal/kernel/codec
FUZZ_TARGET ?= .
FUZZ_TIME ?= 30s

.PHONY: check test fuzz bench briefcheck

check:
	go build ./...
	go vet ./...
	golangci-lint run
	go test -race ./...

test:
	go test ./...

fuzz:
	go test -run '^$$' -fuzz '$(FUZZ_TARGET)' -fuzztime '$(FUZZ_TIME)' $(FUZZ_PACKAGE)

bench:
	go test -run '^$$' -bench . -benchmem -count 5 ./... > .bench.out
	go run ./tools/benchcheck --bench .bench.out --source .

briefcheck:
	go run ./tools/briefcheck $(if $(ID),--id $(ID),)

red:
	go run ./tools/briefcheck red $(if $(ID),--id $(ID),)
