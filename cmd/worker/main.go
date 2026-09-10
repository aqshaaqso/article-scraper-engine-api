package main

import (
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"os"
	"os/signal"
	"sync"
	"syscall"
	"time"

	"github.com/aqshaaqso/article-scraper-engine-api/internal/config"
	"github.com/aqshaaqso/article-scraper-engine-api/internal/domain"
	"github.com/aqshaaqso/article-scraper-engine-api/internal/extractor"
	"github.com/aqshaaqso/article-scraper-engine-api/internal/fetcher"
	searcher "github.com/aqshaaqso/article-scraper-engine-api/internal/search"
	"github.com/aqshaaqso/article-scraper-engine-api/internal/security"
	"github.com/aqshaaqso/article-scraper-engine-api/internal/store"
)

type coded interface {
	Code() string
	Retryable() bool
}
type app struct {
	cfg       config.Config
	store     *store.Store
	fetcher   *fetcher.Fetcher
	extractor extractor.Extractor
	search    searcher.Service
	log       *slog.Logger
	workCtx   context.Context
}

func main() {
	cfg, err := config.Load()
	if err != nil {
		slog.Error("configuration invalid", "error", err)
		os.Exit(1)
	}
	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()
	db, err := store.Open(ctx, cfg.DatabaseURL)
	if err != nil {
		slog.Error("database unavailable", "error", err)
		os.Exit(1)
	}
	defer db.DB.Close()
	logger := slog.New(slog.NewJSONHandler(os.Stdout, nil))
	a := app{cfg: cfg, store: db, log: logger, workCtx: context.Background(), extractor: extractor.Extractor{MinWordCount: cfg.MinWordCount}}
	a.fetcher = &fetcher.Fetcher{Policy: security.Policy{AllowHTTP: cfg.AllowHTTP, AllowedDomains: cfg.AllowedDomains}, Limiter: db, Timeout: cfg.HTTPTimeout, MaxBytes: cfg.MaxHTMLBytes, MaxRedirects: cfg.MaxRedirects, UserAgent: cfg.UserAgent, DomainDelay: cfg.DomainDelay, RespectRobots: cfg.RespectRobots, RobotsFailClosed: cfg.RobotsFailClosed}
	a.search = searcher.Service{Config: cfg, DB: db.DB, Policy: security.Policy{AllowHTTP: cfg.AllowHTTP, AllowedDomains: cfg.AllowedDomains}}
	var wg sync.WaitGroup
	wg.Add(1)
	go func() {
		defer wg.Done()
		ticker := time.NewTicker(10 * time.Second)
		defer ticker.Stop()
		for {
			if err := db.WorkerHeartbeat(ctx, cfg.WorkerID); err != nil {
				logger.Error("worker heartbeat", "error", err)
			}
			select {
			case <-ctx.Done():
				return
			case <-ticker.C:
			}
		}
	}()
	wg.Add(1)
	go func() { defer wg.Done(); a.commandLoop(ctx) }()
	for i := 0; i < cfg.WorkerCount; i++ {
		wg.Add(1)
		go func() { defer wg.Done(); a.itemLoop(ctx) }()
	}
	logger.Info("worker started", "worker_id", cfg.WorkerID, "workers", cfg.WorkerCount)
	wg.Wait()
	logger.Info("worker stopped")
}

func (a *app) commandLoop(ctx context.Context) {
	for {
		if ctx.Err() != nil {
			return
		}
		command, err := a.store.ClaimCommand(ctx, a.cfg.WorkerID, a.cfg.LeaseDuration, a.cfg.MaxAttempts)
		if err != nil {
			a.log.Error("claim command", "error", err)
			wait(ctx, a.cfg.PollInterval)
			continue
		}
		if command == nil {
			wait(ctx, a.cfg.PollInterval)
			continue
		}
		a.processCommand(a.workCtx, *command)
	}
}
func (a *app) processCommand(ctx context.Context, c domain.Command) {
	a.log.Info("command started", "command_id", c.ID, "type", c.Type)
	done := make(chan struct{})
	go func() {
		interval := a.cfg.LeaseDuration / 3
		if interval < time.Second {
			interval = time.Second
		}
		ticker := time.NewTicker(interval)
		defer ticker.Stop()
		for {
			select {
			case <-done:
				return
			case <-ctx.Done():
				return
			case <-ticker.C:
				if err := a.store.Heartbeat(ctx, c.ID, a.cfg.WorkerID, a.cfg.LeaseDuration); err != nil {
					a.log.Error("command heartbeat", "command_id", c.ID, "error", err)
				}
			}
		}
	}()
	defer close(done)
	var result any
	var err error
	switch c.Type {
	case "scrape_one":
		var body struct {
			URL string `json:"url"`
		}
		if err = json.Unmarshal(c.Payload, &body); err == nil {
			result, err = a.scrape(ctx, body.URL)
		}
	case "scrape_batch":
		result = map[string]string{"status": "queued"}
	case "search":
		result, err = a.search.Process(ctx, c.Payload, false)
	case "search_continue":
		result, err = a.search.Process(ctx, c.Payload, true)
	default:
		err = &domain.WorkerError{Code: "internal_error", Detail: "tipe command tidak dikenal", Retryable: false}
	}
	if err == nil {
		if finishErr := a.store.CompleteCommand(ctx, c.ID, a.cfg.WorkerID, result); finishErr != nil {
			a.log.Error("complete command", "command_id", c.ID, "error", finishErr)
		}
		return
	}
	workErr := classify(err)
	if failErr := a.store.FailCommand(ctx, c.ID, a.cfg.WorkerID, workErr.Code, workErr.Detail, workErr.Retryable, a.cfg.MaxAttempts); failErr != nil {
		a.log.Error("fail command", "command_id", c.ID, "error", failErr)
	}
}
func (a *app) itemLoop(ctx context.Context) {
	for {
		if ctx.Err() != nil {
			return
		}
		item, err := a.store.ClaimItem(ctx, a.cfg.WorkerID, a.cfg.LeaseDuration, a.cfg.MaxAttempts)
		if err != nil {
			a.log.Error("claim item", "error", err)
			wait(ctx, a.cfg.PollInterval)
			continue
		}
		if item == nil {
			wait(ctx, a.cfg.PollInterval)
			continue
		}
		done := make(chan struct{})
		go a.heartbeatItem(a.workCtx, item.ID, done)
		target, targetErr := security.Policy{AllowHTTP: a.cfg.AllowHTTP, AllowedDomains: a.cfg.AllowedDomains}.Validate(a.workCtx, item.URL)
		normalized, host := "", ""
		if targetErr == nil {
			normalized = target.URL.String()
			host = target.Hostname
		}
		article, workErr := a.scrape(a.workCtx, item.URL)
		classified := classify(workErr)
		if workErr == nil {
			classified = nil
		}
		close(done)
		if err = a.store.FinishItem(a.workCtx, *item, a.cfg.WorkerID, normalized, host, article, classified, a.cfg.MaxAttempts); err != nil {
			a.log.Error("finish item", "job_id", item.JobID, "error", err)
		}
	}
}

func (a *app) heartbeatItem(ctx context.Context, itemID int64, done <-chan struct{}) {
	interval := a.cfg.LeaseDuration / 3
	if interval < time.Second {
		interval = time.Second
	}
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	for {
		select {
		case <-done:
			return
		case <-ticker.C:
			if err := a.store.HeartbeatItem(ctx, itemID, a.cfg.WorkerID, a.cfg.LeaseDuration); err != nil {
				a.log.Error("item heartbeat", "item_id", itemID, "error", err)
			}
		}
	}
}
func (a *app) scrape(ctx context.Context, raw string) (domain.Article, error) {
	response, err := a.fetcher.Fetch(ctx, raw)
	if err != nil {
		return domain.Article{}, err
	}
	article, err := a.extractor.Extract(response.Body, raw, response.FinalURL, response.RobotsStatus)
	if err != nil {
		return domain.Article{}, &domain.WorkerError{Code: "extraction_failed", Detail: err.Error(), Retryable: false}
	}
	return article, nil
}
func classify(err error) *domain.WorkerError {
	if err == nil {
		return nil
	}
	var w *domain.WorkerError
	if errors.As(err, &w) {
		return w
	}
	var c coded
	if errors.As(err, &c) {
		return &domain.WorkerError{Code: c.Code(), Detail: err.Error(), Retryable: c.Retryable()}
	}
	return &domain.WorkerError{Code: "internal_error", Detail: "Terjadi kesalahan internal", Retryable: true}
}
func wait(ctx context.Context, d time.Duration) {
	timer := time.NewTimer(d)
	defer timer.Stop()
	select {
	case <-ctx.Done():
	case <-timer.C:
	}
}
