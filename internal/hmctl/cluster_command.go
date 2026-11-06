package hmctl

import (
	"errors"
	"github.com/sidosera/hm-gitops/internal/cli"
	"github.com/spf13/cobra"
	"go.yaml.in/yaml/v3"
	"path/filepath"
)

func ClusterCommand(shared *cli.Options) *cobra.Command {
	o := (*options)(shared)
	var address, ipv6, key string
	var local bool
	root := &cobra.Command{Use: "cluster", Short: "Manage cluster infrastructure"}
	hosts := &cobra.Command{Use: "host", Short: "Manage physical inventory independently of service credentials"}
	cmd := &cobra.Command{Use: "replace <host-slot>", Short: "Prepare a physical host replacement PR; never provision or change DNS", Args: oneArg, Example: "  hmctl cluster host replace ru-spb-1/1 --ip NEW_IPV4 --ssh-host-key 'ssh-ed25519 VERIFIED_KEY'"}
	cmd.Flags().StringVar(&address, "ip", "", "New public IPv4 address")
	cmd.Flags().StringVar(&ipv6, "ipv6", "", "New public IPv6; omitted removes the old public IPv6")
	cmd.Flags().StringVar(&key, "ssh-host-key", "", "New ed25519 host key, independently verified through the provider console")
	cmd.Flags().BoolVar(&local, "local-only", false, "Prepare inventory/SSH edits without a PR")
	cmd.RunE = func(cmd *cobra.Command, args []string) error {
		if address == "" || key == "" {
			return usage("--ip and independently verified --ssh-host-key are required")
		}
		if local && o.DryRun {
			return usage("--local-only and --dry-run cannot combine")
		}
		r, close, err := o.open(cmd)
		if err != nil {
			return err
		}
		defer close()
		if !o.DryRun && !local {
			if err = r.preparePR(); err != nil {
				return err
			}
		}
		if !o.DryRun {
			unlock, err := r.lock()
			if err != nil {
				return err
			}
			defer unlock()
		}
		path := "cluster/clusters/" + o.Cluster + "/hosts.yml"
		var inventory, profile object
		if err = r.readYAML(path, &inventory); err != nil {
			return err
		}
		if err = r.readYAML(filepath.ToSlash(filepath.Join("cluster/clusters", o.Cluster, "profile.yml")), &profile); err != nil {
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
		if o.DryRun {
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
	hosts.AddCommand(cmd)
	root.AddCommand(hosts)
	return root
}

const hostPRBody = "## Summary\n\nNew physical address → inventory and pinned SSH trust → existing Ansible bootstrap. Logical node, zone, private addresses and all client UUIDs are preserved.\n\n## Evidence\n\nInventory and SSH-key validation passed. The encrypted client Secret is untouched.\n\n## Merge Danger\n\n**Door:** two-way inventory change; physical cutover needs a maintenance plan.\n\n**Blast Radius:** host replacement. Provision the new host with Cluster Converge, verify VPN connectivity, then move DNS or issue updated profiles. Control-plane replacement additionally requires datastore recovery or an explicit cluster rebuild/rejoin. This PR does not provision or modify DNS.\n"
