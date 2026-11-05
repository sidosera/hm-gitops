package hmctl

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"path/filepath"
	"regexp"
	"strings"
	"time"

	"github.com/spf13/cobra"
	"go.yaml.in/yaml/v3"
)

type UsageError struct{ Message string }

func (e *UsageError) Error() string { return e.Message }
func usage(message string) error    { return &UsageError{message} }

type options struct {
	repo, cluster, keyFile string
	json, dryRun           bool
	timeout                time.Duration
	out                    io.Writer
}
type Result struct {
	SchemaVersion int           `json:"schema_version"`
	Status        string        `json:"status"`
	Files         []string      `json:"files,omitempty"`
	PR            string        `json:"pr_url,omitempty"`
	Clients       []ClientInfo  `json:"clients,omitempty"`
	Checks        []CheckResult `json:"checks,omitempty"`
}
type ClientInfo struct {
	Name     string   `json:"name"`
	Zones    []string `json:"zones"`
	Accounts int      `json:"accounts"`
	Service  bool     `json:"service_account"`
}

func (o *options) emit(result Result) error {
	result.SchemaVersion = 1
	if o.json {
		return json.NewEncoder(o.out).Encode(result)
	}
	fmt.Fprintln(o.out, result.Status)
	if result.PR != "" {
		fmt.Fprintln(o.out, result.PR)
	}
	for _, file := range result.Files {
		fmt.Fprintln(o.out, file)
	}
	for _, client := range result.Clients {
		kind := "client"
		if client.Service {
			kind = "service"
		}
		fmt.Fprintf(o.out, "%s\t%s\t%s\t%d account(s)\n", client.Name, kind, strings.Join(client.Zones, ","), client.Accounts)
	}
	for _, check := range result.Checks {
		fmt.Fprintf(o.out, "%s: %t\n", check.Name, check.Passed)
	}
	return nil
}

var clusterPattern = regexp.MustCompile(`^[a-z0-9]+(?:-[a-z0-9]+)*$`)

func (o *options) open(cmd *cobra.Command) (*repository, func(), error) {
	if !clusterPattern.MatchString(o.cluster) {
		return nil, nil, usage("invalid --cluster")
	}
	if o.timeout <= 0 {
		return nil, nil, usage("--timeout must be positive")
	}
	ctx, cancel := context.WithTimeout(cmd.Context(), o.timeout)
	r, err := openRepository(ctx, o.repo)
	if err != nil {
		cancel()
		return nil, nil, err
	}
	r.keyFile = o.keyFile
	return r, func() { r.close(); cancel() }, nil
}
func NewCommand(version string, out, stderr io.Writer) *cobra.Command {
	o := &options{out: out}
	root := &cobra.Command{Use: "hmctl", Short: "Manage HM services through encrypted GitOps pull requests", Version: version, SilenceUsage: true, SilenceErrors: true}
	root.SetOut(out)
	root.SetErr(stderr)
	root.SetFlagErrorFunc(func(_ *cobra.Command, err error) error { return usage(err.Error()) })
	root.PersistentFlags().StringVar(&o.repo, "repo", "", "GitOps checkout (default: discover from current directory)")
	root.PersistentFlags().StringVar(&o.cluster, "cluster", "hm-cluster", "Cluster configuration name")
	root.PersistentFlags().StringVar(&o.keyFile, "age-key-file", "", "Native age identity file (default: SOPS_AGE_KEY_FILE)")
	root.PersistentFlags().DurationVar(&o.timeout, "timeout", 2*time.Minute, "Maximum operation duration")
	root.PersistentFlags().BoolVar(&o.json, "json", false, "Emit versioned JSON metadata; never credential values")
	root.PersistentFlags().BoolVar(&o.dryRun, "dry-run", false, "Validate and plan without writing files, committing or opening a PR")
	root.SetVersionTemplate("hmctl {{.Version}}\n")
	root.AddCommand(newServiceCommand(o), newHostCommand(o))
	root.AddCommand(&cobra.Command{Use: "version", Short: "Print binary version", Args: noArgs, Run: func(cmd *cobra.Command, _ []string) { fmt.Fprintln(cmd.OutOrStdout(), "hmctl", version) }})
	return root
}
func noArgs(_ *cobra.Command, args []string) error {
	if len(args) != 0 {
		return usage("unexpected positional arguments")
	}
	return nil
}
func oneArg(_ *cobra.Command, args []string) error {
	if len(args) != 1 {
		return usage("exactly one service or host slot is required")
	}
	return nil
}
func newServiceCommand(o *options) *cobra.Command {
	var add, remove, export, listing, migrate, syncProbe, local bool
	var name, endpoint, output, httpsHost, checkFile, expectedExit string
	var zones []string
	cmd := &cobra.Command{Use: "service-cfg <service>", Short: "Inspect or change a service configuration", Args: oneArg,
		ValidArgs: []string{"gateway", "vpn-probe"},
		Example:   "  hmctl service-cfg gateway --add-client --name alice\n  hmctl service-cfg gateway --export-client --name alice --zone ru-spb-1 --output-dir /private/alice\n  hmctl service-cfg gateway --list-clients --json\n  hmctl service-cfg vpn-probe --sync-credentials --dry-run"}
	cmd.Flags().BoolVar(&add, "add-client", false, "Generate/reuse a client UUID and open an encrypted account PR")
	cmd.Flags().BoolVar(&remove, "remove-client", false, "Revoke a client's access in selected zones through a PR")
	cmd.Flags().BoolVar(&export, "export-client", false, "Write a client's VLESS profiles to a private directory")
	cmd.Flags().BoolVar(&listing, "list-clients", false, "List account labels and counts without UUIDs")
	cmd.Flags().BoolVar(&migrate, "migrate-service-labels", false, "Rename known legacy service labels without rotating UUIDs")
	cmd.Flags().BoolVar(&syncProbe, "sync-credentials", false, "Prepare dedicated VPN-probe accounts and client configs")
	cmd.Flags().StringVar(&checkFile, "check-profile", "", "Verify a private VLESS profile with local Xray and HTTPS requests")
	cmd.Flags().StringVar(&name, "name", "", "Client label (short name or email; not published in PR metadata)")
	cmd.Flags().StringSliceVar(&zones, "zone", nil, "Gateway zone(s); repeat or comma-separate (default: all for account edits)")
	cmd.Flags().StringVar(&endpoint, "endpoint", "", "Override exported connection IP/DNS; does not provision a host or edit DNS")
	cmd.Flags().StringVar(&output, "output-dir", "", "Private profile output directory outside Git; existing files are never overwritten")
	cmd.Flags().StringVar(&httpsHost, "https-host", "www.hackamonth.io", "Public HTTPS/XHTTP backup hostname")
	cmd.Flags().StringVar(&expectedExit, "expected-exit-ip", "", "Expected profile-check exit IP (default: control-plane inventory address)")
	cmd.Flags().BoolVar(&local, "local-only", false, "Write encrypted/configuration edits without commit or PR")
	_ = cmd.MarkFlagDirname("output-dir")
	_ = cmd.MarkFlagFilename("check-profile")
	cmd.RunE = func(cmd *cobra.Command, args []string) error {
		service := args[0]
		if service != "gateway" && service != "vpn-probe" {
			return usage("unknown service; use gateway or vpn-probe")
		}
		actions := 0
		for _, v := range []bool{add, remove, export, listing, migrate, syncProbe, checkFile != ""} {
			if v {
				actions++
			}
		}
		if actions != 1 {
			return usage("select exactly one action: --add-client, --remove-client, --export-client, --list-clients, --migrate-service-labels, --sync-credentials or --check-profile")
		}
		if (service == "vpn-probe") != syncProbe {
			return usage("--sync-credentials belongs to vpn-probe; client actions belong to gateway")
		}
		if (add || remove || export) && name == "" {
			return usage("--name is required for this action")
		}
		if export && (len(zones) != 1 || output == "") {
			return usage("--export-client requires exactly one --zone and --output-dir")
		}
		if local && (o.dryRun || export || listing || checkFile != "") {
			return usage("--local-only is only valid for mutations and cannot combine with --dry-run")
		}
		if endpoint != "" && !export {
			return usage("--endpoint is only valid with --export-client")
		}
		if output != "" && !export {
			return usage("--output-dir is only valid with --export-client")
		}

		if add || remove {
			if err := validateLabel(name); err != nil {
				return usage(err.Error())
			}
		}
		if name != "" && !(add || remove || export) {
			return usage("--name is only valid for client account changes or export")
		}
		if len(zones) > 0 && !(add || remove || export || listing) {
			return usage("--zone is not valid for this action")
		}
		if expectedExit != "" && checkFile == "" {
			return usage("--expected-exit-ip requires --check-profile")
		}
		if cmd.Flags().Changed("https-host") && !export {
			return usage("--https-host requires --export-client")
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
			if o.dryRun {
				return usage("--check-profile performs network requests and does not support --dry-run")
			}
			return o.check(r, checkFile, expectedExit)
		}
		if !o.dryRun && !export && !listing && !local {
			if err = r.preparePR(); err != nil {
				return err
			}
		}
		var unlock func()
		if !o.dryRun && !export && !listing {
			unlock, err = r.lock()
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
			hosts, err := r.endpoints(o.cluster, zones[0], endpoint)
			if err != nil {
				return err
			}
			profiles, err := profilesFor(c, name, hosts, httpsHost)
			if err != nil {
				return err
			}
			if o.dryRun {
				files, err := validateExport(r.path, output, profiles)
				if err != nil {
					return err
				}
				return o.emit(Result{Status: "dry-run: private profiles validated; no files written", Files: files})
			}
			files, err := exportProfiles(r.path, output, profiles)
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
			changed, err = addClient(doc.configs, name, zones)
		case remove:
			changed, err = removeClient(doc.configs, name, zones)
		case migrate:
			changed = migrateLabels(doc.configs)
		case syncProbe:
			changed, err = syncProbeCredentials(doc)
		}
		if err != nil {
			return err
		}
		if !changed {
			return o.emit(Result{Status: "unchanged; existing UUIDs and encrypted files preserved"})
		}
		if o.dryRun {
			return o.emit(Result{Status: "dry-run: encrypted account update planned; no files written"})
		}
		encrypted, err := doc.encode()
		if err != nil {
			return err
		}
		if err = r.atomicWrite(secretPath, encrypted); err != nil {
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
			result.PR, err = r.openPR([]string{secretPath}, title, accountPRBody)
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

func newHostCommand(o *options) *cobra.Command {
	var address, ipv6, key string
	var local bool
	root := &cobra.Command{Use: "host", Short: "Manage physical inventory independently of service credentials"}
	cmd := &cobra.Command{Use: "replace <host-slot>", Short: "Prepare a physical host replacement PR; never provision or change DNS", Args: oneArg, Example: "  hmctl host replace ru-spb-1/1 --ip NEW_IPV4 --ssh-host-key 'ssh-ed25519 VERIFIED_KEY'"}
	cmd.Flags().StringVar(&address, "ip", "", "New public IPv4 address")
	cmd.Flags().StringVar(&ipv6, "ipv6", "", "New public IPv6; omitted removes the old public IPv6")
	cmd.Flags().StringVar(&key, "ssh-host-key", "", "New ed25519 host key, independently verified through the provider console")
	cmd.Flags().BoolVar(&local, "local-only", false, "Prepare inventory/SSH edits without a PR")
	cmd.RunE = func(cmd *cobra.Command, args []string) error {
		if address == "" || key == "" {
			return usage("--ip and independently verified --ssh-host-key are required")
		}
		if local && o.dryRun {
			return usage("--local-only and --dry-run cannot combine")
		}
		r, close, err := o.open(cmd)
		if err != nil {
			return err
		}
		defer close()
		if !o.dryRun && !local {
			if err = r.preparePR(); err != nil {
				return err
			}
		}
		if !o.dryRun {
			unlock, err := r.lock()
			if err != nil {
				return err
			}
			defer unlock()
		}
		path := "cluster/clusters/" + o.cluster + "/hosts.yml"
		var inventory, profile object
		if err = r.readYAML(path, &inventory); err != nil {
			return err
		}
		if err = r.readYAML(filepath.ToSlash(filepath.Join("cluster/clusters", o.cluster, "profile.yml")), &profile); err != nil {
			return err
		}
		known, err := r.root.ReadFile("vm/ssh_known_hosts")
		if err != nil {
			return errors.New("cannot read pinned SSH host keys")
		}
		updated, trust, err := replaceHost(inventory, string(known), args[0], address, key, ipv6)
		if err != nil {
			return err
		}
		if err = validateInventory(updated, profile); err != nil {
			return err
		}
		if o.dryRun {
			return o.emit(Result{Status: "dry-run: host replacement validated; no files, hosts, DNS or UUIDs changed"})
		}
		data, err := yaml.Marshal(updated)
		if err != nil {
			return errors.New("cannot encode inventory")
		}
		if err = r.atomicWrite(path, data); err != nil {
			return err
		}
		if err = r.atomicWrite("vm/ssh_known_hosts", []byte(trust)); err != nil {
			return err
		}
		result := Result{Status: "inventory prepared; no live hosts, DNS or UUIDs changed", Files: []string{path, "vm/ssh_known_hosts"}}
		if !local {
			result.PR, err = r.openPR(result.Files, "Replace VPN host", hostPRBody)
			if err != nil {
				return err
			}
			result.Status = "host replacement PR opened; provision and cut over separately"
		}
		return o.emit(result)
	}
	root.AddCommand(cmd)
	return root
}

const hostPRBody = "## Summary\n\nNew physical address → inventory and pinned SSH trust → existing Ansible bootstrap. Logical node, zone, private addresses and all client UUIDs are preserved.\n\n## Evidence\n\nInventory and SSH-key validation passed. The encrypted client Secret is untouched.\n\n## Merge Danger\n\n**Door:** two-way inventory change; physical cutover needs a maintenance plan.\n\n**Blast Radius:** host replacement. Provision the new host with Cluster Converge, verify VPN connectivity, then move DNS or issue updated profiles. Control-plane replacement additionally requires datastore recovery or an explicit cluster rebuild/rejoin. This PR does not provision or modify DNS.\n"
