package xray

import (
	"bytes"
	"encoding/json"
	"errors"
	"io"
	"regexp"

	"github.com/google/uuid"
	core "github.com/sidosera/hm-gitops/internal/hmctl"
)

type object = core.Object

var asObject = core.AsObject
var list = core.List
var text = core.Text
var clone = core.Clone

func sortedKeys[V any](m map[string]V) []string { return core.SortedKeys(m) }

func decodeJSON(data []byte) (object, error) {
	var m object
	d := json.NewDecoder(bytes.NewReader(data))
	d.UseNumber()
	if err := d.Decode(&m); err != nil || m == nil {
		return nil, errors.New("invalid Xray configuration")
	}
	var extra any
	if err := d.Decode(&extra); err != io.EOF {
		return nil, errors.New("trailing data in Xray configuration")
	}
	for _, v := range list(m, "inbounds") {
		i := asObject(v)
		if i == nil {
			return nil, errors.New("invalid Xray inbound")
		}
		if text(i, "protocol") == "vless" {
			settings := asObject(i["settings"])
			if settings == nil {
				return nil, errors.New("invalid VLESS settings")
			}
			if _, ok := settings["clients"].([]any); !ok {
				return nil, errors.New("invalid VLESS client list")
			}
			for _, c := range list(settings, "clients") {
				if asObject(c) == nil || text(asObject(c), "id") == "" {
					return nil, errors.New("invalid VLESS client record")
				}
			}
		}
	}
	return m, nil
}

func inbounds(m object) []object {
	var out []object
	for _, v := range list(m, "inbounds") {
		out = append(out, asObject(v))
	}
	return out
}
func clients(i object) []any { return list(asObject(i["settings"]), "clients") }
func stream(i object) object { return asObject(i["streamSettings"]) }
func publicInbounds(m object) []object {
	tag := "gateway"
	for _, i := range inbounds(m) {
		if text(i, "tag") == "reality-legacy" {
			tag = "reality-legacy"
		}
	}
	var out []object
	for _, i := range inbounds(m) {
		if text(i, "protocol") == "vless" && (text(i, "tag") == tag || text(stream(i), "network") == "xhttp") {
			out = append(out, i)
		}
	}
	return out
}
func newUUID() (string, error) {
	id, err := uuid.NewRandom()
	if err != nil {
		return "", errors.New("cannot generate secure client identity")
	}
	return id.String(), nil
}

var labelPattern = regexp.MustCompile(`^[A-Za-z0-9][A-Za-z0-9._+@-]{0,253}$`)

func validateLabel(label string) error {
	if !labelPattern.MatchString(label) {
		return errors.New("use an account label without whitespace or control characters")
	}
	return nil
}

func addClient(configs map[string]object, label string, zones []string) (bool, error) {
	if err := validateLabel(label); err != nil {
		return false, err
	}
	for _, zone := range zones {
		if _, ok := configs[zone]; !ok {
			return false, errors.New("unknown gateway zone")
		}
		if len(publicInbounds(configs[zone])) == 0 {
			return false, errors.New("zone has no supported public gateway listener")
		}
	}
	ids := map[string]bool{}
	for _, c := range configs {
		for _, i := range inbounds(c) {
			for _, v := range clients(i) {
				u := asObject(v)
				if text(u, "email") == label {
					ids[text(u, "id")] = true
				}
			}
		}
	}
	if len(ids) > 1 {
		return false, errors.New("client label has multiple UUIDs; resolve explicitly without rotating credentials")
	}
	id := ""
	for v := range ids {
		id = v
	}
	if id == "" {
		var err error
		id, err = newUUID()
		if err != nil {
			return false, err
		}
	}
	changed := false
	for _, zone := range zones {
		for _, i := range publicInbounds(configs[zone]) {
			found := false
			for _, v := range clients(i) {
				if text(asObject(v), "email") == label {
					found = true
				}
			}
			if found {
				continue
			}
			u := object{"id": id, "email": label}
			if text(stream(i), "security") == "reality" {
				u["flow"] = "xtls-rprx-vision"
			}
			settings := asObject(i["settings"])
			settings["clients"] = append(clients(i), u)
			changed = true
		}
	}
	return changed, nil
}
