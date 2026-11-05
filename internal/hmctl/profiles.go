package hmctl

import (
	"crypto/ecdh"
	"encoding/base64"
	"errors"
	"fmt"
	"net"
	"net/netip"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strings"

	"golang.org/x/net/idna"
)

func publicKey(private string) (string, error) {
	raw, err := base64.RawURLEncoding.DecodeString(strings.TrimRight(private, "="))
	if err != nil {
		return "", errors.New("invalid REALITY key")
	}
	defer clear(raw)
	key, err := ecdh.X25519().NewPrivateKey(raw)
	if err != nil {
		return "", errors.New("invalid REALITY key")
	}
	return base64.RawURLEncoding.EncodeToString(key.PublicKey().Bytes()), nil
}

var dnsLabel = regexp.MustCompile(`^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$`)

func normalizeEndpoint(host string) (string, error) {
	if ip, err := netip.ParseAddr(host); err == nil {
		return ip.String(), nil
	}
	domain, err := idna.Lookup.ToASCII(strings.ToLower(strings.TrimSuffix(host, ".")))
	if err != nil || domain == "" || len(domain) > 253 {
		return "", errors.New("invalid endpoint IP or DNS name")
	}
	for _, label := range strings.Split(domain, ".") {
		if !dnsLabel.MatchString(label) {
			return "", errors.New("invalid endpoint IP or DNS name")
		}
	}
	return domain, nil
}
func vlessURL(id, host string, q url.Values, name string) string {
	u := url.URL{Scheme: "vless", User: url.User(id), Host: net.JoinHostPort(host, "443"), RawQuery: q.Encode(), Fragment: name}
	return u.String()
}
func profilesFor(config object, label string, hosts []string, httpsHost string) (map[string]string, error) {
	out := map[string]string{}
	for _, host := range hosts {
		if _, err := normalizeEndpoint(host); err != nil {
			return nil, err
		}
	}
	httpsHost, err := normalizeEndpoint(httpsHost)
	if err != nil {
		return nil, err
	}
	for _, i := range publicInbounds(config) {
		var selected object
		for _, v := range clients(i) {
			c := asObject(v)
			if text(c, "email") == label {
				if selected != nil && text(selected, "id") != text(c, "id") {
					return nil, errors.New("ambiguous client label on public listener")
				}
				selected = c
			}
		}
		if selected == nil {
			continue
		}
		s := stream(i)
		if text(s, "security") == "reality" {
			reality := asObject(s["realitySettings"])
			key, err := publicKey(text(reality, "privateKey"))
			if err != nil {
				return nil, err
			}
			names, shorts := list(reality, "serverNames"), list(reality, "shortIds")
			if len(names) == 0 || len(shorts) == 0 {
				return nil, errors.New("incomplete REALITY settings")
			}
			name, ok1 := names[0].(string)
			short, ok2 := shorts[0].(string)
			if !ok1 || !ok2 {
				return nil, errors.New("invalid REALITY settings")
			}
			for _, host := range hosts {
				family := "dns"
				if address, err := netip.ParseAddr(host); err == nil {
					family = "ipv4"
					if address.Is6() {
						family = "ipv6"
					}
				}
				for _, fingerprint := range []string{"chrome", "firefox", "safari"} {
					title := "hm-reality-" + family + "-" + fingerprint
					if _, exists := out[title]; exists {
						return nil, errors.New("multiple endpoints of one address family; select one --endpoint")
					}
					q := url.Values{"encryption": {"none"}, "security": {"reality"}, "sni": {name}, "fp": {fingerprint}, "pbk": {key}, "sid": {short}, "type": {"tcp"}, "flow": {text(selected, "flow")}}
					out[title] = vlessURL(text(selected, "id"), host, q, title)
				}
			}
		} else if text(s, "network") == "xhttp" {
			title := "hm-https-backup"
			settings := asObject(s["xhttpSettings"])
			q := url.Values{"encryption": {"none"}, "security": {"tls"}, "sni": {httpsHost}, "fp": {"chrome"}, "alpn": {"h2"}, "type": {"xhttp"}, "host": {httpsHost}, "path": {text(settings, "path")}, "mode": {"packet-up"}}
			out[title] = vlessURL(text(selected, "id"), httpsHost, q, title)
		}
	}
	if len(out) == 0 {
		return nil, errors.New("no public client profiles match this label")
	}
	return out, nil
}
func (r *repository) endpoints(cluster, zone, override string) ([]string, error) {
	if override != "" {
		host, err := normalizeEndpoint(override)
		if err != nil {
			return nil, err
		}
		return []string{host}, nil
	}
	var inventory object
	if err := r.readYAML("cluster/clusters/"+cluster+"/hosts.yml", &inventory); err != nil {
		return nil, err
	}
	machines := asObject(inventory["vm_node_settings"])
	var hosts []string
	count := 0
	for address, v := range machines {
		machine := asObject(v)
		if text(machine, "zone") != zone {
			continue
		}
		if entry, ok := machine["gateway_entry"].(bool); ok && !entry {
			continue
		}
		count++
		hosts = append(hosts, address)
		if ipv6 := text(machine, "ipv6"); ipv6 != "" {
			hosts = append(hosts, ipv6)
		}
	}
	if count != 1 {
		return nil, errors.New("zone has multiple or no public machines; specify --endpoint")
	}
	for i, host := range hosts {
		parsed, err := normalizeEndpoint(host)
		if err != nil {
			return nil, err
		}
		hosts[i] = parsed
	}
	return hosts, nil
}
func inside(parent, path string) bool {
	rel, err := filepath.Rel(parent, path)
	return err == nil && rel != ".." && !strings.HasPrefix(rel, ".."+string(filepath.Separator))
}
func privateDirectory(repo, path string, create bool) (string, *os.Root, error) {
	canonical, canonicalErr := filepath.EvalSymlinks(repo)
	if canonicalErr != nil {
		return "", nil, errors.New("cannot resolve repository path")
	}
	repo = canonical
	absolute, err := filepath.Abs(path)
	if err != nil {
		return "", nil, errors.New("invalid output directory")
	}
	// Resolve existing ancestors before mkdir to reject repository paths reached through symlinks.
	ancestor := absolute
	for {
		if _, err = os.Lstat(ancestor); err == nil {
			break
		}
		if !os.IsNotExist(err) || filepath.Dir(ancestor) == ancestor {
			return "", nil, errors.New("invalid output path")
		}
		ancestor = filepath.Dir(ancestor)
	}
	resolved, err := filepath.EvalSymlinks(ancestor)
	if err != nil {
		return "", nil, errors.New("cannot resolve output directory")
	}
	tail, _ := filepath.Rel(ancestor, absolute)
	absolute = filepath.Join(resolved, tail)
	if inside(repo, absolute) {
		return "", nil, errors.New("profile exports must be outside the repository")
	}
	if create {
		if err = os.MkdirAll(absolute, 0700); err != nil {
			return "", nil, errors.New("cannot create private output directory")
		}
	} else if _, err = os.Stat(absolute); os.IsNotExist(err) {
		return absolute, nil, nil
	}
	absolute, err = filepath.EvalSymlinks(absolute)
	if err != nil {
		return "", nil, errors.New("cannot resolve output directory")
	}
	if inside(repo, absolute) {
		return "", nil, errors.New("profile exports must be outside the repository")
	}
	info, err := os.Stat(absolute)
	if err != nil || info.Mode().Perm()&0077 != 0 || !ownedByUser(info) {
		return "", nil, errors.New("output directory must be owned by you with permissions 0700")
	}
	root, err := os.OpenRoot(absolute)
	if err != nil {
		return "", nil, errors.New("cannot open private output directory")
	}
	opened, err := root.Stat(".")
	if err != nil || !os.SameFile(info, opened) {
		root.Close()
		return "", nil, errors.New("output directory changed while opening")
	}
	return absolute, root, nil
}
func exportProfiles(repo, path string, profiles map[string]string) ([]string, error) {
	absolute, root, err := privateDirectory(repo, path, true)
	if err != nil {
		return nil, err
	}
	defer root.Close()
	names := sortedKeys(profiles)
	for _, name := range names {
		if _, err = root.Lstat(name + ".txt"); !os.IsNotExist(err) {
			return nil, errors.New("refusing to overwrite an existing profile or symlink; use a new output directory")
		}
	}
	var made []string
	rollback := func() {
		for _, name := range made {
			_ = root.Remove(name)
		}
	}
	for _, name := range names {
		filename := name + ".txt"
		f, err := root.OpenFile(filename, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
		if err != nil {
			rollback()
			return nil, errors.New("cannot create private profile")
		}
		made = append(made, filename)
		_, err = fmt.Fprintln(f, profiles[name])
		if err == nil {
			err = f.Sync()
		}
		closeErr := f.Close()
		if err == nil {
			err = closeErr
		}
		if err != nil {
			rollback()
			return nil, errors.New("cannot write private profile")
		}
	}
	paths := make([]string, 0, len(made))
	for _, name := range made {
		paths = append(paths, filepath.Join(absolute, name))
	}
	return paths, nil
}

func validateExport(repo, path string, profiles map[string]string) ([]string, error) {
	absolute, root, err := privateDirectory(repo, path, false)
	if err != nil {
		return nil, err
	}
	if root != nil {
		defer root.Close()
	}
	var files []string
	for _, name := range sortedKeys(profiles) {
		if root != nil {
			if _, err = root.Lstat(name + ".txt"); !os.IsNotExist(err) {
				return nil, errors.New("refusing to overwrite existing profile or symlink")
			}
		}
		files = append(files, filepath.Join(absolute, name+".txt"))
	}
	return files, nil
}
