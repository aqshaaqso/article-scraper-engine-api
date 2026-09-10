package fetcher

import (
	"context"
	"io"
	"net"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/aqshaaqso/article-scraper-engine-api/internal/security"
)

type fakeResolver map[string][]net.IP

func (r fakeResolver) LookupIP(_ context.Context, _ string, host string) ([]net.IP, error) {
	return r[host], nil
}

type noWait struct{}

func (noWait) ReserveDomain(context.Context, string, time.Duration) (time.Duration, error) {
	return 0, nil
}

func TestRedirectTargetValidatedBeforeRequest(t *testing.T) {
	calls := 0
	f := Fetcher{Policy: security.Policy{Resolver: fakeResolver{"public.test": {net.ParseIP("8.8.8.8")}, "private.test": {net.ParseIP("127.0.0.1")}}}, Limiter: noWait{}, MaxBytes: 1024, MaxRedirects: 3, RespectRobots: false, RequestOnce: func(_ context.Context, _ security.Target, _ int64) (*http.Response, error) {
		calls++
		return &http.Response{StatusCode: 302, Header: http.Header{"Location": []string{"https://private.test/secret"}}, Body: io.NopCloser(strings.NewReader(""))}, nil
	}}
	_, err := f.Fetch(context.Background(), "https://public.test/start")
	if err == nil {
		t.Fatal("expected unsafe redirect")
	}
	if calls != 1 {
		t.Fatalf("private target was requested: %d calls", calls)
	}
}
func TestRejectsOversizedBody(t *testing.T) {
	f := Fetcher{Policy: security.Policy{Resolver: fakeResolver{"public.test": {net.ParseIP("8.8.8.8")}}}, Limiter: noWait{}, MaxBytes: 4, MaxRedirects: 1, RespectRobots: false, RequestOnce: func(_ context.Context, _ security.Target, _ int64) (*http.Response, error) {
		return &http.Response{StatusCode: 200, Header: http.Header{"Content-Type": []string{"text/html"}}, Body: io.NopCloser(strings.NewReader("12345"))}, nil
	}}
	if _, err := f.Fetch(context.Background(), "https://public.test/a"); err == nil {
		t.Fatal("expected size error")
	}
}

func TestRobotsPolicyIsAppliedBeforeArticle(t *testing.T) {
	f := Fetcher{
		Policy:  security.Policy{Resolver: fakeResolver{"public.test": {net.ParseIP("8.8.8.8")}}},
		Limiter: noWait{}, MaxBytes: 1024, MaxRedirects: 1, RespectRobots: true,
		UserAgent: "ArticleScraperLab", DomainDelay: time.Millisecond,
		RequestOnce: func(_ context.Context, target security.Target, _ int64) (*http.Response, error) {
			body := "<html>article</html>"
			contentType := "text/html"
			if target.URL.Path == "/robots.txt" {
				body = "User-agent: *\nDisallow: /blocked"
				contentType = "text/plain"
			}
			return &http.Response{StatusCode: 200, Header: http.Header{"Content-Type": []string{contentType}}, Body: io.NopCloser(strings.NewReader(body))}, nil
		},
	}
	if _, err := f.Fetch(context.Background(), "https://public.test/blocked"); err == nil {
		t.Fatal("expected robots denial")
	}
}
