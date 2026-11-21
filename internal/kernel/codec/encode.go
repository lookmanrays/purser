package codec

import (
	"encoding/json/jsontext"
	"maps"
	"slices"
	"strconv"
)

const (
	maxInteger = 1<<53 - 1
	// Match the decoder's maximum container depth and bound cyclic Go values.
	maxNesting = 10000
)

// Encode returns the canonical JSON bytes of v.
func Encode(v any) ([]byte, error) { return appendValue(nil, v, 0) }

func appendValue(dst []byte, value any, depth int) ([]byte, error) {
	var err error
	switch v := value.(type) {
	case nil:
		return append(dst, "null"...), nil
	case bool:
		return strconv.AppendBool(dst, v), nil
	case string:
		dst, err = jsontext.AppendQuote(dst, v)
		if err != nil {
			return nil, &Error{Code: "invalid_utf8"}
		}
		return dst, nil
	case int:
		return appendValue(dst, int64(v), depth)
	case int64:
		if v < -maxInteger || v > maxInteger {
			return nil, &Error{Code: "integer_range"}
		}
		return strconv.AppendInt(dst, v, 10), nil
	case []any:
		if depth == maxNesting {
			return nil, &Error{Code: "invalid_value", Detail: "excessive nesting"}
		}
		dst = append(dst, '[')
		for i, item := range v {
			if i > 0 {
				dst = append(dst, ',')
			}
			dst, err = appendValue(dst, item, depth+1)
			if err != nil {
				return nil, err
			}
		}
		return append(dst, ']'), nil
	case map[string]any:
		if depth == maxNesting {
			return nil, &Error{Code: "invalid_value", Detail: "excessive nesting"}
		}
		dst = append(dst, '{')
		for i, key := range slices.Sorted(maps.Keys(v)) {
			if i > 0 {
				dst = append(dst, ',')
			}
			dst, err = appendValue(dst, key, depth)
			if err != nil {
				return nil, err
			}
			dst, err = appendValue(append(dst, ':'), v[key], depth+1)
			if err != nil {
				return nil, err
			}
		}
		return append(dst, '}'), nil
	default:
		return nil, &Error{Code: "invalid_value"}
	}
}
