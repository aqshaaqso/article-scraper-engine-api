package security

import (
	"context"
	"fmt"
	"net"
	"net/url"
	"sort"
	"strings"

	"golang.org/x/net/idna"
)

type Resolver interface {
	LookupIP(context.Context, string, string) ([]net.IP, error)
}

type Target struct {
	URL      *url.URL
	Hostname string
	Port     string
	IPs      []net.IP
}

type Policy struct {
	AllowHTTP      bool
	AllowedDomains []string
	Resolver       Resolver
}

func (p Policy) Validate(ctx context.Context, raw string) (Target, error) {
	if raw == "" || len(raw) > 2048 {
		return Target{}, fmt.Errorf("URL kosong atau terlalu panjang")
	}
	for _, r := range raw {
		if r < 32 {
			return Target{}, fmt.Errorf("URL mengandung karakter kontrol")
		}
	}
	u, err := url.Parse(strings.TrimSpace(raw))
	if err != nil || u.Hostname() == "" {
		return Target{}, fmt.Errorf("format URL tidak valid")
	}
	scheme := strings.ToLower(u.Scheme)
	if scheme != "http" && scheme != "https" {
		return Target{}, fmt.Errorf("hanya URL HTTP/HTTPS yang didukung")
	}
	if scheme == "http" && !p.AllowHTTP {
		return Target{}, fmt.Errorf("HTTP tidak diizinkan; gunakan HTTPS")
	}
	if u.User != nil {
		return Target{}, fmt.Errorf("credential tidak boleh ditanam di dalam URL")
	}
	host, err := idna.Lookup.ToASCII(strings.TrimSuffix(u.Hostname(), "."))
	if err != nil {
		return Target{}, fmt.Errorf("hostname tidak valid")
	}
	host = strings.ToLower(host)
	if !p.allowed(host) {
		return Target{}, fmt.Errorf("domain tidak terdaftar pada ALLOWED_DOMAINS")
	}
	expected := "443"
	if scheme == "http" {
		expected = "80"
	}
	port := u.Port()
	if port == "" {
		port = expected
	}
	if port != expected {
		return Target{}, fmt.Errorf("hanya port standar 443/80 yang diizinkan")
	}
	resolver := p.Resolver
	if resolver == nil {
		resolver = net.DefaultResolver
	}
	ips, err := resolver.LookupIP(ctx, "ip", host)
	if err != nil || len(ips) == 0 {
		return Target{}, fmt.Errorf("hostname tidak dapat di-resolve")
	}
	for _, ip := range ips {
		if !isPublic(ip) {
			return Target{}, fmt.Errorf("URL mengarah ke jaringan lokal, privat, atau khusus")
		}
	}
	sort.Slice(ips, func(i, j int) bool { return ips[i].String() < ips[j].String() })
	u.Scheme = scheme
	u.Host = host
	if strings.Contains(host, ":") {
		u.Host = "[" + host + "]"
	}
	if u.Path == "" {
		u.Path = "/"
	}
	u.Fragment = ""
	return Target{URL: u, Hostname: host, Port: port, IPs: ips}, nil
}

var specialNetworks = []*net.IPNet{
	mustCIDR("0.0.0.0/8"), mustCIDR("100.64.0.0/10"), mustCIDR("192.0.0.0/24"),
	mustCIDR("192.0.2.0/24"), mustCIDR("198.18.0.0/15"), mustCIDR("198.51.100.0/24"),
	mustCIDR("203.0.113.0/24"), mustCIDR("224.0.0.0/4"), mustCIDR("240.0.0.0/4"),
	mustCIDR("2001:db8::/32"), mustCIDR("2001:10::/28"), mustCIDR("ff00::/8"),
}

func isPublic(ip net.IP) bool {
	if !ip.IsGlobalUnicast() || ip.IsPrivate() || ip.IsLoopback() || ip.IsLinkLocalUnicast() || ip.IsUnspecified() {
		return false
	}
	for _, network := range specialNetworks {
		if network.Contains(ip) {
			return false
		}
	}
	return true
}

func mustCIDR(raw string) *net.IPNet {
	_, network, err := net.ParseCIDR(raw)
	if err != nil {
		panic(err)
	}
	return network
}

func (p Policy) allowed(host string) bool {
	if len(p.AllowedDomains) == 0 {
		return true
	}
	for _, d := range p.AllowedDomains {
		d = strings.ToLower(strings.TrimSuffix(d, "."))
		if host == d || strings.HasSuffix(host, "."+d) {
			return true
		}
	}
	return false
}
