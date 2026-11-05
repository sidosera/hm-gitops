//go:build !darwin && !linux

package hmctl

import "os"

// Private profile exports require ownership checks supported on Linux and macOS.
func ownedByUser(os.FileInfo) bool { return false }
