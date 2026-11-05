package main

import (
	"context"
	"errors"
	"fmt"
	"os"
	"os/signal"
	"strings"
	"syscall"

	"github.com/sidosera/hm-gitops/internal/hmctl"
)

var version = "dev"

func main() {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	cmd := hmctl.NewCommand(version, os.Stdout, os.Stderr)
	if err := cmd.ExecuteContext(ctx); err != nil {
		fmt.Fprintln(os.Stderr, "hmctl:", err)
		code := 1
		var usage *hmctl.UsageError
		if errors.As(err, &usage) || strings.HasPrefix(err.Error(), "unknown command ") {
			code = 2
		}
		if ctx.Err() != nil {
			code = 130
		}
		os.Exit(code)
	}
}
