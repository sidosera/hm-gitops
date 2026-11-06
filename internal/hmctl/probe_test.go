package hmctl

import (
	"encoding/base64"
	"encoding/json"
	"os"
	"path/filepath"
	"reflect"
	"testing"

	"go.yaml.in/yaml/v3"
)

func TestProbeConsumesOrdinaryAccountWithoutChangingGateway(t *testing.T) {
	const id = "ed933c4c-178f-4765-a12b-10ac8e51fd73"
	gateway := object{"inbounds": []any{fixtureInbound("reality-legacy", "raw", "person@example.org", id), fixtureInbound("reality", "raw", "private-relay", "private-id")}}
	initial := clone(gateway)
	observer, err := probeClient(inbounds(gateway)[0], "old-probe-id", "vpn-entry-a.hm.svc.cluster.local")
	if err != nil {
		t.Fatal(err)
	}
	observer["custom_field"] = "preserve"
	raw, err := json.Marshal(observer)
	if err != nil {
		t.Fatal(err)
	}
	d := &secretDocument{configs: map[string]object{"a": gateway, "unrelated-zone": {}}, original: map[string][]byte{"observer.probe.json": raw}, data: &yaml.Node{Kind: yaml.MappingNode}}
	changed, err := syncProbeCredentials(d, id)
	if err != nil || !changed {
		t.Fatalf("sync failed: %v", err)
	}
	if !reflect.DeepEqual(gateway, initial) {
		t.Fatal("probe changed gateway accounts or private relay")
	}
	encoded := mapping(d.data, "observer.probe.json").Value
	updatedRaw, err := base64.StdEncoding.DecodeString(encoded)
	if err != nil {
		t.Fatal(err)
	}
	updated, err := decodeJSON(updatedRaw)
	if err != nil {
		t.Fatal(err)
	}
	if updated["custom_field"] != "preserve" {
		t.Fatal("unknown observer fields lost")
	}
	d.original["observer.probe.json"] = updatedRaw
	if changed, err = syncProbeCredentials(d, id); err != nil || changed {
		t.Fatal("repeat changed configuration")
	}
	snapshot := clone(gateway)
	if _, err = syncProbeCredentials(d, "c4bcae6b-1c8c-4363-91d8-dc997f99c395"); err == nil {
		t.Fatal("probe granted access to unknown client")
	}
	if !reflect.DeepEqual(gateway, snapshot) {
		t.Fatal("failed sync mutated gateway")
	}
}

func TestClientIDFileRejectsPublicOrMalformedCredentials(t *testing.T) {
	path := filepath.Join(t.TempDir(), "client.uuid")
	const id = "ed933c4c-178f-4765-a12b-10ac8e51fd73"
	if err := os.WriteFile(path, []byte(id+"\n"), 0600); err != nil {
		t.Fatal(err)
	}
	if got, err := readClientID(path); err != nil || got != id {
		t.Fatal("valid private identity rejected")
	}
	if err := os.Chmod(path, 0644); err != nil {
		t.Fatal(err)
	}
	if _, err := readClientID(path); err == nil {
		t.Fatal("public credential file accepted")
	}
	if err := os.Chmod(path, 0600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte("not-a-uuid"), 0600); err != nil {
		t.Fatal(err)
	}
	if _, err := readClientID(path); err == nil {
		t.Fatal("malformed UUID accepted")
	}
}

func TestServiceAccountLabelsUseOrdinaryAccountLifecycle(t *testing.T) {
	c := map[string]object{"a": {"inbounds": []any{fixtureInbound("gateway", "raw", "existing", "old")}}}
	if changed, err := addClient(c, "probe@service-account", []string{"a"}); err != nil || !changed {
		t.Fatalf("conventional label rejected: %v", err)
	}
	id := text(asObject(clients(inbounds(c["a"])[0])[1]), "id")
	if changed, err := addClient(c, "probe@service-account", []string{"a"}); err != nil || changed {
		t.Fatal("ordinary service account not idempotent")
	}
	if got := text(asObject(clients(inbounds(c["a"])[0])[1]), "id"); got != id {
		t.Fatal("UUID rotated")
	}
	if changed, err := removeClient(c, "probe@service-account", []string{"a"}); err != nil || !changed {
		t.Fatal("ordinary service account cannot be revoked")
	}
}
