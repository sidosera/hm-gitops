// Package cli provides workload-independent command dispatch and common flags.
package cli

import (
	"fmt"
	"io"
	"sort"
	"strings"
	"time"

	"github.com/spf13/cobra"
)

type UsageError struct{ Message string }

func (e *UsageError) Error() string { return e.Message }

type Options struct {
	Repo, Cluster, KeyFile string
	JSON, DryRun           bool
	Timeout                time.Duration
	Out                    io.Writer
}

type Factory func(*Options) *cobra.Command

// BindFlags shares common options with independently parsed service commands.
func (o *Options) BindFlags(cmd *cobra.Command) {
	f := cmd.PersistentFlags()
	f.StringVar(&o.Repo, "repo", o.Repo, "GitOps checkout (default: current repository)")
	f.StringVar(&o.Cluster, "cluster", o.Cluster, "Cluster configuration name")
	f.StringVar(&o.KeyFile, "age-key-file", o.KeyFile, "Native age identity file (default: SOPS_AGE_KEY_FILE)")
	f.DurationVar(&o.Timeout, "timeout", o.Timeout, "Maximum operation duration")
	f.BoolVar(&o.JSON, "json", o.JSON, "Emit versioned JSON metadata; never credentials")
	f.BoolVar(&o.DryRun, "dry-run", o.DryRun, "Validate without writing or publishing")
}

// NewCommand knows only the registered service and cluster handlers. Service
// arguments are forwarded verbatim to their handler, without running a shell.
func NewCommand(version string, out, stderr io.Writer, services map[string]Factory, cluster Factory) *cobra.Command {
	o := &Options{Cluster: "hm-cluster", Timeout: 2 * time.Minute, Out: out}
	root := &cobra.Command{Use: "hmctl", Short: "Manage HM infrastructure and service configuration through GitOps", Version: version, SilenceUsage: true, SilenceErrors: true, TraverseChildren: true}
	root.SetOut(out)
	root.SetErr(stderr)
	root.SetFlagErrorFunc(func(_ *cobra.Command, err error) error { return &UsageError{err.Error()} })
	o.BindFlags(root)
	root.SetVersionTemplate("hmctl {{.Version}}\n")
	dispatch := &cobra.Command{Use: "service <service> [-- service arguments]", Short: "Forward configuration arguments to a registered service", DisableFlagParsing: true}
	names := make([]string, 0, len(services))
	for name := range services {
		names = append(names, name)
	}
	sort.Strings(names)
	dispatch.ValidArgs = names
	dispatch.Long = "Forward service arguments unchanged. Registered services: " + strings.Join(names, ", ")
	dispatch.RunE = func(cmd *cobra.Command, args []string) error {
		if len(args) == 0 || args[0] == "--help" || args[0] == "-h" {
			return cmd.Help()
		}
		factory, ok := services[args[0]]
		if !ok {
			return &UsageError{"unknown service"}
		}
		handler := factory(o)
		rest := args[1:]
		if len(rest) > 0 && rest[0] == "--" {
			rest = rest[1:]
		}
		handler.SetArgs(rest)
		handler.SetOut(out)
		handler.SetErr(stderr)
		handler.SilenceUsage = true
		handler.SilenceErrors = true
		handler.SetFlagErrorFunc(func(_ *cobra.Command, err error) error { return &UsageError{err.Error()} })
		return handler.ExecuteContext(cmd.Context())
	}
	root.AddCommand(dispatch)
	if cluster != nil {
		root.AddCommand(cluster(o))
	}
	root.AddCommand(&cobra.Command{Use: "version", Short: "Print binary version", Args: cobra.NoArgs, Run: func(cmd *cobra.Command, _ []string) { fmt.Fprintln(cmd.OutOrStdout(), "hmctl", version) }})
	return root
}
