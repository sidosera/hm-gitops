//go:build darwin || linux

package hmctl

import (
	"os"
	"syscall"
)

// Go exposes file ownership through FileInfo.Sys; no portable ownership predicate exists.
func ownedByUser(info os.FileInfo) bool {
	stat, ok := info.Sys().(*syscall.Stat_t)
	return ok && stat.Uid == uint32(os.Getuid())
}
