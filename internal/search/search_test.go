package search

import (
	"context"
	"net"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"github.com/aqshaaqso/article-scraper-engine-api/internal/config"
	"github.com/aqshaaqso/article-scraper-engine-api/internal/security"
)

type staticResolver struct{}

func (staticResolver) LookupIP(context.Context, string, string) ([]net.IP, error) {
	return []net.IP{net.ParseIP("8.8.8.8")}, nil
}

func TestMonthWindowsIncludesPartialAndLeapMonth(t *testing.T) {
	windows, err := monthWindows("2024-01-20", "2024-03-03")
	if err != nil {
		t.Fatal(err)
	}
	if len(windows) != 3 {
		t.Fatalf("got %d windows", len(windows))
	}
	if got := windows[0][0].Format("2006-01-02"); got != "2024-01-20" {
		t.Fatal(got)
	}
	if got := windows[1][1].Format("2006-01-02"); got != "2024-02-29" {
		t.Fatal(got)
	}
	if got := windows[2][1].Format("2006-01-02"); got != "2024-03-03" {
		t.Fatal(got)
	}
}
func TestMonthWindowsRejectsReverseRange(t *testing.T) {
	if _, err := monthWindows("2025-01-01", "2024-01-01"); err == nil {
		t.Fatal("expected error")
	}
}
func TestFingerprintStable(t *testing.T) {
	if fingerprint([]string{"a", "b"}) != fingerprint([]string{"a", "b"}) {
		t.Fatal("unstable")
	}
	if fingerprint([]string{"a", "b"}) == fingerprint([]string{"b", "a"}) {
		t.Fatal("order must matter")
	}
}
func TestWindowUsesUTCDateOnly(t *testing.T) {
	windows, _ := monthWindows("2024-02-29", "2024-02-29")
	if !windows[0][0].Equal(time.Date(2024, 2, 29, 0, 0, 0, 0, time.UTC)) {
		t.Fatal(windows)
	}
}

func TestNormalizeOptionalDateRanges(t *testing.T) {
	now := time.Date(2026, 9, 10, 1, 0, 0, 0, time.UTC)
	day, month, year := 5, 2, 2024
	cases := []struct {
		name      string
		req       Request
		wantStart string
		wantEnd   string
	}{
		{"day", Request{Day: &day, Timezone: "Asia/Jakarta"}, "2026-09-05", "2026-09-05"},
		{"month", Request{Month: &month, Timezone: "Asia/Jakarta"}, "2026-02-01", "2026-02-28"},
		{"year", Request{Year: &year, Timezone: "Asia/Jakarta"}, "2024-01-01", "2024-12-31"},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if err := normalizeDateRange(&tc.req, now); err != nil {
				t.Fatal(err)
			}
			if tc.req.StartDate == nil || *tc.req.StartDate != tc.wantStart || tc.req.EndDate == nil || *tc.req.EndDate != tc.wantEnd {
				t.Fatalf("got %v - %v", tc.req.StartDate, tc.req.EndDate)
			}
		})
	}
}

func TestNormalizeStartDateThroughToday(t *testing.T) {
	start := "2025-07-01"
	req := Request{StartDate: &start, Timezone: "Asia/Jakarta"}
	now := time.Date(2026, 9, 9, 18, 30, 0, 0, time.UTC)
	if err := normalizeDateRange(&req, now); err != nil {
		t.Fatal(err)
	}
	if *req.EndDate != "2026-09-10" {
		t.Fatalf("end=%s", *req.EndDate)
	}
}

func TestNormalizeRejectsAmbiguousFilters(t *testing.T) {
	day, month := 1, 2
	req := Request{Day: &day, Month: &month, Timezone: "Asia/Jakarta"}
	if err := normalizeDateRange(&req, time.Now()); err == nil {
		t.Fatal("expected error")
	}
}

func TestDiscoveryFiltersListingsAndDuplicates(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Query().Get("api_key") != "secret" {
			t.Error("missing key")
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"organic_results":[{"link":"https://news.example/story#fragment"},{"link":"https://news.example/story"},{"link":"https://news.example/tag/all"},{"link":"https://outside.example/story"}]}`))
	}))
	defer server.Close()
	service := Service{
		Config:      config.Config{SerpAPIKey: "secret", AllowedDomains: []string{"news.example"}},
		ProviderURL: server.URL, Client: server.Client(),
		Policy: security.Policy{AllowedDomains: []string{"news.example"}, Resolver: staticResolver{}},
	}
	urls, pages, skipped, err := service.discover(context.Background(), Request{Query: "krakatau", MaxArticles: 10, MaxPages: 1}, 0, nil, nil, nil)
	if err != nil {
		t.Fatal(err)
	}
	if pages != 1 || len(urls) != 1 || urls[0] != "https://news.example/story" || skipped != 3 {
		t.Fatalf("urls=%v pages=%d skipped=%d", urls, pages, skipped)
	}
}
