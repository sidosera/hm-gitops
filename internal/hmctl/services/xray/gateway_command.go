package xray

import (
	"encoding/json"
	"errors"
	"fmt"
	core "github.com/sidosera/hm-gitops/internal/hmctl"
	"strings"

	"github.com/sidosera/hm-gitops/internal/cli"
	"github.com/spf13/cobra"
)

type UsageError = cli.UsageError

func usage(message string) error { return &UsageError{Message: message} }

type options cli.Options

type Result struct {
	SchemaVersion int           `json:"schema_version"`
	Status        string        `json:"status"`
	Files         []string      `json:"files,omitempty"`
	PR            string        `json:"pr_url,omitempty"`
	Clients       []ClientInfo  `json:"clients,omitempty"`
	Checks        []CheckResult `json:"checks,omitempty"`
}
type ClientInfo struct {
	Label    string   `json:"label"`
	Zones    []string `json:"zones"`
	Accounts int      `json:"accounts"`
	Service  bool     `json:"service_account"`
}

func (o *options) emit(result Result) error {
	result.SchemaVersion = 1
	if o.JSON {
		return json.NewEncoder(o.Out).Encode(result)
	}
	fmt.Fprintln(o.Out, result.Status)
	if result.PR != "" {
		fmt.Fprintln(o.Out, result.PR)
	}
	for _, file := range result.Files {
		fmt.Fprintln(o.Out, file)
	}
	for _, client := range result.Clients {
		kind := "client"
		if client.Service {
			kind = "service"
		}
		fmt.Fprintf(o.Out, "%s\t%s\t%s\t%d account(s)\n", client.Label, kind, strings.Join(client.Zones, ","), client.Accounts)
	}
	for _, check := range result.Checks {
		fmt.Fprintf(o.Out, "%s: %t\n", check.Name, check.Passed)
	}
	return nil
}

func (o *options) open(cmd *cobra.Command) (*repository, func(), error) {
	r, close, err := core.Open((*cli.Options)(o), cmd)
	if err != nil {
		return nil, nil, err
	}
	return &repository{r}, close, nil
}
func noArgs(_ *cobra.Command, args []string) error {
	if len(args) != 0 {
		return usage("unexpected positional arguments")
	}
	return nil
}
func newGatewayCommand(o *options) *cobra.Command {
	var add, remove, export, listing, local bool
	var label, endpoint, output, httpsHost, checkFile, expectedExit string
	var zones []string
	cmd := &cobra.Command{Use: "gateway", Short: "Inspect or change a service configuration", Args: noArgs,
		Example: "  hmctl service gateway --add-account --label alice\n  hmctl service gateway --export-account --label alice --zone ru-spb-1 --output-dir /private/alice\n  hmctl service gateway --list-accounts --json"}
	cmd.Flags().BoolVar(&add, "add-account", false, "Generate/reuse a client UUID and open an encrypted account PR")
	cmd.Flags().BoolVar(&remove, "remove-account", false, "Revoke a client's access in selected zones through a PR")
	cmd.Flags().BoolVar(&export, "export-account", false, "Write a client's VLESS profiles to a private directory")
	cmd.Flags().BoolVar(&listing, "list-accounts", false, "List account labels and counts without UUIDs")
	cmd.Flags().StringVar(&checkFile, "check-profile", "", "Verify a private VLESS profile with local Xray and HTTPS requests")
	cmd.Flags().StringVar(&label, "label", "", "Account label (email is a convention; not published in PR metadata)")
	cmd.Flags().StringSliceVar(&zones, "zone", nil, "Gateway zone(s); repeat or comma-separate (default: all for account edits)")
	cmd.Flags().StringVar(&endpoint, "endpoint", "", "Override exported connection IP/DNS; does not provision a host or edit DNS")
	cmd.Flags().StringVar(&output, "output-dir", "", "Private profile output directory outside Git; existing files are never overwritten")
	cmd.Flags().StringVar(&httpsHost, "https-host", "www.hackamonth.io", "Public HTTPS/XHTTP backup hostname")
	cmd.Flags().StringVar(&expectedExit, "expected-exit-ip", "", "Expected profile-check exit IP (default: control-plane inventory address)")
	cmd.Flags().BoolVar(&local, "local-only", false, "Write encrypted/configuration edits without commit or PR")
	_ = cmd.MarkFlagDirname("output-dir")
	_ = cmd.MarkFlagFilename("check-profile")
	cmd.RunE = func(cmd *cobra.Command, args []string) error {
		actions := 0
		for _, v := range []bool{add, remove, export, listing, checkFile != ""} {
			if v {
				actions++
			}
		}
		if actions != 1 {
			return usage("select exactly one action: --add-account, --remove-account, --export-account, --list-accounts or --check-profile")
		}
		if (add || remove || export) && label == "" {
			return usage("--label is required for this action")
		}
		if export && (len(zones) != 1 || output == "") {
			return usage("--export-account requires exactly one --zone and --output-dir")
		}
		if local && (o.DryRun || export || listing || checkFile != "") {
			return usage("--local-only is only valid for mutations and cannot combine with --dry-run")
		}
		if endpoint != "" && !export {
			return usage("--endpoint is only valid with --export-account")
		}
		if output != "" && !export {
			return usage("--output-dir is only valid with --export-account")
		}

		if add || remove {
			if err := validateLabel(label); err != nil {
				return usage(err.Error())
			}
		}
		if label != "" && !(add || remove || export) {
			return usage("--label is only valid for client account changes or export")
		}
		if len(zones) > 0 && !(add || remove || export || listing) {
			return usage("--zone is not valid for this action")
		}
		if expectedExit != "" && checkFile == "" {
			return usage("--expected-exit-ip requires --check-profile")
		}
		if cmd.Flags().Changed("https-host") && !export {
			return usage("--https-host requires --export-account")
		}
		if endpoint != "" {
			if _, err := normalizeEndpoint(endpoint); err != nil {
				return usage(err.Error())
			}
		}
		r, close, err := o.open(cmd)
		if err != nil {
			return err
		}
		defer close()
		if checkFile != "" {
			if o.DryRun {
				return usage("--check-profile performs network requests and does not support --dry-run")
			}
			return o.check(r, checkFile, expectedExit)
		}
		if !o.DryRun && !export && !listing && !local {
			if err = r.PreparePR(); err != nil {
				return err
			}
		}
		var unlock func()
		if !o.DryRun && !export && !listing {
			unlock, err = r.Lock()
			if err != nil {
				return err
			}
			defer unlock()
		}
		doc, err := r.loadSecret()
		if err != nil {
			return err
		}
		defer doc.close()
		if listing {
			selected := doc.configs
			if len(zones) > 0 {
				selected = map[string]object{}
				for _, zone := range zones {
					c, ok := doc.configs[zone]
					if !ok {
						return usage("unknown gateway zone")
					}
					selected[zone] = c
				}
			}
			return o.emit(Result{Status: "configured accounts", Clients: clientInfos(selected)})
		}
		if export {
			c, ok := doc.configs[zones[0]]
			if !ok {
				return usage("unknown gateway zone")
			}
			hosts, err := r.endpoints(o.Cluster, zones[0], endpoint)
			if err != nil {
				return err
			}
			profiles, err := profilesFor(c, label, hosts, httpsHost)
			if err != nil {
				return err
			}
			if o.DryRun {
				files, err := validateExport(r.Path, output, profiles)
				if err != nil {
					return err
				}
				return o.emit(Result{Status: "dry-run: private profiles validated; no files written", Files: files})
			}
			files, err := exportProfiles(r.Path, output, profiles)
			if err != nil {
				return err
			}
			return o.emit(Result{Status: "private profiles exported", Files: files})
		}
		if len(zones) == 0 {
			zones = sortedKeys(doc.configs)
		}
		zones = unique(zones)
		changed := false
		switch {
		case add:
			changed, err = addClient(doc.configs, label, zones)
		case remove:
			changed, err = removeClient(doc.configs, label, zones)
		}
		if err != nil {
			return err
		}
		if !changed {
			return o.emit(Result{Status: "unchanged; existing UUIDs and encrypted files preserved"})
		}
		if o.DryRun {
			return o.emit(Result{Status: "dry-run: encrypted account update planned; no files written"})
		}
		encrypted, err := doc.encode()
		if err != nil {
			return err
		}
		if err = r.AtomicWrite(secretPath, encrypted); err != nil {
			return err
		}
		result := Result{Status: "encrypted source updated; no direct cluster changes", Files: []string{secretPath}}
		if !local {
			title := "Update VPN service accounts"
			if add {
				title = "Add VPN client"
			}
			if remove {
				title = "Revoke VPN client access"
			}
			result.PR, err = r.OpenPR([]string{secretPath}, title, accountPRBody)
			if err != nil {
				return err
			}
			result.Status = "pull request opened; merge and let Flux deploy before use"
		}
		return o.emit(result)
	}
	return cmd
}
func unique(values []string) []string {
	seen := map[string]bool{}
	var result []string
	for _, v := range values {
		if !seen[v] {
			seen[v] = true
			result = append(result, v)
		}
	}
	return result
}
func clientInfos(configs map[string]object) []ClientInfo {
	ids, zones := map[string]map[string]bool{}, map[string]map[string]bool{}
	for zone, c := range configs {
		for _, i := range inbounds(c) {
			for _, v := range clients(i) {
				u := asObject(v)
				label := text(u, "email")
				if ids[label] == nil {
					ids[label] = map[string]bool{}
					zones[label] = map[string]bool{}
				}
				ids[label][text(u, "id")] = true
				zones[label][zone] = true
			}
		}
	}
	var result []ClientInfo
	for _, label := range sortedKeys(ids) {
		result = append(result, ClientInfo{label, sortedKeys(zones[label]), len(ids[label]), strings.HasSuffix(label, "@service-account")})
	}
	return result
}
func removeClient(configs map[string]object, label string, zones []string) (bool, error) {
	if err := validateLabel(label); err != nil {
		return false, err
	}
	for _, zone := range zones {
		if configs[zone] == nil {
			return false, errors.New("unknown gateway zone")
		}
	}
	changed := false
	for _, zone := range zones {
		for _, i := range inbounds(configs[zone]) {
			var kept []any
			for _, v := range clients(i) {
				if text(asObject(v), "email") == label {
					changed = true
				} else {
					kept = append(kept, v)
				}
			}
			if settings := asObject(i["settings"]); settings != nil && settings["clients"] != nil {
				if kept == nil {
					kept = []any{}
				}
				settings["clients"] = kept
			}
		}
	}
	return changed, nil
}

const accountPRBody = "## Summary\n\nClient/service configuration → SOPS-encrypted Secret → Flux after merge.\n\nClient labels and credentials are deliberately omitted from public GitHub metadata. Existing identities are preserved unless explicitly revoked.\n\n## Evidence\n\nNative SOPS MAC verification and account validation completed before encryption. No plaintext credentials written or printed.\n\n## Merge Danger\n\n**Door:** two-way encrypted Git change.\n\n**Blast Radius:** VPN access; Flux rolls affected Xray workloads after merge.\n"

// GatewayCommand parses gateway-owned arguments after generic CLI dispatch.
func GatewayCommand(shared *cli.Options) *cobra.Command {
	o := (*options)(shared)
	cmd := newGatewayCommand(o)
	shared.BindFlags(cmd)
	return cmd
}
