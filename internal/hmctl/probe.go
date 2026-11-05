package hmctl

import (
	"bytes"
	"encoding/json"
	"errors"
	"strings"
)

const probeLabel = "probe-e2e@service-account"

func addProbeAccount(c object) (string, bool, error) {
	ids := map[string]bool{}
	for _, i := range inbounds(c) {
		for _, v := range clients(i) {
			u := asObject(v)
			if label := text(u, "email"); label == probeLabel || label == "probe-e2e@local" {
				ids[text(u, "id")] = true
			}
		}
	}
	if len(ids) > 1 {
		return "", false, errors.New("inconsistent existing probe identities; no UUIDs rotated")
	}
	id := ""
	for v := range ids {
		id = v
	}
	if id == "" {
		var err error
		id, err = newUUID()
		if err != nil {
			return "", false, err
		}
	}
	changed := false
	for _, i := range inbounds(c) {
		found := false
		for _, v := range clients(i) {
			u := asObject(v)
			if text(u, "email") == "probe-e2e@local" {
				u["email"] = probeLabel
				changed = true
			}
			if text(u, "email") == probeLabel {
				found = true
			}
		}
		if text(stream(i), "security") == "reality" && !found {
			settings := asObject(i["settings"])
			if settings == nil {
				return "", false, errors.New("invalid probe listener")
			}
			settings["clients"] = append(clients(i), object{"id": id, "flow": "xtls-rprx-vision", "email": probeLabel})
			changed = true
		}
	}
	return id, changed, nil
}
func probeClient(i object, id, address string) (object, error) {
	reality := asObject(stream(i)["realitySettings"])
	key, err := publicKey(text(reality, "privateKey"))
	if err != nil {
		return nil, err
	}
	names, shorts := list(reality, "serverNames"), list(reality, "shortIds")
	if len(names) == 0 || len(shorts) == 0 {
		return nil, errors.New("incomplete probe REALITY settings")
	}
	outbound := object{"tag": "public-vpn", "protocol": "vless", "settings": object{"vnext": []any{object{
		"address": address, "port": 443, "users": []any{object{"id": id, "encryption": "none", "flow": "xtls-rprx-vision"}}}}},
		"streamSettings": object{"network": "raw", "security": "reality", "realitySettings": object{
			"serverName": names[0], "fingerprint": "chrome", "password": key, "shortId": shorts[0]}}}
	rules := []any{object{"type": "field", "inboundTag": []any{"probe"}, "outboundTag": "public-vpn"}}
	outbounds := []any{object{"tag": "blocked", "protocol": "blackhole"}, outbound}
	result := object{"log": object{"loglevel": "warning"}, "inbounds": []any{object{"tag": "probe", "listen": "127.0.0.1", "port": 10800, "protocol": "socks", "settings": object{"auth": "noauth", "udp": false}}}}
	if strings.HasSuffix(address, ".svc.cluster.local") {
		result["dns"] = object{"servers": []any{"10.43.0.10"}, "queryStrategy": "UseIPv4"}
		asObject(outbound["streamSettings"])["sockopt"] = object{"domainStrategy": "ForceIPv4"}
		rules = append([]any{object{"type": "field", "ip": []any{"10.43.0.10"}, "port": "53", "outboundTag": "cluster-dns"}}, rules...)
		outbounds = append(outbounds, object{"tag": "cluster-dns", "protocol": "freedom"})
	}
	result["outbounds"] = outbounds
	result["routing"] = object{"rules": rules}
	return result, nil
}
func syncProbeCredentials(d *secretDocument) (bool, error) {
	entries := map[string]object{}
	changed := false
	for _, zone := range sortedKeys(d.configs) {
		c := d.configs[zone]
		id, modified, err := addProbeAccount(c)
		if err != nil {
			return false, err
		}
		changed = changed || modified
		var chosen object
		for _, i := range publicInbounds(c) {
			if text(stream(i), "security") == "reality" {
				chosen = i
				break
			}
		}
		if chosen == nil {
			return false, errors.New("gateway has no public REALITY listener")
		}
		entries[zone], err = probeClient(chosen, id, "vpn-entry-"+zone+".hm.svc.cluster.local")
		if err != nil {
			return false, err
		}
	}
	observers := 0
	for _, name := range sortedKeys(d.original) {
		if !strings.HasSuffix(name, ".probe.json") {
			continue
		}
		observers++
		old, err := decodeJSON(d.original[name])
		if err != nil {
			return false, err
		}
		zone := ""
		for _, v := range list(old, "outbounds") {
			o := asObject(v)
			if text(o, "tag") != "public-vpn" {
				continue
			}
			next := list(asObject(o["settings"]), "vnext")
			if len(next) != 1 {
				continue
			}
			address := text(asObject(next[0]), "address")
			if strings.HasPrefix(address, "vpn-entry-") && strings.HasSuffix(address, ".hm.svc.cluster.local") {
				zone = strings.TrimSuffix(strings.TrimPrefix(address, "vpn-entry-"), ".hm.svc.cluster.local")
			}
		}
		updated, ok := entries[zone]
		if !ok {
			return false, errors.New("probe must reference a configured public entry discovery service")
		}
		before, _ := json.Marshal(old)
		after, _ := json.Marshal(updated)
		if !bytes.Equal(before, after) {
			if err = d.setJSON(name, updated); err != nil {
				return false, err
			}
			changed = true
		}
	}
	if observers == 0 {
		return false, errors.New("no probe observers configured in encrypted source")
	}
	return changed, nil
}
