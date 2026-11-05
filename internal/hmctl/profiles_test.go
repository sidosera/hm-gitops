package hmctl

import (
	"encoding/base64"
	"encoding/hex"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestX25519RFC7748Vector(t *testing.T) {
	private, _ := hex.DecodeString("77076d0a7318a57d3c16c17251b26645df4c2f87ebc0992ab177fba51db92c2a")
	expected, _ := hex.DecodeString("8520f0098930a754748b7ddcb43ef75a0dbf3a0d26381af4eba4a98eaa9b4e6a")
	actual, err := publicKey(base64.RawURLEncoding.EncodeToString(private))
	if err != nil || actual != base64.RawURLEncoding.EncodeToString(expected) {
		t.Fatal("X25519 public key mismatch")
	}
}
func TestProfileSelectionAndEndpointReplacement(t *testing.T) {
	inbound := fixtureInbound("gateway", "raw", "first", "first-id")
	asObject(inbound["settings"])["clients"] = append(clients(inbound), object{"id": "selected-id", "email": "alice", "flow": "xtls-rprx-vision"})
	c := object{"inbounds": []any{inbound}}
	for _, host := range []string{"8.8.8.8", "2001:4860:4860::8888", "vpn.example.org"} {
		profiles, err := profilesFor(c, "alice", []string{host}, "example.org")
		if err != nil {
			t.Fatal(err)
		}
		for _, profile := range profiles {
			u, _ := url.Parse(profile)
			if u.User.Username() != "selected-id" || u.Hostname() != host {
				t.Fatal("wrong client or endpoint")
			}
			if u.Query().Get("sni") != "example.org" {
				t.Fatal("endpoint change altered REALITY SNI")
			}
		}
	}
	if _, err := profilesFor(c, "missing", []string{"8.8.8.8"}, "example.org"); err == nil {
		t.Fatal("missing client exported another account")
	}
	for _, host := range []string{"evil@attacker.example", "https://example.org", "bad host", "-bad.example", "a..example"} {
		if _, err := normalizeEndpoint(host); err == nil {
			t.Fatal("malformed endpoint accepted")
		}
	}
}
func TestPrivateExportsNeverOverwriteFollowSymlinksOrEnterRepo(t *testing.T) {
	base := t.TempDir()
	repo := filepath.Join(base, "repo")
	os.Mkdir(repo, 0700)
	profiles := map[string]string{"profile": "vless://test-only"}
	target := filepath.Join(base, "private")
	if _, err := validateExport(repo, target, profiles); err != nil {
		t.Fatal(err)
	}
	if _, err := os.Stat(target); !os.IsNotExist(err) {
		t.Fatal("dry-run created output directory")
	}
	if _, err := validateExport(repo, filepath.Join(repo, "private"), profiles); err == nil {
		t.Fatal("dry-run accepted repository output")
	}

	paths, err := exportProfiles(repo, target, profiles)
	if err != nil {
		t.Fatal(err)
	}
	stat, _ := os.Stat(paths[0])
	if stat.Mode().Perm() != 0600 {
		t.Fatal("profile not private")
	}
	if _, err = exportProfiles(repo, target, profiles); err == nil {
		t.Fatal("existing profile overwritten")
	}
	original, _ := os.ReadFile(paths[0])
	if string(original) != "vless://test-only\n" {
		t.Fatal("profile changed")
	}
	links := filepath.Join(base, "links")
	os.Mkdir(links, 0700)
	os.Symlink(paths[0], filepath.Join(links, "profile.txt"))
	if _, err = exportProfiles(repo, links, profiles); err == nil {
		t.Fatal("followed output symlink")
	}
	alias := filepath.Join(base, "alias")
	os.Symlink(repo, alias)
	if _, err = exportProfiles(repo, filepath.Join(alias, "nested"), profiles); err == nil {
		t.Fatal("export entered repo through symlink")
	}
	if _, err = os.Stat(filepath.Join(repo, "nested")); !os.IsNotExist(err) {
		t.Fatal("invalid output created directories inside repository")
	}
	public := filepath.Join(base, "public")
	os.Mkdir(public, 0755)
	if _, err = exportProfiles(repo, public, profiles); err == nil {
		t.Fatal("world-readable output directory accepted")
	}
}
func TestCheckConfigUsesTLSAndLoopbackSOCKS(t *testing.T) {
	raw := vlessURL("test-id", "example.org", url.Values{"security": {"reality"}, "sni": {"example.org"}, "pbk": {"public"}, "sid": {"1234"}}, "test")
	config, err := profileClient(raw, 10800)
	if err != nil {
		t.Fatal(err)
	}
	if text(inbounds(config)[0], "listen") != "127.0.0.1" {
		t.Fatal("SOCKS listener exposed")
	}
	for _, v := range list(config, "outbounds") {
		if text(asObject(v), "protocol") != "vless" {
			t.Fatal("direct fallback enabled")
		}
	}
	if _, err = profileClient(strings.Replace(raw, "security=reality", "security=none", 1), 10800); err == nil {
		t.Fatal("unencrypted transport accepted")
	}
}
