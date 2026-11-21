package codec

import (
	"bytes"
	"encoding/json/jsontext"
	"encoding/json/v2"
	"errors"
	"strconv"
	"unicode/utf8"
)

// Decode strictly decodes data into a Value.
func Decode(data []byte) (any, error) {
	if !utf8.Valid(data) {
		return nil, &Error{Code: "invalid_utf8"}
	}
	decoder := jsontext.NewDecoder(bytes.NewReader(data))
	var value any
	if err := json.UnmarshalDecode(decoder, &value, withIntegers); err != nil {
		if codecErr, ok := errors.AsType[*Error](err); ok {
			return nil, codecErr
		}
		if errors.Is(err, jsontext.ErrDuplicateName) {
			return nil, &Error{Code: "duplicate_key", Detail: err.Error()}
		}

		// The raw bytes are valid UTF-8. If otherwise valid JSON is rejected
		// by the Unicode check, a string contains an unpaired surrogate.
		relaxed := jsontext.NewDecoder(bytes.NewReader(data), jsontext.AllowInvalidUTF8(true))
		if _, relaxedErr := relaxed.ReadValue(); relaxedErr == nil {
			return nil, &Error{Code: "lone_surrogate", Detail: err.Error()}
		}
		return nil, &Error{Code: "invalid_json", Detail: err.Error()}
	}
	if len(bytes.Trim(data[decoder.InputOffset():], " \t\r\n")) != 0 {
		return nil, &Error{Code: "trailing_data"}
	}
	return value, nil
}

// Decode each JSON number directly into int64, including inside containers.
var withIntegers = json.WithUnmarshalers(json.UnmarshalFromFunc(func(d *jsontext.Decoder, value *any) error {
	if d.PeekKind() != jsontext.KindNumber {
		return errors.ErrUnsupported
	}
	token, err := d.ReadToken()
	if err != nil {
		return err
	}
	n, err := token.Int()
	switch {
	case errors.Is(err, strconv.ErrSyntax):
		return &Error{Code: "non_integer"}
	case err != nil || n < -maxInteger || n > maxInteger:
		return &Error{Code: "integer_range"}
	}
	*value = n
	return nil
}))

// DecodeObject strictly decodes data that must be a JSON object.
func DecodeObject(data []byte) (map[string]any, error) {
	value, err := Decode(data)
	object, ok := value.(map[string]any)
	if err == nil && !ok {
		err = &Error{Code: "bad_event", Detail: "expected an object"}
	}
	return object, err
}
