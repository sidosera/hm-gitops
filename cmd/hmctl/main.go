package main

import (
	"context"
	"errors"
	"fmt"
	"os"
	"os/signal"
	"strings"
	"syscall"

	"github.com/sidosera/hm-gitops/internal/cli"
	"github.com/sidosera/hm-gitops/internal/hmctl"
)

var version = "dev"

func main() {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()
	cmd := cli.NewCommand(version, os.Stdout, os.Stderr, map[string]cli.Factory{"gateway": hmctl.GatewayCommand, "vpn-probe": hmctl.ProbeCommand}, hmctl.ClusterCommand)
	if err := cmd.ExecuteContext(ctx); err != nil {
		fmt.Fprintln(os.Stderr, "hmctl:", err)
		code := 1
		var usage *cli.UsageError
		if errors.As(err, &usage) || strings.HasPrefix(err.Error(), "unknown command ") {
			code = 2
		}
		if ctx.Err() != nil {
			code = 130
		}
		os.Exit(code)
	}
}
