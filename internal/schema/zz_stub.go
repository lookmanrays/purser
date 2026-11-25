// Package schema validates Purser's wire and record values.
package schema

import "errors"

var unimplemented = func() { panic("unimplemented: P0-03") }

// ErrUnknownSchema is wrapped by the error Validate returns for a schema
// name that is not embedded.
var ErrUnknownSchema = errors.New("schema: unknown schema name")

// ValidationError reports one violation of a schema.
type ValidationError struct {
	Schema  string // the name passed to Validate
	Pointer string // RFC 6901 pointer into the value
	Message string // human-readable reason
}

// Error returns the validation error's human-readable description.
func (*ValidationError) Error() string {
	unimplemented()
	return ""
}

// Validate checks value against the named schema. value is a JSON value as
// codec.Decode produces it: map[string]any, []any, string, int64, bool or nil.
// schemaName is the schema's file name in contracts/records without
// ".schema.json", for example "proposal", "fields.Commitment" or "graph.node".
// It returns nil, a *ValidationError, or an error wrapping ErrUnknownSchema.
func Validate(_ string, _ any) error {
	unimplemented()
	return nil
}
