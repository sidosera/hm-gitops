package hmctl

import (
	"bytes"
	"encoding/json"
	"errors"
	"github.com/google/uuid"
	"strings"
)

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

// syncProbeCredentials updates only existing observers using an authorized UUID.
// Gateway account lists and labels are read-only inputs.
func syncProbeCredentials(d *secretDocument, id string) (bool, error) {
	if _, err := uuid.Parse(id); err != nil {
		return false, errors.New("invalid supplied client UUID")
	}
	updates := map[string]object{}
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
		for _, value := range list(old, "outbounds") {
			out := asObject(value)
			if text(out, "tag") != "public-vpn" {
				continue
			}
			next := list(asObject(out["settings"]), "vnext")
			if len(next) != 1 {
				continue
			}
			address := text(asObject(next[0]), "address")
			if strings.HasPrefix(address, "vpn-entry-") && strings.HasSuffix(address, ".hm.svc.cluster.local") {
				zone = strings.TrimSuffix(strings.TrimPrefix(address, "vpn-entry-"), ".hm.svc.cluster.local")
			}
		}
		c, ok := d.configs[zone]
		if !ok {
			return false, errors.New("probe must reference a configured public entry discovery service")
		}
		var chosen object
		for _, inbound := range publicInbounds(c) {
			if text(stream(inbound), "security") == "reality" {
				chosen = inbound
				break
			}
		}
		if chosen == nil {
			return false, errors.New("gateway has no public REALITY listener")
		}
		allowed := false
		for _, value := range clients(chosen) {
			if text(asObject(value), "id") == id {
				allowed = true
			}
		}
		if !allowed {
			return false, errors.New("supplied client is not authorized on the public gateway; create access through gateway first")
		}
		generated, err := probeClient(chosen, id, "vpn-entry-"+zone+".hm.svc.cluster.local")
		if err != nil {
			return false, err
		}
		// Retain observer routing, SOCKS settings and unknown fields. Only the
		// selected VPN outbound's credentials and server transport are refreshed.
		updated := clone(old)
		for index, value := range list(updated, "outbounds") {
			if text(asObject(value), "tag") == "public-vpn" {
				list(updated, "outbounds")[index] = list(generated, "outbounds")[1]
			}
		}
		before, _ := json.Marshal(old)
		after, _ := json.Marshal(updated)
		if !bytes.Equal(before, after) {
			updates[name] = updated
		}
	}
	if observers == 0 {
		return false, errors.New("no probe observers configured in encrypted source")
	}
	for _, name := range sortedKeys(updates) {
		if err := d.setJSON(name, updates[name]); err != nil {
			return false, err
		}
	}
	return len(updates) > 0, nil
}
