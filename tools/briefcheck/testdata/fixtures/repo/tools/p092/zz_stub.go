package p092

var unimplemented = func() { panic("unimplemented: P0-92") }

// StubFunc92 is a stub.
func StubFunc92() error { unimplemented(); return nil }
