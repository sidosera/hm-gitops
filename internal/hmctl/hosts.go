package hmctl

import (
	"errors"
	"fmt"
	"net/netip"
	"strings"

	"golang.org/x/crypto/ssh"
)

func publicAddress(ip netip.Addr) bool {
	if !ip.IsGlobalUnicast() || ip.IsPrivate() || ip.IsLoopback() || ip.IsLinkLocalUnicast() {
		return false
	}
	for _, block := range []string{"0.0.0.0/8", "100.64.0.0/10", "192.0.0.0/24", "192.0.2.0/24", "198.18.0.0/15", "198.51.100.0/24", "203.0.113.0/24", "240.0.0.0/4", "2001:db8::/32"} {
		if netip.MustParsePrefix(block).Contains(ip) {
			return false
		}
	}
	return true
}
func replaceHost(inventory object, known, slot, address, hostKey, ipv6 string) (object, string, error) {
	ip, err := netip.ParseAddr(address)
	if err != nil || !ip.Is4() || !publicAddress(ip) {
		return nil, "", errors.New("new server requires a public IPv4 address")
	}
	address = ip.String()
	machines := asObject(inventory["vm_node_settings"])
	old := ""
	count := 0
	for host, v := range machines {
		if text(asObject(v), "host") == slot {
			old = host
			count++
		}
	}
	if count != 1 {
		return nil, "", errors.New("select exactly one existing logical host slot")
	}
	if _, exists := machines[address]; exists {
		return nil, "", errors.New("new address must differ from existing machine addresses")
	}
	parsed, _, _, rest, err := ssh.ParseAuthorizedKey([]byte(hostKey))
	if err != nil || len(rest) != 0 || parsed.Type() != ssh.KeyAlgoED25519 {
		return nil, "", errors.New("provide a valid ed25519 public host key verified out of band")
	}
	key := strings.TrimSpace(string(ssh.MarshalAuthorizedKey(parsed)))
	if ipv6 != "" {
		v, err := netip.ParseAddr(ipv6)
		if err != nil || !v.Is6() || !publicAddress(v) {
			return nil, "", errors.New("new IPv6 must be public")
		}
		ipv6 = v.String()
	}
	for _, line := range strings.Split(known, "\n") {
		fields := strings.Fields(line)
		if len(fields) < 3 || strings.HasPrefix(strings.TrimSpace(line), "#") {
			continue
		}
		for _, host := range strings.Split(fields[0], ",") {
			if host == address && strings.Join(fields[1:3], " ") != key {
				return nil, "", errors.New("new address has a conflicting pinned SSH key; review trust explicitly")
			}
		}
	}
	result := clone(inventory)
	if result == nil {
		return nil, "", errors.New("invalid inventory")
	}
	resultMachines := asObject(result["vm_node_settings"])
	machine := asObject(resultMachines[old])
	delete(resultMachines, old)
	resultMachines[address] = machine
	machine["name"] = address
	delete(machine, "ipv6")
	if ipv6 != "" {
		machine["ipv6"] = ipv6
	}
	for _, field := range []string{"vm_hosts", "vm_agent_hosts"} {
		hosts := list(result, field)
		for i, v := range hosts {
			if v == old {
				hosts[i] = address
			}
		}
		result[field] = hosts
	}
	var lines []string
	for _, line := range strings.Split(strings.TrimSuffix(known, "\n"), "\n") {
		fields := strings.Fields(line)
		if len(fields) > 0 && !strings.HasPrefix(strings.TrimSpace(line), "#") {
			var aliases []string
			for _, host := range strings.Split(fields[0], ",") {
				if host != old && host != address {
					aliases = append(aliases, host)
				}
			}
			if len(aliases) == 0 {
				continue
			}
			if len(aliases) != len(strings.Split(fields[0], ",")) {
				line = strings.Join(aliases, ",") + " " + strings.Join(fields[1:], " ")
			}
		}
		lines = append(lines, line)
	}
	lines = append(lines, address+" "+key)
	return result, strings.Join(lines, "\n") + "\n", nil
}
func validateInventory(inventory, profile object) error {
	machines := asObject(inventory["vm_node_settings"])
	network := asObject(asObject(profile["vm_cluster_profile"])["network"])
	if text(network, "interface") != "hm0" || fmt.Sprint(network["port"]) != "51822" {
		return errors.New("invalid private transport profile")
	}
	v4, err := netip.ParsePrefix(text(network, "cidr"))
	if err != nil || !v4.Addr().Is4() || !v4.Addr().IsPrivate() {
		return errors.New("invalid private IPv4 network")
	}
	v6, err := netip.ParsePrefix(text(network, "cidr_ipv6"))
	if err != nil || !v6.Addr().IsPrivate() || !v6.Addr().Is6() {
		return errors.New("invalid private IPv6 network")
	}
	seen, privates, slots := map[string]bool{}, map[string]bool{}, map[string]bool{}
	for _, field := range []string{"vm_hosts", "vm_agent_hosts"} {
		for _, value := range list(inventory, field) {
			host, ok := value.(string)
			if !ok || seen[host] || machines[host] == nil {
				return errors.New("host lists must match unique machine inventory")
			}
			seen[host] = true
			address, err := netip.ParseAddr(host)
			if err != nil || !address.Is4() {
				return errors.New("inventory requires IPv4 host addresses")
			}
			machine := asObject(machines[host])
			role := "agent"
			if field == "vm_hosts" {
				role = "control_plane"
			}
			if text(machine, "role") != role {
				return errors.New("machine role differs from host group")
			}
			slot := text(machine, "host")
			if slot == "" || slots[slot] {
				return errors.New("duplicate logical host slot")
			}
			slots[slot] = true
			if !dnsLabel.MatchString(text(machine, "zone")) {
				return errors.New("zone must be a DNS label")
			}
			for key, subnet := range map[string]netip.Prefix{"private_ipv4": v4, "private_ipv6": v6} {
				ip, err := netip.ParseAddr(text(machine, key))
				if err != nil || !subnet.Contains(ip) || ip == subnet.Masked().Addr() || privates[ip.String()] || (key == "private_ipv4" && !subnet.Contains(ip.Next())) {
					return errors.New("invalid or duplicate private host identity")
				}
				privates[ip.String()] = true
			}
		}
	}
	if len(seen) != len(machines) || len(list(inventory, "vm_hosts")) == 0 {
		return errors.New("host lists and machines differ")
	}
	return nil
}
