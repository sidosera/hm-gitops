// Package hmctl contains shared GitOps repository and filesystem helpers.
package hmctl

import (
	"context"
	"encoding/json"
	"fmt"
	"regexp"

	"github.com/sidosera/hm-gitops/internal/cli"
	"github.com/spf13/cobra"
)

var clusterPattern = regexp.MustCompile(`^[a-z0-9]+(?:-[a-z0-9]+)*$`)

// Open validates common options and opens the checkout without loading any
// service configuration or credentials.
func Open(o *cli.Options, cmd *cobra.Command) (*Repository, func(), error) {
	if !clusterPattern.MatchString(o.Cluster) {
		return nil, nil, &cli.UsageError{Message: "invalid --cluster"}
	}
	if o.Timeout <= 0 {
		return nil, nil, &cli.UsageError{Message: "--timeout must be positive"}
	}
	ctx, cancel := context.WithTimeout(cmd.Context(), o.Timeout)
	r, err := OpenRepository(ctx, o.Repo)
	if err != nil {
		cancel()
		return nil, nil, err
	}
	r.KeyFile = o.KeyFile
	return r, func() { r.Close(); cancel() }, nil
}

type Result struct {
	SchemaVersion int      `json:"schema_version"`
	Status        string   `json:"status"`
	Files         []string `json:"files,omitempty"`
	PR            string   `json:"pr_url,omitempty"`
}

func Emit(o *cli.Options, result Result) error {
	result.SchemaVersion = 1
	if o.JSON {
		return json.NewEncoder(o.Out).Encode(result)
	}
	fmt.Fprintln(o.Out, result.Status)
	if result.PR != "" {
		fmt.Fprintln(o.Out, result.PR)
	}
	for _, path := range result.Files {
		fmt.Fprintln(o.Out, path)
	}
	return nil
}
