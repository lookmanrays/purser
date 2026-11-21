package p091

var unimplemented = func() { panic("unimplemented: P0-91") }

// StubFunc is a stub.
func StubFunc() error { unimplemented(); return nil }
