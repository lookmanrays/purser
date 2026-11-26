// Package schema validates Purser's wire and record values against embedded JSON Schemas.
package schema

import (
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"slices"
	"strings"

	"github.com/santhosh-tekuri/jsonschema/v6"
	"github.com/santhosh-tekuri/jsonschema/v6/kind"

	"github.com/lookmanrays/purser/contracts/records"
	"github.com/lookmanrays/purser/internal/kernel/codec"
)

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
func (e *ValidationError) Error() string {
	loc := e.Pointer
	if loc == "" {
		loc = "<root>"
	}
	return fmt.Sprintf("schema %s at %s: %s", e.Schema, loc, e.Message)
}

var schemas = compileAll()

func compileAll() map[string]*jsonschema.Schema {
	c := jsonschema.NewCompiler()
	c.DefaultDraft(jsonschema.Draft2020)
	c.AssertFormat()
	c.RegisterFormat(&jsonschema.Format{Name: "purser-time", Validate: validateTime})
	c.RegisterFormat(&jsonschema.Format{Name: "purser-branch", Validate: validateBranch})
	c.RegisterFormat(&jsonschema.Format{Name: "purser-git-path", Validate: validateGitPath})

	entries, err := fs.ReadDir(records.FS, ".")
	if err != nil {
		panic(err)
	}
	var names []string
	for _, e := range entries {
		if e.IsDir() || !strings.HasSuffix(e.Name(), ".schema.json") {
			continue
		}
		raw, err := fs.ReadFile(records.FS, e.Name())
		if err != nil {
			panic(err)
		}
		var doc any
		if err := json.Unmarshal(raw, &doc); err != nil {
			panic(fmt.Errorf("%s: %w", e.Name(), err))
		}
		if err := c.AddResource("purser://records/"+e.Name(), doc); err != nil {
			panic(fmt.Errorf("%s: %w", e.Name(), err))
		}
		if e.Name() != "defs.schema.json" {
			names = append(names, strings.TrimSuffix(e.Name(), ".schema.json"))
		}
	}
	out := make(map[string]*jsonschema.Schema, len(names))
	for _, name := range names {
		sch, err := c.Compile("purser://records/" + name + ".schema.json")
		if err != nil {
			panic(fmt.Errorf("compile %s: %w", name, err))
		}
		out[name] = sch
	}
	return out
}

// Validate checks value against the named schema.
func Validate(schemaName string, value any) error {
	sch, ok := schemas[schemaName]
	if !ok {
		return fmt.Errorf("%w: %q", ErrUnknownSchema, schemaName)
	}
	err := sch.Validate(value)
	if err == nil {
		return nil
	}
	verr, ok := errors.AsType[*jsonschema.ValidationError](err)
	if !ok {
		return &ValidationError{Schema: schemaName, Message: err.Error()}
	}
	tokens, msg := leafViolation(verr)
	return &ValidationError{
		Schema:  schemaName,
		Pointer: formatPointer(tokens),
		Message: msg,
	}
}

func leafViolation(verr *jsonschema.ValidationError) ([]string, string) {
	if len(verr.Causes) > 0 {
		for _, cause := range verr.Causes {
			switch cause.ErrorKind.(type) {
			case *kind.OneOf, *kind.AnyOf:
				continue
			}
			return leafViolation(cause)
		}
		return leafViolation(verr.Causes[0])
	}
	loc := slices.Clone(verr.InstanceLocation)
	switch k := verr.ErrorKind.(type) {
	case *kind.Required:
		loc = append(loc, k.Missing[0])
	case *kind.DependentRequired:
		loc = append(loc, k.Missing[0])
	case *kind.Dependency:
		loc = append(loc, k.Missing[0])
	case *kind.AdditionalProperties:
		loc = append(loc, k.Properties[0])
	case *kind.PropertyNames:
		loc = append(loc, k.Property)
	}
	return loc, verr.Error()
}

func formatPointer(tokens []string) string {
	if len(tokens) == 0 {
		return ""
	}
	var b strings.Builder
	for _, tok := range tokens {
		b.WriteByte('/')
		b.WriteString(strings.ReplaceAll(strings.ReplaceAll(tok, "~", "~0"), "/", "~1"))
	}
	return b.String()
}

func validateTime(v any) error {
	s, ok := v.(string)
	if !ok {
		return nil
	}
	_, err := codec.ParseTime(s)
	return err
}

func validateBranch(v any) error {
	s, ok := v.(string)
	if !ok {
		return nil
	}
	if !strings.HasPrefix(s, "refs/heads/") || strings.HasSuffix(s, ".") ||
		strings.Contains(s, "..") || strings.Contains(s, "@{") {
		return errors.New("invalid branch ref")
	}
	for _, part := range strings.Split(s, "/") {
		if part == "" || strings.HasPrefix(part, ".") || strings.HasSuffix(part, ".lock") {
			return errors.New("invalid branch segment")
		}
	}
	for i := 0; i < len(s); i++ {
		c := s[i]
		if c <= 32 || c == 127 || strings.ContainsRune("~^:?*[\\", rune(c)) {
			return errors.New("invalid branch character")
		}
	}
	return nil
}

func validateGitPath(v any) error {
	s, ok := v.(string)
	if !ok {
		return nil
	}
	if strings.IndexByte(s, 0) >= 0 {
		return errors.New("NUL byte in path")
	}
	for _, part := range strings.Split(s, "/") {
		if part == "" || part == "." || part == ".." {
			return errors.New("invalid path segment")
		}
	}
	return nil
}
