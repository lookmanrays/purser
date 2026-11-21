// Command purser is the CLI entry point for Purser.
package main

import (
	"fmt"
	"runtime/debug"
)

func main() {
	info, _ := debug.ReadBuildInfo()
	fmt.Println(info.Main.Version)
}
