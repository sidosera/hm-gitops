package hmctl

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/netip"
	"net/url"
	"os"
	"os/exec"
	"strings"
	"time"
)

type CheckResult struct {
	Name   string `json:"name"`
	Passed bool   `json:"passed"`
	Status int    `json:"http_status,omitempty"`
}

func profileClient(raw string, port int) (object, error) {
	u, err := url.Parse(strings.TrimSpace(raw))
	if err != nil || u.Scheme != "vless" || u.User == nil || u.User.Username() == "" {
		return nil, errors.New("expected a VLESS profile")
	}
	if _, err = normalizeEndpoint(u.Hostname()); err != nil {
		return nil, err
	}
	destinationPort := 443
	if u.Port() != "" {
		if _, err = fmt.Sscanf(u.Port(), "%d", &destinationPort); err != nil || destinationPort < 1 || destinationPort > 65535 {
			return nil, errors.New("invalid profile port")
		}
	}
	q := u.Query()
	user := object{"id": u.User.Username(), "encryption": "none"}
	if q.Get("flow") != "" {
		user["flow"] = q.Get("flow")
	}
	security := q.Get("security")
	network := "raw"
	if q.Get("type") == "xhttp" {
		network = "xhttp"
	}
	s := object{"network": network, "security": security}
	switch security {
	case "reality":
		if q.Get("sni") == "" || q.Get("pbk") == "" {
			return nil, errors.New("incomplete REALITY profile")
		}
		fingerprint := q.Get("fp")
		if fingerprint == "" {
			fingerprint = "chrome"
		}
		s["realitySettings"] = object{"serverName": q.Get("sni"), "fingerprint": fingerprint, "password": q.Get("pbk"), "shortId": q.Get("sid")}
	case "tls":
		if q.Get("sni") == "" {
			return nil, errors.New("incomplete TLS profile")
		}
		alpn := q.Get("alpn")
		if alpn == "" {
			alpn = "h2"
		}
		s["tlsSettings"] = object{"serverName": q.Get("sni"), "fingerprint": "chrome", "alpn": strings.Split(alpn, ",")}
	default:
		return nil, errors.New("profile must use TLS or REALITY")
	}
	if network == "xhttp" {
		host := q.Get("host")
		if host == "" {
			host = q.Get("sni")
		}
		mode := q.Get("mode")
		if mode == "" {
			mode = "packet-up"
		}
		s["xhttpSettings"] = object{"host": host, "path": q.Get("path"), "mode": mode}
	}
	return object{"log": object{"loglevel": "error"}, "inbounds": []any{object{"listen": "127.0.0.1", "port": port, "protocol": "socks", "settings": object{"auth": "noauth", "udp": false}}},
		"outbounds": []any{object{"protocol": "vless", "settings": object{"vnext": []any{object{"address": u.Hostname(), "port": destinationPort, "users": []any{user}}}}, "streamSettings": s}}}, nil
}
func (o *options) check(r *repository, path, expected string) error {
	if expected == "" {
		var inventory object
		if err := r.readYAML("cluster/clusters/"+o.cluster+"/hosts.yml", &inventory); err != nil {
			return err
		}
		hosts := list(inventory, "vm_hosts")
		if len(hosts) == 0 {
			return errors.New("no control-plane address configured")
		}
		expected, _ = hosts[0].(string)
	}
	if _, err := netip.ParseAddr(expected); err != nil {
		return usage("--expected-exit-ip must be an IP address")
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		return errors.New("cannot read private profile")
	}
	defer clear(raw)
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return errors.New("cannot allocate local probe port")
	}
	port := listener.Addr().(*net.TCPAddr).Port
	listener.Close()
	config, err := profileClient(string(raw), port)
	if err != nil {
		return err
	}
	data, _ := json.Marshal(config)
	defer clear(data)
	ctx, cancel := context.WithCancel(r.ctx)
	defer cancel()
	process := exec.CommandContext(ctx, "xray", "run", "-config", "stdin:")
	process.Stdin = bytes.NewReader(data)
	process.Stdout = io.Discard
	process.Stderr = io.Discard
	if err = process.Start(); err != nil {
		return errors.New("install Xray in PATH to check a profile")
	}
	exited := make(chan error, 1)
	go func() { exited <- process.Wait() }()
	defer func() { cancel(); <-exited }()
	address := net.JoinHostPort("127.0.0.1", fmt.Sprint(port))
	ready := false
	for i := 0; i < 50; i++ {
		select {
		case err := <-exited:
			exited <- err
			return errors.New("Xray could not start; check installed version and profile support")
		case <-ctx.Done():
			return errors.New("profile check cancelled")
		default:
		}
		conn, err := net.DialTimeout("tcp", address, 100*time.Millisecond)
		if err == nil {
			conn.Close()
			ready = true
			break
		}
		select {
		case <-time.After(100 * time.Millisecond):
		case <-ctx.Done():
			return errors.New("profile check cancelled")
		}
	}
	if !ready {
		return errors.New("local Xray SOCKS listener did not become ready")
	}
	proxy, _ := url.Parse("socks5://" + address)
	transport := &http.Transport{Proxy: http.ProxyURL(proxy), DisableKeepAlives: true}
	defer transport.CloseIdleConnections()
	client := &http.Client{Transport: transport, Timeout: 25 * time.Second, CheckRedirect: func(_ *http.Request, _ []*http.Request) error { return http.ErrUseLastResponse }}
	tests := []struct {
		name, url string
		status    int
		exit      bool
	}{{"exit-ip", "https://api.ipify.org", 200, true}, {"https", "https://www.google.com/generate_204", 204, false}, {"download", "https://speed.cloudflare.com/__down?bytes=1000000", 200, false}}
	result := Result{Status: "profile connectivity verified"}
	passed := true
	for _, test := range tests {
		req, _ := http.NewRequestWithContext(ctx, http.MethodGet, test.url, nil)
		response, err := client.Do(req)
		item := CheckResult{Name: test.name}
		if err == nil {
			item.Status = response.StatusCode
			body, readErr := io.ReadAll(io.LimitReader(response.Body, 2<<20))
			response.Body.Close()
			item.Passed = readErr == nil && response.StatusCode == test.status
			if test.exit {
				item.Passed = item.Passed && strings.TrimSpace(string(body)) == expected
			}
			clear(body)
		}
		passed = passed && item.Passed
		result.Checks = append(result.Checks, item)
	}
	if !passed {
		result.Status = "profile connectivity check failed"
	}
	if err = o.emit(result); err != nil {
		return err
	}
	if !passed {
		return errors.New("one or more connectivity checks failed")
	}
	return nil
}
