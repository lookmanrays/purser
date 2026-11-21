package codec

import "errors"

// Event is one stored kernel event.
type Event struct {
	V            int64
	EventVersion int64
	Revision     int64
	BatchStart   int64
	BatchSize    int64
	ActorID      string
	ProposalID   string
	At           string
	Type         string
	Data         map[string]any
	PrevHash     string
	Hash         string
}

// EventSpec is the part of an event that operators decide; the store fills in
// the envelope (revision, batch, actor, proposal, time, hashes).
type EventSpec struct {
	Type         string
	EventVersion int64
	Data         map[string]any
}

// ParseEvent strictly decodes one stored event.
func ParseEvent(raw []byte) (Event, error) {
	object, err := DecodeObject(raw)
	if err != nil {
		if codecErr, ok := errors.AsType[*Error](err); ok {
			switch codecErr.Code {
			case "duplicate_key", "invalid_json", "bad_event":
				return Event{}, codecErr
			}
		}
		return Event{}, &Error{Code: "invalid_json", Detail: err.Error()}
	}
	if len(object) != 12 {
		return Event{}, &Error{Code: "bad_event", Detail: "expected twelve fields"}
	}
	event := Event{
		V:            eventField[int64](object, "v", &err),
		EventVersion: eventField[int64](object, "event_version", &err),
		Revision:     eventField[int64](object, "revision", &err),
		BatchStart:   eventField[int64](object, "batch_start", &err),
		BatchSize:    eventField[int64](object, "batch_size", &err),
		ActorID:      eventField[string](object, "actor_id", &err),
		ProposalID:   eventField[string](object, "proposal_id", &err),
		At:           eventField[string](object, "at", &err),
		Type:         eventField[string](object, "type", &err),
		Data:         eventField[map[string]any](object, "data", &err),
		PrevHash:     eventField[string](object, "prev_hash", &err),
		Hash:         eventField[string](object, "hash", &err),
	}
	if err != nil {
		return Event{}, err
	}
	return event, nil
}

func eventField[T any](object map[string]any, key string, err *error) T {
	value, ok := object[key].(T)
	if !ok && *err == nil {
		*err = &Error{Code: "bad_event", Detail: "missing or ill-typed field " + key}
	}
	return value
}

// Body returns the event as a map without its hash.
func (e Event) Body() map[string]any {
	return map[string]any{
		"v": e.V, "event_version": e.EventVersion, "revision": e.Revision,
		"batch_start": e.BatchStart, "batch_size": e.BatchSize,
		"actor_id": e.ActorID, "proposal_id": e.ProposalID, "at": e.At,
		"type": e.Type, "data": e.Data, "prev_hash": e.PrevHash,
	}
}

// Map returns the event as a map including its hash.
func (e Event) Map() map[string]any {
	object := e.Body()
	object["hash"] = e.Hash
	return object
}

// ComputeHash returns H("event", e.Body()).
func (e Event) ComputeHash() (string, error) { return Hash(TagEvent, e.Body()) }

// VerifyChain checks hashes, revisions, links and batches.
func VerifyChain(events []Event, prevHash string, nextRevision int64) error {
	var batch Event
	var remaining int64
	for _, event := range events {
		hash, err := event.ComputeHash()
		switch {
		case err != nil || hash != event.Hash:
			return &Error{Code: "bad_hash", Revision: event.Revision}
		case event.Revision != nextRevision || event.Revision < 1:
			return &Error{Code: "chain_gap", Revision: event.Revision}
		case event.PrevHash != prevHash:
			return &Error{Code: "bad_link", Revision: event.Revision}
		}

		if remaining == 0 {
			if event.BatchStart != event.Revision || event.BatchSize < 1 ||
				event.BatchSize > maxInteger-event.Revision+1 {
				return &Error{Code: "bad_batch", Revision: event.Revision}
			}
			batch, remaining = event, event.BatchSize
		} else if event.BatchStart != batch.BatchStart || event.BatchSize != batch.BatchSize ||
			event.At != batch.At || event.ActorID != batch.ActorID || event.ProposalID != batch.ProposalID {
			return &Error{Code: "bad_batch", Revision: event.Revision}
		}
		remaining--
		nextRevision, prevHash = event.Revision+1, event.Hash
	}
	if remaining != 0 {
		return &Error{Code: "bad_batch", Revision: nextRevision}
	}
	return nil
}
