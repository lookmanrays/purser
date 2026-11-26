// Package records embeds the JSON Schema definitions for Purser's wire shapes and records.
package records

import "embed"

// FS embeds all JSON Schema files in contracts/records.
//
//go:embed *.schema.json
var FS embed.FS
