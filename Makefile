FUZZ_TIME ?= 30s

.PHONY: check test fuzz bench briefcheck

check:
	go build ./...
	go vet ./...
	golangci-lint run
	go test -race -coverprofile=.coverage.out ./...
	go run ./tools/covercheck --coverprofile=.coverage.out

test:
	go test ./...

fuzz:
	go run ./tools/covercheck fuzz-targets -run -fuzztime $(FUZZ_TIME) .

bench:
	go test -run '^$$' -bench . ./...

briefcheck:
	go run ./tools/briefcheck $(if $(ID),--id $(ID),)

red:
	go run ./tools/briefcheck red $(if $(ID),--id $(ID),)
