package p091

import "testing"

func TestAC1_First(t *testing.T) {
	if err := StubFunc(); err != nil {
		t.Fatal(err)
	}
}

func TestAC2_Second(t *testing.T) {
	if err := StubFunc(); err != nil {
		t.Fatal(err)
	}
}
