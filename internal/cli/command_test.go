package cli

import (
	"bytes"
	"context"
	"testing"

	"github.com/spf13/cobra"
)

func TestDispatchForwardsServiceArgumentsWithoutWorkloadKnowledge(t *testing.T) {
	for _, separator := range []bool{false, true} {
		var output bytes.Buffer
		ran := false
		key := struct{}{}
		ctx := context.WithValue(context.Background(), key, "context")
		factory := func(o *Options) *cobra.Command {
			var value string
			cmd := &cobra.Command{Use: "unrelated-service", RunE: func(cmd *cobra.Command, args []string) error {
				ran = true
				if value != "literal $(do-not-execute)" || len(args) != 0 || o.Cluster != "another-cluster" || !o.DryRun || cmd.Context().Value(key) != "context" {
					t.Fatal("dispatch changed service arguments, common options or context")
				}
				return nil
			}}
			o.BindFlags(cmd)
			cmd.Flags().StringVar(&value, "service-owned-option", "", "")
			return cmd
		}
		root := NewCommand("test", &output, &output, map[string]Factory{"unrelated-service": factory}, nil)
		args := []string{"--cluster", "another-cluster", "--dry-run", "service", "unrelated-service"}
		if separator {
			args = append(args, "--")
		}
		args = append(args, "--service-owned-option", "literal $(do-not-execute)")
		root.SetArgs(args)
		if err := root.ExecuteContext(ctx); err != nil {
			t.Fatal(err)
		}
		if !ran {
			t.Fatal("service not dispatched")
		}
	}
}
