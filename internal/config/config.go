package config

import (
	"fmt"
	"os"
	"strconv"
	"strings"
	"time"
)

type Config struct {
	DatabaseURL      string
	SerpAPIKey       string
	AllowedDomains   []string
	AllowHTTP        bool
	RespectRobots    bool
	RobotsFailClosed bool
	HTTPTimeout      time.Duration
	MaxHTMLBytes     int64
	MaxRedirects     int
	MinWordCount     int
	UserAgent        string
	DomainDelay      time.Duration
	WorkerCount      int
	LeaseDuration    time.Duration
	PollInterval     time.Duration
	MaxAttempts      int
	WorkerID         string
}

func Load() (Config, error) {
	host, _ := os.Hostname()
	c := Config{
		DatabaseURL: os.Getenv("DATABASE_URL"), SerpAPIKey: os.Getenv("SERPAPI_API_KEY"),
		AllowedDomains: splitDomains(os.Getenv("ALLOWED_DOMAINS")),
		UserAgent:      value("SCRAPER_USER_AGENT", "ArticleScraperLab/0.3-go"),
		WorkerID:       value("WORKER_ID", host+"-worker"),
	}
	var err error
	if strings.TrimSpace(c.DatabaseURL) == "" {
		return c, fmt.Errorf("DATABASE_URL wajib diisi")
	}
	if os.Getenv("DATABASE_PATH") != "" {
		return c, fmt.Errorf("DATABASE_PATH tidak didukung worker Go; gunakan DATABASE_URL")
	}
	if c.AllowHTTP, err = boolean("ALLOW_HTTP", false); err != nil {
		return c, err
	}
	if c.RespectRobots, err = boolean("RESPECT_ROBOTS", true); err != nil {
		return c, err
	}
	if c.RobotsFailClosed, err = boolean("ROBOTS_FAIL_CLOSED", true); err != nil {
		return c, err
	}
	if c.HTTPTimeout, err = durationSeconds("HTTP_TIMEOUT_SECONDS", 15); err != nil {
		return c, err
	}
	if c.DomainDelay, err = durationSeconds("DOMAIN_DELAY_SECONDS", 1); err != nil {
		return c, err
	}
	if c.LeaseDuration, err = durationSeconds("LEASE_SECONDS", 600); err != nil {
		return c, err
	}
	if c.PollInterval, err = durationSeconds("POLL_INTERVAL_SECONDS", 1); err != nil {
		return c, err
	}
	if c.MaxHTMLBytes, err = positiveInt64("MAX_HTML_BYTES", 5*1024*1024); err != nil {
		return c, err
	}
	if c.MaxRedirects, err = positiveInt("MAX_REDIRECTS", 3); err != nil {
		return c, err
	}
	if c.MinWordCount, err = positiveInt("MIN_WORD_COUNT", 80); err != nil {
		return c, err
	}
	if c.WorkerCount, err = positiveInt("WORKER_COUNT", 3); err != nil {
		return c, err
	}
	if c.MaxAttempts, err = positiveInt("MAX_ATTEMPTS", 3); err != nil {
		return c, err
	}
	return c, nil
}

func value(name, fallback string) string {
	if v := strings.TrimSpace(os.Getenv(name)); v != "" {
		return v
	}
	return fallback
}
func splitDomains(raw string) []string {
	var out []string
	for _, v := range strings.Split(raw, ",") {
		if v = strings.TrimSuffix(strings.ToLower(strings.TrimSpace(v)), "."); v != "" {
			out = append(out, v)
		}
	}
	return out
}
func boolean(name string, fallback bool) (bool, error) {
	raw := value(name, strconv.FormatBool(fallback))
	v, err := strconv.ParseBool(raw)
	if err != nil {
		return false, fmt.Errorf("%s harus true atau false", name)
	}
	return v, nil
}
func positiveInt(name string, fallback int) (int, error) {
	v, err := strconv.Atoi(value(name, strconv.Itoa(fallback)))
	if err != nil || v <= 0 {
		return 0, fmt.Errorf("%s harus berupa angka bulat positif", name)
	}
	return v, nil
}
func positiveInt64(name string, fallback int64) (int64, error) {
	v, err := strconv.ParseInt(value(name, strconv.FormatInt(fallback, 10)), 10, 64)
	if err != nil || v <= 0 {
		return 0, fmt.Errorf("%s harus berupa angka bulat positif", name)
	}
	return v, nil
}
func durationSeconds(name string, fallback int) (time.Duration, error) {
	raw := value(name, strconv.Itoa(fallback))
	v, err := strconv.ParseFloat(raw, 64)
	if err != nil || v <= 0 {
		return 0, fmt.Errorf("%s harus berupa angka positif", name)
	}
	return time.Duration(v * float64(time.Second)), nil
}
