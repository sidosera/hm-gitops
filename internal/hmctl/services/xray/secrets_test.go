package xray

import (
	"bytes"
	"context"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"io"
	"os"
	"path/filepath"
	"strings"
	"testing"

	age "filippo.io/age"
	"github.com/getsops/sops/v3"
	"github.com/getsops/sops/v3/aes"
	sopsage "github.com/getsops/sops/v3/age"
	"github.com/getsops/sops/v3/cmd/sops/common"
	"github.com/getsops/sops/v3/config"
	sopsyaml "github.com/getsops/sops/v3/stores/yaml"
	"github.com/sidosera/hm-gitops/internal/cli"
	core "github.com/sidosera/hm-gitops/internal/hmctl"
	"github.com/spf13/cobra"
	"go.yaml.in/yaml/v3"
)

func encryptedFixture(t *testing.T) (string, string) {
	t.Helper()
	base := t.TempDir()
	repo := filepath.Join(base, "repo")
	os.MkdirAll(filepath.Join(repo, "conf/xray"), 0700)
	identity, err := age.GenerateX25519Identity()
	if err != nil {
		t.Fatal(err)
	}
	keyFile := filepath.Join(base, "identity.agekey")
	if err = os.WriteFile(keyFile, []byte(identity.String()+"\n"), 0600); err != nil {
		t.Fatal(err)
	}
	key, _ := sopsage.MasterKeyFromRecipient(identity.Recipient().String())
	dataKey := make([]byte, 32)
	rand.Read(dataKey)
	defer clear(dataKey)
	if err = key.Encrypt(dataKey); err != nil {
		t.Fatal(err)
	}
	gateway := object{"inbounds": []any{fixtureInbound("gateway", "raw", "existing", "original-id")}, "opaque_number": json.Number("9007199254740993")}
	raw, _ := json.Marshal(gateway)
	plain, _ := yaml.Marshal(object{"apiVersion": "v1", "kind": "Secret", "metadata": object{"name": "xray-credentials", "namespace": "hm"}, "data": object{"a.gateway.json": base64.StdEncoding.EncodeToString(raw), "unrelated.txt": base64.StdEncoding.EncodeToString([]byte("unchanged-extra-payload"))}})
	store := sopsyaml.NewStore(&config.YAMLStoreConfig{Indent: 4})
	branches, err := store.LoadPlainFile(plain)
	if err != nil {
		t.Fatal(err)
	}
	tree := sops.Tree{Branches: branches, Metadata: sops.Metadata{KeyGroups: []sops.KeyGroup{{key}}, EncryptedRegex: "^(data|stringData)$", Version: "3.13.3"}}
	if err = common.EncryptTree(common.EncryptTreeOpts{Tree: &tree, Cipher: aes.NewCipher(), DataKey: dataKey}); err != nil {
		t.Fatal(err)
	}
	encrypted, err := store.EmitEncryptedFile(tree)
	if err != nil {
		t.Fatal(err)
	}
	if err = os.WriteFile(filepath.Join(repo, secretPath), encrypted, 0644); err != nil {
		t.Fatal(err)
	}
	return repo, keyFile
}
func openFixture(t *testing.T, path, key string) *repository {
	t.Helper()
	base, err := core.OpenRepository(context.Background(), path)
	if err != nil {
		t.Fatal(err)
	}
	r := &repository{base}
	r.KeyFile = key
	t.Cleanup(r.Close)
	return r
}
func TestNativeSOPSRoundTripAndIdempotentCLIWithoutExecutables(t *testing.T) {
	repo, key := encryptedFixture(t)
	t.Setenv("PATH", t.TempDir())
	before, _ := os.ReadFile(filepath.Join(repo, secretPath))
	run := func(flags ...string) string {
		t.Helper()
		var stdout, stderr bytes.Buffer
		cmd := newTestCommand("test", &stdout, &stderr)
		cmd.SetArgs(append([]string{"--repo", repo, "--age-key-file", key, "--json", "service", "gateway", "--add-account", "--label", "alice"}, flags...))
		if err := cmd.Execute(); err != nil {
			t.Fatal(err)
		}
		if stderr.Len() != 0 {
			t.Fatalf("unexpected stderr: %s", stderr.String())
		}
		return stdout.String()
	}
	run("--dry-run")
	dry, _ := os.ReadFile(filepath.Join(repo, secretPath))
	if !bytes.Equal(before, dry) {
		t.Fatal("dry-run changed ciphertext")
	}
	if _, err := os.Stat(filepath.Join(repo, ".hmctl.lock")); !os.IsNotExist(err) {
		t.Fatal("dry-run wrote lock file")
	}
	output := run("--local-only")
	after, _ := os.ReadFile(filepath.Join(repo, secretPath))
	if bytes.Equal(before, after) {
		t.Fatal("encrypted update not written")
	}
	r := openFixture(t, repo, key)
	doc, err := r.loadSecret()
	if err != nil {
		t.Fatal(err)
	}
	defer doc.close()
	if string(doc.original["unrelated.txt"]) != "unchanged-extra-payload" {
		t.Fatal("unrelated Secret data changed")
	}
	c := doc.configs["a"]
	if c["opaque_number"] != json.Number("9007199254740993") {
		t.Fatal("unknown JSON number rounded")
	}
	if text(asObject(clients(inbounds(c)[0])[0]), "id") != "original-id" {
		t.Fatal("existing UUID changed")
	}
	id := text(asObject(clients(inbounds(c)[0])[1]), "id")
	if strings.Contains(output, id) || bytes.Contains(after, []byte(id)) {
		t.Fatal("credential leaked to output or unencrypted Git source")
	}
	if !strings.Contains(run("--local-only"), "unchanged") {
		t.Fatal("repeat should be no-op")
	}
	again, _ := os.ReadFile(filepath.Join(repo, secretPath))
	if !bytes.Equal(after, again) {
		t.Fatal("repeat reencrypted source")
	}
}
func TestSOPSMACAndIdentityPermissionsFailClosed(t *testing.T) {
	repo, key := encryptedFixture(t)
	r := openFixture(t, repo, key)
	raw, _ := os.ReadFile(filepath.Join(repo, secretPath))
	tampered := bytes.Replace(raw, []byte("namespace: hm"), []byte("namespace: changed"), 1)
	if bytes.Equal(raw, tampered) {
		t.Fatal("fixture mutation failed")
	}
	os.WriteFile(filepath.Join(repo, secretPath), tampered, 0644)
	if doc, err := r.loadSecret(); err == nil {
		doc.close()
		t.Fatal("unauthenticated SOPS metadata accepted")
	}
	os.WriteFile(filepath.Join(repo, secretPath), raw, 0644)
	os.Chmod(key, 0644)
	if doc, err := r.loadSecret(); err == nil {
		doc.close()
		t.Fatal("world-readable age identity accepted")
	}
}
func TestCLIValidationHelpAndJSONNeverRequireSecrets(t *testing.T) {
	for _, args := range [][]string{{"service", "unknown", "--list-accounts"}, {"service", "gateway", "--add-account", "--remove-account", "--label", "alice"}, {"service", "gateway", "--export-account", "--label", "alice"}, {"service", "gateway", "--add-account", "--label", "alice", "--dry-run", "--local-only"}, {"--cluster", "../escape", "service", "gateway", "--list-accounts"}} {
		var output bytes.Buffer
		cmd := newTestCommand("v-test", &output, &output)
		cmd.SetArgs(args)
		if err := cmd.Execute(); err == nil {
			t.Fatalf("invalid CLI accepted: %v", args)
		}
	}
	for _, args := range [][]string{{"--help"}, {"version"}, {"completion", "zsh"}} {
		var output bytes.Buffer
		cmd := newTestCommand("v-test", &output, &output)
		cmd.SetArgs(args)
		if err := cmd.Execute(); err != nil || output.Len() == 0 {
			t.Fatalf("help/version/completion failed: %v", err)
		}
	}
}

func newTestCommand(version string, out, stderr io.Writer) *cobra.Command {
	return cli.NewCommand(version, out, stderr, map[string]cli.Factory{"gateway": GatewayCommand, "vpn-probe": ProbeCommand}, nil)
}
