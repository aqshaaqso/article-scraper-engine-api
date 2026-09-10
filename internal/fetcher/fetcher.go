package fetcher

import (
	"context"
	"crypto/tls"
	"fmt"
	"io"
	"net"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/aqshaaqso/article-scraper-engine-api/internal/security"
	"github.com/temoto/robotstxt"
)

type RateLimiter interface {
	ReserveDomain(context.Context, string, time.Duration) (time.Duration, error)
}

type Response struct {
	Body         []byte
	FinalURL     string
	ContentType  string
	RobotsStatus string
}

type Fetcher struct {
	Policy           security.Policy
	Limiter          RateLimiter
	Timeout          time.Duration
	MaxBytes         int64
	MaxRedirects     int
	UserAgent        string
	DomainDelay      time.Duration
	RespectRobots    bool
	RobotsFailClosed bool
	RequestOnce      func(context.Context, security.Target, int64) (*http.Response, error)
}

func (f *Fetcher) Fetch(ctx context.Context, raw string) (Response, error) {
	current := raw
	robotsStatus := "unknown"
	for redirects := 0; redirects <= f.MaxRedirects; redirects++ {
		target, err := f.Policy.Validate(ctx, current)
		if err != nil {
			return Response{}, workerErr("unsafe_url", err.Error(), false)
		}
		status, delay, err := f.robots(ctx, target)
		if err != nil {
			return Response{}, err
		}
		robotsStatus = status
		if wait, err := f.Limiter.ReserveDomain(ctx, target.Hostname, max(f.DomainDelay, delay)); err != nil {
			return Response{}, workerErr("internal_error", "rate limiter gagal", true)
		} else if err = sleep(ctx, wait); err != nil {
			return Response{}, err
		}
		resp, err := f.doRequest(ctx, target, f.MaxBytes)
		if err != nil {
			return Response{}, err
		}
		location := resp.Header.Get("Location")
		if !isRedirect(resp.StatusCode) {
			defer resp.Body.Close()
			if resp.StatusCode < 200 || resp.StatusCode >= 300 {
				return Response{}, workerErr("fetch_failed", fmt.Sprintf("Halaman mengembalikan HTTP %d", resp.StatusCode), resp.StatusCode >= 500)
			}
			ct := strings.ToLower(resp.Header.Get("Content-Type"))
			if !strings.Contains(ct, "text/html") && !strings.Contains(ct, "application/xhtml+xml") {
				return Response{}, workerErr("fetch_failed", "URL tidak mengembalikan dokumen HTML", false)
			}
			body, err := readLimited(resp.Body, f.MaxBytes)
			if err != nil {
				return Response{}, err
			}
			return Response{body, target.URL.String(), ct, robotsStatus}, nil
		}
		resp.Body.Close()
		if location == "" {
			return Response{}, workerErr("fetch_failed", "Provider mengirim redirect tanpa Location", false)
		}
		if redirects == f.MaxRedirects {
			return Response{}, workerErr("fetch_failed", "Jumlah redirect melewati batas", false)
		}
		next, err := target.URL.Parse(location)
		if err != nil {
			return Response{}, workerErr("unsafe_url", "redirect tidak valid", false)
		}
		current = next.String()
	}
	return Response{}, workerErr("fetch_failed", "Jumlah redirect melewati batas", false)
}

func (f *Fetcher) robots(ctx context.Context, target security.Target) (string, time.Duration, error) {
	if !f.RespectRobots {
		return "disabled", 0, nil
	}
	u := *target.URL
	u.Path = "/robots.txt"
	u.RawQuery = ""
	u.Fragment = ""
	current := u.String()
	var resp *http.Response
	var err error
	for redirects := 0; redirects <= f.MaxRedirects; redirects++ {
		robotsTarget, validateErr := f.Policy.Validate(ctx, current)
		if validateErr != nil {
			return "", 0, workerErr("robots_denied", "redirect robots.txt tidak aman", false)
		}
		wait, reserveErr := f.Limiter.ReserveDomain(ctx, robotsTarget.Hostname, f.DomainDelay)
		if reserveErr != nil {
			return "", 0, workerErr("internal_error", "rate limiter gagal", true)
		}
		if err = sleep(ctx, wait); err != nil {
			return "", 0, err
		}
		resp, err = f.doRequest(ctx, robotsTarget, 512*1024)
		if err != nil {
			break
		}
		if !isRedirect(resp.StatusCode) {
			break
		}
		location := resp.Header.Get("Location")
		resp.Body.Close()
		if location == "" || redirects == f.MaxRedirects {
			err = fmt.Errorf("redirect robots invalid")
			break
		}
		next, parseErr := robotsTarget.URL.Parse(location)
		if parseErr != nil {
			err = parseErr
			break
		}
		current = next.String()
	}
	if err != nil {
		if f.RobotsFailClosed {
			return "", 0, workerErr("robots_denied", "robots.txt tidak dapat diperiksa", false)
		}
		return "unavailable", 0, nil
	}
	defer resp.Body.Close()
	if resp.StatusCode == 404 {
		return "not_found", 0, nil
	}
	if resp.StatusCode == 401 || resp.StatusCode == 403 {
		return "", 0, workerErr("robots_denied", "robots.txt menolak akses crawler", false)
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		if f.RobotsFailClosed {
			return "", 0, workerErr("robots_denied", fmt.Sprintf("robots.txt mengembalikan HTTP %d", resp.StatusCode), false)
		}
		return "unavailable", 0, nil
	}
	body, readErr := readLimited(resp.Body, 512*1024)
	if readErr != nil {
		return "", 0, readErr
	}
	data, parseErr := robotstxt.FromBytes(body)
	if parseErr != nil {
		if f.RobotsFailClosed {
			return "", 0, workerErr("robots_denied", "robots.txt tidak valid", false)
		}
		return "unavailable", 0, nil
	}
	group := data.FindGroup(f.UserAgent)
	if !group.Test(target.URL.Path) {
		return "", 0, workerErr("robots_denied", "URL tidak diizinkan oleh robots.txt", false)
	}
	return "allowed", group.CrawlDelay, nil
}

func (f *Fetcher) request(ctx context.Context, target security.Target, maxBytes int64) (*http.Response, error) {
	dialer := net.Dialer{Timeout: f.Timeout}
	// A proxy would bypass the validated, pinned destination IP. Outbound scraping
	// therefore always connects directly to the address approved by URLPolicy.
	transport := &http.Transport{Proxy: nil, TLSClientConfig: &tls.Config{ServerName: target.Hostname, MinVersion: tls.VersionTLS12}, DisableCompression: true, ResponseHeaderTimeout: f.Timeout, DialContext: func(ctx context.Context, network, _ string) (net.Conn, error) {
		return dialer.DialContext(ctx, network, net.JoinHostPort(target.IPs[0].String(), target.Port))
	}}
	defer transport.CloseIdleConnections()
	client := http.Client{Transport: transport, Timeout: f.Timeout, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, target.URL.String(), nil)
	if err != nil {
		return nil, workerErr("fetch_failed", "request tidak valid", false)
	}
	req.Host = target.Hostname
	req.Header.Set("User-Agent", f.UserAgent)
	req.Header.Set("Accept", "text/html,application/xhtml+xml;q=0.9,text/plain;q=0.5")
	req.Header.Set("Accept-Encoding", "identity")
	resp, err := client.Do(req)
	if err != nil {
		return nil, workerErr("fetch_failed", "Gagal mengambil halaman", true)
	}
	if length := resp.Header.Get("Content-Length"); length != "" {
		if n, e := strconv.ParseInt(length, 10, 64); e != nil || n > maxBytes {
			resp.Body.Close()
			return nil, workerErr("fetch_failed", "Respons melebihi batas ukuran", false)
		}
	}
	return resp, nil
}

func (f *Fetcher) doRequest(ctx context.Context, target security.Target, maxBytes int64) (*http.Response, error) {
	if f.RequestOnce != nil {
		return f.RequestOnce(ctx, target, maxBytes)
	}
	return f.request(ctx, target, maxBytes)
}

func readLimited(r io.Reader, n int64) ([]byte, error) {
	body, err := io.ReadAll(io.LimitReader(r, n+1))
	if err != nil {
		return nil, workerErr("fetch_failed", "Gagal membaca halaman", true)
	}
	if int64(len(body)) > n {
		return nil, workerErr("fetch_failed", "Respons melebihi batas ukuran", false)
	}
	return body, nil
}
func isRedirect(s int) bool { return s == 301 || s == 302 || s == 303 || s == 307 || s == 308 }
func sleep(ctx context.Context, d time.Duration) error {
	if d <= 0 {
		return nil
	}
	timer := time.NewTimer(d)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return ctx.Err()
	case <-timer.C:
		return nil
	}
}

type codedError struct {
	code, detail string
	retry        bool
}

func (e *codedError) Error() string                   { return e.detail }
func (e *codedError) Code() string                    { return e.code }
func (e *codedError) Retryable() bool                 { return e.retry }
func workerErr(code, detail string, retry bool) error { return &codedError{code, detail, retry} }
