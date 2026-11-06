package hmctl

import (
	"errors"
	"io"
	"os"
	"strings"

	"github.com/google/uuid"
	"github.com/sidosera/hm-gitops/internal/cli"
	"github.com/spf13/cobra"
)

// readClientID accepts a supplied credential from a private file, so it never
// appears in shell history or process arguments.
func readClientID(path string) (string, error) {
	info, err := os.Lstat(path)
	if err != nil || !info.Mode().IsRegular() {
		return "", errors.New("client UUID must be supplied in a private regular file")
	}
	f, err := os.Open(path)
	if err != nil {
		return "", errors.New("cannot read client UUID file")
	}
	defer f.Close()
	actual, err := f.Stat()
	if err != nil || !os.SameFile(info, actual) || actual.Size() > 128 || actual.Mode().Perm()&0077 != 0 || !ownedByUser(actual) {
		return "", errors.New("client UUID file must be owned with permissions 0600")
	}
	data, err := io.ReadAll(io.LimitReader(f, 128))
	if err != nil {
		return "", errors.New("cannot read client UUID file")
	}
	defer clear(data)
	id := strings.TrimSpace(string(data))
	parsed, err := uuid.Parse(id)
	if err != nil || parsed.String() != id {
		return "", errors.New("client UUID file must contain one canonical UUID")
	}
	return id, nil
}

// ProbeCommand configures observers using a supplied ordinary gateway account;
// it never generates identities, changes labels, or grants gateway access.
func ProbeCommand(shared *cli.Options) *cobra.Command {
	o := (*options)(shared)
	var sync, local bool
	var idFile string
	cmd := &cobra.Command{Use: "vpn-probe", Short: "Configure probe clients using an existing account", Args: noArgs}
	shared.BindFlags(cmd)
	cmd.Flags().BoolVar(&sync, "sync-credentials", false, "Update probe configuration using an already authorized client")
	cmd.Flags().StringVar(&idFile, "client-id-file", "", "Private file containing the existing client UUID")
	cmd.Flags().BoolVar(&local, "local-only", false, "Write encrypted configuration without a PR")
	cmd.RunE = func(cmd *cobra.Command, _ []string) error {
		if !sync || idFile == "" {
			return usage("--sync-credentials and --client-id-file are required")
		}
		if local && o.DryRun {
			return usage("--local-only and --dry-run cannot combine")
		}
		id, err := readClientID(idFile)
		if err != nil {
			return err
		}
		r, close, err := o.open(cmd)
		if err != nil {
			return err
		}
		defer close()
		if !o.DryRun {
			unlock, err := r.lock()
			if err != nil {
				return err
			}
			defer unlock()
			if !local {
				if err := r.preparePR(); err != nil {
					return err
				}
			}
		}
		doc, err := r.loadSecret()
		if err != nil {
			return err
		}
		defer doc.close()
		changed, err := syncProbeCredentials(doc, id)
		if err != nil {
			return err
		}
		if !changed {
			return o.emit(Result{Status: "unchanged; existing probe configuration preserved"})
		}
		if o.DryRun {
			return o.emit(Result{Status: "dry-run: probe configuration validated; accounts unchanged"})
		}
		encrypted, err := doc.encode()
		if err != nil {
			return err
		}
		if err = r.atomicWrite(secretPath, encrypted); err != nil {
			return err
		}
		result := Result{Status: "encrypted probe configuration updated; accounts unchanged", Files: []string{secretPath}}
		if !local {
			result.PR, err = r.openPR(result.Files, "Update probe configuration", accountPRBody)
			if err != nil {
				return err
			}
		}
		return o.emit(result)
	}
	return cmd
}
