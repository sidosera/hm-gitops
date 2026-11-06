package hmctl

import (
	"bytes"
	"context"
	"crypto/ed25519"
	"crypto/rand"
	"net/netip"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	"go.yaml.in/yaml/v3"
	"golang.org/x/crypto/ssh"
)

func TestPhysicalReplacementPreservesLogicalIdentityAndCredentials(t *testing.T) {
	root, err := filepath.Abs("../..")
	if err != nil {
		t.Fatal(err)
	}
	repo, err := openRepository(context.Background(), root)
	if err != nil {
		t.Fatal(err)
	}
	defer repo.close()
	var inventory, profile object
	if err := repo.readYAML("cluster/clusters/hm-cluster/hosts.yml", &inventory); err != nil {
		t.Fatal(err)
	}
	if err := repo.readYAML("cluster/clusters/hm-cluster/profile.yml", &profile); err != nil {
		t.Fatal(err)
	}
	known, err := os.ReadFile(filepath.Join(root, "vm/ssh_known_hosts"))
	if err != nil {
		t.Fatal(err)
	}
	before, err := os.ReadFile(filepath.Join(root, secretPath))
	if err != nil {
		t.Fatal(err)
	}
	old := list(inventory, "vm_agent_hosts")[0].(string)
	machine := asObject(asObject(inventory["vm_node_settings"])[old])
	slot := text(machine, "host")
	pub, _, _ := ed25519.GenerateKey(rand.Reader)
	sshKey, _ := ssh.NewPublicKey(pub)
	key := strings.TrimSpace(string(ssh.MarshalAuthorizedKey(sshKey)))
	updated, trust, err := replaceHost(inventory, string(known), slot, "8.8.8.8", key, "")
	if err != nil {
		t.Fatal(err)
	}
	if err = validateInventory(updated, profile); err != nil {
		t.Fatal(err)
	}
	if text(asObject(asObject(updated["vm_node_settings"])["8.8.8.8"]), "private_ipv4") != text(machine, "private_ipv4") {
		t.Fatal("private address changed")
	}
	if text(asObject(asObject(updated["vm_node_settings"])["8.8.8.8"]), "kubernetes_name") != text(machine, "kubernetes_name") {
		t.Fatal("node identity changed")
	}
	if !reflect.DeepEqual(updated["vm_hosts"], inventory["vm_hosts"]) {
		t.Fatal("another host changed")
	}
	if !strings.Contains(trust, "8.8.8.8 "+key) || strings.Contains(trust, old+" ") {
		t.Fatal("SSH trust not replaced")
	}
	after, err := os.ReadFile(filepath.Join(root, secretPath))
	if err != nil {
		t.Fatal(err)
	}
	if string(before) != string(after) {
		t.Fatal("client Secret modified")
	}
	for _, tc := range []struct{ slot, ip, key, ipv6 string }{{slot, "127.0.0.1", key, ""}, {"missing", "8.8.8.8", key, ""}, {slot, "8.8.8.8", "ssh-ed25519 invalid", ""}, {slot, "8.8.8.8", key, "fd00::1"}} {
		if _, _, err := replaceHost(inventory, string(known), tc.slot, tc.ip, tc.key, tc.ipv6); err == nil {
			t.Fatal("invalid replacement accepted")
		}
	}
	// Confirm the pure operation did not mutate its source inventory.
	var unchanged object
	data, err := os.ReadFile(filepath.Join(root, "cluster/clusters/hm-cluster/hosts.yml"))
	if err != nil {
		t.Fatal(err)
	}
	if err := yaml.Unmarshal(data, &unchanged); err != nil {
		t.Fatal(err)
	}
	if !reflect.DeepEqual(inventory, unchanged) {
		t.Fatal("replacement mutated input")
	}
}

func TestInventoryCloneRetainsUnknownYAMLTypes(t *testing.T) {
	original := object{"custom_port": 2222, "metadata": object{"enabled": true, "nested": []any{object{"count": 3}}}}
	copy := clone(original)
	if !reflect.DeepEqual(original, copy) {
		t.Fatal("unknown YAML field types changed")
	}
	asObject(copy["metadata"])["enabled"] = false
	if asObject(original["metadata"])["enabled"] != true {
		t.Fatal("clone shares mutable containers")
	}
}

func TestPublicIPv6RejectsMappedZonedAndDocumentationAddresses(t *testing.T) {
	for _, raw := range []string{"::ffff:10.0.0.1", "::ffff:8.8.8.8", "2001:4860::1%eth0", "2001:db8::1", "3fff::1", "3fff:fff::1", "fd00::1"} {
		if publicAddress(netip.MustParseAddr(raw)) {
			t.Fatalf("invalid public address accepted: %s", raw)
		}
	}
	if !publicAddress(netip.MustParseAddr("2001:4860:4860::8888")) {
		t.Fatal("public IPv6 rejected")
	}
}

func TestReplacementPreservesAliasesAndRejectsTrustConflict(t *testing.T) {
	pub, _, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	key, err := ssh.NewPublicKey(pub)
	if err != nil {
		t.Fatal(err)
	}
	trusted := strings.TrimSpace(string(ssh.MarshalAuthorizedKey(key)))
	inventory := object{"vm_node_settings": object{"1.1.1.1": object{"host": "test-zone/1", "name": "1.1.1.1"}}, "vm_hosts": []any{}, "vm_agent_hosts": []any{"1.1.1.1"}}
	known := "# keep this comment\n\n1.1.1.1,old-alias " + trusted + " old comment\n8.8.8.8,new-alias " + trusted + " matching\n"
	_, result, err := replaceHost(inventory, known, "test-zone/1", "8.8.8.8", trusted, "")
	if err != nil {
		t.Fatal(err)
	}
	for _, want := range []string{"# keep this comment\n\n", "old-alias " + trusted + " old comment", "new-alias " + trusted + " matching", "8.8.8.8 " + trusted} {
		if !strings.Contains(result, want) {
			t.Fatalf("lost trust content: %s", want)
		}
	}
	if strings.Contains(result, "1.1.1.1,") {
		t.Fatal("old address retained")
	}
	otherPub, _, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	otherKey, err := ssh.NewPublicKey(otherPub)
	if err != nil {
		t.Fatal(err)
	}
	conflicting := "alias,8.8.8.8 " + strings.TrimSpace(string(ssh.MarshalAuthorizedKey(otherKey))) + "\n"
	if _, _, err := replaceHost(inventory, conflicting, "test-zone/1", "8.8.8.8", trusted, ""); err == nil {
		t.Fatal("conflicting pinned key accepted")
	}
}

func TestClusterCommandDoesNotRequireVPNSecret(t *testing.T) {
	root := t.TempDir()
	for _, path := range []string{"cluster/clusters/hm-cluster/hosts.yml", "cluster/clusters/hm-cluster/profile.yml", "vm/ssh_known_hosts"} {
		data, err := os.ReadFile(filepath.Join("../..", path))
		if err != nil {
			t.Fatal(err)
		}
		dest := filepath.Join(root, path)
		if err := os.MkdirAll(filepath.Dir(dest), 0700); err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(dest, data, 0600); err != nil {
			t.Fatal(err)
		}
	}
	var output bytes.Buffer
	cmd := newTestCommand("test", &output, &output)
	pub, _, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	key, err := ssh.NewPublicKey(pub)
	if err != nil {
		t.Fatal(err)
	}
	cmd.SetArgs([]string{"--repo", root, "--dry-run", "cluster", "host", "replace", "ru-spb-1/1", "--ip", "8.8.8.8", "--ssh-host-key", strings.TrimSpace(string(ssh.MarshalAuthorizedKey(key)))})
	if err := cmd.Execute(); err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(output.String(), "dry-run") {
		t.Fatal("cluster handler did not run")
	}
}
