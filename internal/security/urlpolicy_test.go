package security

import (
	"context"
	"net"
	"testing"
)

type resolver map[string][]net.IP

func (r resolver) LookupIP(_ context.Context, _ string, host string) ([]net.IP, error) {
	return r[host], nil
}

func TestPolicyBlocksPrivateAndMixedDNS(t *testing.T) {
	p := Policy{Resolver: resolver{"private.test": {net.ParseIP("127.0.0.1")}, "mixed.test": {net.ParseIP("8.8.8.8"), net.ParseIP("10.0.0.1")}, "docs.test": {net.ParseIP("192.0.2.1")}}}
	for _, raw := range []string{"https://private.test/x", "https://mixed.test/x", "https://docs.test/x"} {
		if _, err := p.Validate(context.Background(), raw); err == nil {
			t.Fatalf("expected %s to fail", raw)
		}
	}
}

func TestPolicyBlocksSensitiveLiteralRangesWithoutAllowlist(t *testing.T) {
	p := Policy{Resolver: resolver{
		"127.0.0.1":       {net.ParseIP("127.0.0.1")},
		"::1":             {net.ParseIP("::1")},
		"169.254.169.254": {net.ParseIP("169.254.169.254")},
		"10.0.0.1":        {net.ParseIP("10.0.0.1")},
		"100.64.0.1":      {net.ParseIP("100.64.0.1")},
	}}
	for _, raw := range []string{
		"https://127.0.0.1/",
		"https://[::1]/",
		"https://169.254.169.254/latest/meta-data/",
		"https://10.0.0.1/",
		"https://100.64.0.1/",
	} {
		if _, err := p.Validate(context.Background(), raw); err == nil {
			t.Fatalf("expected %s to fail", raw)
		}
	}
}

func TestPolicyNormalizesAndAllowsSubdomain(t *testing.T) {
	p := Policy{AllowedDomains: []string{"example.com"}, Resolver: resolver{"news.example.com": {net.ParseIP("8.8.8.8")}}}
	target, err := p.Validate(context.Background(), "https://news.example.com/story?q=1#x")
	if err != nil {
		t.Fatal(err)
	}
	if got := target.URL.String(); got != "https://news.example.com/story?q=1" {
		t.Fatalf("unexpected URL %s", got)
	}
}

func TestPolicyRejectsCredentialsPortAndHTTP(t *testing.T) {
	p := Policy{Resolver: resolver{"example.com": {net.ParseIP("8.8.8.8")}}}
	for _, raw := range []string{"http://example.com/x", "https://u:p@example.com/x", "https://example.com:444/x"} {
		if _, err := p.Validate(context.Background(), raw); err == nil {
			t.Fatalf("expected %s to fail", raw)
		}
	}
}

func TestPolicyNormalizesInternationalHostname(t *testing.T) {
	p := Policy{AllowedDomains: []string{"xn--bcher-kva.example"}, Resolver: resolver{"xn--bcher-kva.example": {net.ParseIP("8.8.8.8")}}}
	target, err := p.Validate(context.Background(), "https://bücher.example/story")
	if err != nil {
		t.Fatal(err)
	}
	if target.Hostname != "xn--bcher-kva.example" {
		t.Fatal(target.Hostname)
	}
}
