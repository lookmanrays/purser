package store

var unimplemented = func() { panic("unimplemented: P1-02c") }

// TrustedHead identifies an expected revision and hash at the head of the log.
type TrustedHead struct {
	Revision int64
	Hash     string
}

// Verify checks stored bytes, the contiguous chain, batch boundaries and known versions without writing.
func (s *Store) Verify(_ *TrustedHead) error {
	unimplemented()
	return nil
}

// Replay verifies, folds into fresh tables and swaps them in under the write reservation.
func (s *Store) Replay(_ *TrustedHead) (Snapshot, error) {
	unimplemented()
	return Snapshot{}, nil
}
