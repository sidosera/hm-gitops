package hmctl

import (
	"context"
	"crypto/ed25519"
	"crypto/rand"
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
	repo.readYAML("cluster/clusters/hm-cluster/hosts.yml", &inventory)
	repo.readYAML("cluster/clusters/hm-cluster/profile.yml", &profile)
	known, _ := os.ReadFile(filepath.Join(root, "vm/ssh_known_hosts"))
	before, _ := os.ReadFile(filepath.Join(root, secretPath))
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
	after, _ := os.ReadFile(filepath.Join(root, secretPath))
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
	data, _ := os.ReadFile(filepath.Join(root, "cluster/clusters/hm-cluster/hosts.yml"))
	yaml.Unmarshal(data, &unchanged)
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
