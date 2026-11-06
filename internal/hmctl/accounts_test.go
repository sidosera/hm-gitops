package hmctl

import (
	"encoding/json"
	"reflect"
	"testing"
)

func fixtureInbound(tag, network, label, id string) object {
	security := "reality"
	if network == "xhttp" {
		security = "none"
	}
	return object{"tag": tag, "protocol": "vless", "settings": object{"clients": []any{object{"id": id, "email": label, "flow": "xtls-rprx-vision", "extra": "keep"}}},
		"streamSettings": object{"network": network, "security": security, "realitySettings": object{"privateKey": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", "serverNames": []any{"example.org"}, "shortIds": []any{"0011223344556677"}}, "xhttpSettings": object{"path": "/fixture"}}}
}
func TestClientIdentityLifecycle(t *testing.T) {
	configs := map[string]object{"a": {"inbounds": []any{fixtureInbound("gateway", "raw", "existing", "old-id")}}, "b": {"inbounds": []any{fixtureInbound("reality-legacy", "raw", "other", "other-id"), fixtureInbound("reality", "raw", "relay@service-account", "relay-id"), fixtureInbound("https-backup", "xhttp", "old-backup", "backup-id")}}}
	before, _ := json.Marshal(configs)
	changed, err := addClient(configs, "alice", []string{"a", "b"})
	if err != nil || !changed {
		t.Fatalf("add failed: %v", err)
	}
	ids := map[string]bool{}
	for _, c := range configs {
		for _, i := range publicInbounds(c) {
			for _, v := range clients(i) {
				u := asObject(v)
				if text(u, "email") == "alice" {
					ids[text(u, "id")] = true
					if text(stream(i), "network") == "xhttp" && u["flow"] != nil {
						t.Fatal("XHTTP inherited REALITY flow")
					}
				}
			}
		}
	}
	if len(ids) != 1 {
		t.Fatal("client UUID differs across public gateways/transports")
	}
	for id := range ids {
		if len(id) != 36 || id[14] != '4' {
			t.Fatal("not a v4 UUID")
		}
	}
	if len(clients(inbounds(configs["b"])[1])) != 1 {
		t.Fatal("client added to private relay listener")
	}
	first, _ := json.Marshal(configs)
	changed, err = addClient(configs, "alice", []string{"b", "a"})
	again, _ := json.Marshal(configs)
	if err != nil || changed || string(first) != string(again) {
		t.Fatal("repeat changed credentials")
	}
	changed, err = removeClient(configs, "alice", []string{"a", "b"})
	after, _ := json.Marshal(configs)
	if err != nil || !changed || string(before) != string(after) {
		t.Fatal("revocation changed another account")
	}
}
func TestClientValidationAndAmbiguity(t *testing.T) {
	configs := map[string]object{"a": {"inbounds": []any{fixtureInbound("gateway", "raw", "alice", "one")}}, "b": {"inbounds": []any{fixtureInbound("gateway", "raw", "alice", "two")}}}
	before, _ := json.Marshal(configs)
	for _, tc := range []struct {
		label string
		zones []string
	}{{"alice", []string{"a"}}, {"bad name", []string{"a"}}, {"new", []string{"missing"}}} {
		if _, err := addClient(configs, tc.label, tc.zones); err == nil {
			t.Fatal("invalid request accepted")
		}
	}
	after, _ := json.Marshal(configs)
	if string(before) != string(after) {
		t.Fatal("failed validation mutated source")
	}
	if err := validateLabel("primary@local"); err != nil {
		t.Fatal("legacy account label rejected")
	}
	if err := validateLabel("person@example.org"); err != nil {
		t.Fatalf("email should work: %v", err)
	}
}
func TestProbeUsesRemoteDNSAndFailsClosed(t *testing.T) {
	c, err := probeClient(fixtureInbound("gateway", "raw", "alice", "id"), "probe-id", "vpn-entry-a.hm.svc.cluster.local")
	if err != nil {
		t.Fatal(err)
	}
	outs := list(c, "outbounds")
	if text(asObject(outs[0]), "protocol") != "blackhole" {
		t.Fatal("direct fallback enabled")
	}
	vpn := asObject(outs[1])
	if !reflect.DeepEqual(asObject(stream(vpn)["sockopt"]), object{"domainStrategy": "ForceIPv4"}) {
		t.Fatal("endpoint lookup bypasses cluster DNS")
	}
	rules := list(asObject(c["routing"]), "rules")
	if text(asObject(rules[0]), "outboundTag") != "cluster-dns" || text(asObject(rules[1]), "outboundTag") != "public-vpn" {
		t.Fatal("probe routing changed")
	}
	if asObject(c["dns"]) == nil {
		t.Fatal("cluster discovery DNS missing")
	}
}
