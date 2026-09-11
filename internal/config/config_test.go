package config

import "testing"

func TestDefaultUserAgentMatchesRelease(t *testing.T) {
	t.Setenv("DATABASE_URL", "postgres://example.invalid/article_scraper")
	t.Setenv("DATABASE_PATH", "")
	t.Setenv("SCRAPER_USER_AGENT", "")

	cfg, err := Load()
	if err != nil {
		t.Fatal(err)
	}
	if cfg.UserAgent != "ArticleScraperLab/0.4-go" {
		t.Fatalf("user agent=%q", cfg.UserAgent)
	}
}
