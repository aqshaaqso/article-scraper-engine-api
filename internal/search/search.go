package search

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"sort"
	"strings"
	"time"

	"github.com/aqshaaqso/article-scraper-engine-api/internal/config"
	"github.com/aqshaaqso/article-scraper-engine-api/internal/domain"
	"github.com/aqshaaqso/article-scraper-engine-api/internal/security"
	"github.com/google/uuid"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

type Request struct {
	Query       string  `json:"query"`
	MaxArticles int     `json:"max_articles"`
	MaxPages    int     `json:"max_pages"`
	StartDate   *string `json:"start_date"`
	EndDate     *string `json:"end_date"`
	Day         *int    `json:"day"`
	Month       *int    `json:"month"`
	Year        *int    `json:"year"`
	Timezone    string  `json:"timezone"`
	SearchID    string  `json:"search_id"`
	JobID       string  `json:"job_id"`
	CommandID   string  `json:"command_id"`
}
type State struct {
	Month        int      `json:"month"`
	Offset       int      `json:"offset"`
	Pending      []string `json:"pending"`
	Seen         []string `json:"seen"`
	Fingerprints []string `json:"fingerprints"`
	Pages        int      `json:"pages"`
	Skipped      int      `json:"skipped"`
	JobIDs       []string `json:"job_ids"`
	Warnings     []string `json:"warnings"`
	LastCommand  string   `json:"last_command_id,omitempty"`
	LastResult   *Result  `json:"last_result,omitempty"`
}
type Result struct {
	Status       string   `json:"status"`
	JobID        *string  `json:"job_id"`
	Total        int      `json:"total"`
	PagesFetched int      `json:"pages_fetched"`
	Skipped      int      `json:"skipped"`
	URLs         []string `json:"urls"`
	SearchID     *string  `json:"search_id,omitempty"`
}
type discoveryPage struct {
	URLs    []string
	Skipped int
	HasNext bool
}
type Service struct {
	Config      config.Config
	DB          *pgxpool.Pool
	Policy      security.Policy
	Client      *http.Client
	ProviderURL string
}

func (s Service) Process(ctx context.Context, raw []byte, continuation bool) (Result, error) {
	var req Request
	if continuation {
		var p Request
		if err := json.Unmarshal(raw, &p); err != nil {
			return Result{}, bad("payload search tidak valid")
		}
		return s.advance(ctx, p.SearchID, p.CommandID)
	}
	if err := json.Unmarshal(raw, &req); err != nil {
		return Result{}, bad("payload search tidak valid")
	}
	if err := normalizeDateRange(&req, time.Now()); err != nil {
		return Result{}, bad(err.Error())
	}
	if strings.TrimSpace(s.Config.SerpAPIKey) == "" {
		return Result{}, &domain.WorkerError{Code: "fetch_failed", Detail: "SERPAPI_API_KEY belum dikonfigurasi", Retryable: false}
	}
	if len(s.Config.AllowedDomains) == 0 {
		return Result{}, bad("ALLOWED_DOMAINS wajib diisi untuk pencarian berita")
	}
	if req.StartDate != nil {
		return s.advance(ctx, req.SearchID, req.CommandID)
	}
	if existing, ok, err := s.existingJob(ctx, req.JobID); err != nil {
		return Result{}, err
	} else if ok {
		return existing, nil
	}
	urls, pages, skipped, err := s.discover(ctx, req, 0, nil, nil, nil)
	if err != nil {
		return Result{}, err
	}
	if err = s.fillExistingJob(ctx, req.JobID, urls); err != nil {
		return Result{}, err
	}
	status := "queued"
	if len(urls) == 0 {
		status = "no_results"
	}
	return Result{Status: status, JobID: &req.JobID, Total: len(urls), PagesFetched: pages, Skipped: skipped, URLs: urls}, nil
}

func normalizeDateRange(req *Request, now time.Time) error {
	if req.Timezone == "" {
		req.Timezone = "Asia/Jakarta"
	}
	location, err := reportLocation(req.Timezone)
	if err != nil {
		return err
	}
	today := now.In(location)
	selectors := 0
	if req.Day != nil {
		selectors++
	}
	if req.Month != nil {
		selectors++
	}
	if req.Year != nil {
		selectors++
	}
	if selectors > 1 {
		return fmt.Errorf("day, month, dan year tidak boleh digabungkan")
	}
	if selectors > 0 && (req.StartDate != nil || req.EndDate != nil) {
		return fmt.Errorf("filter day/month/year tidak boleh digabung dengan start_date/end_date")
	}

	var start, end time.Time
	switch {
	case req.Day != nil:
		if *req.Day < 1 || *req.Day > daysInMonth(today.Year(), today.Month()) {
			return fmt.Errorf("day tidak tersedia pada bulan berjalan")
		}
		start = time.Date(today.Year(), today.Month(), *req.Day, 0, 0, 0, 0, location)
		end = start
	case req.Month != nil:
		if *req.Month < 1 || *req.Month > 12 {
			return fmt.Errorf("month harus antara 1 dan 12")
		}
		month := time.Month(*req.Month)
		start = time.Date(today.Year(), month, 1, 0, 0, 0, 0, location)
		end = time.Date(today.Year(), month, daysInMonth(today.Year(), month), 0, 0, 0, 0, location)
	case req.Year != nil:
		if *req.Year < 1900 || *req.Year > 2100 {
			return fmt.Errorf("year harus antara 1900 dan 2100")
		}
		start = time.Date(*req.Year, time.January, 1, 0, 0, 0, 0, location)
		end = time.Date(*req.Year, time.December, 31, 0, 0, 0, 0, location)
	case req.StartDate != nil && req.EndDate == nil:
		parsed, parseErr := time.Parse("2006-01-02", *req.StartDate)
		if parseErr != nil {
			return fmt.Errorf("start_date tidak valid")
		}
		start = parsed
		end = time.Date(today.Year(), today.Month(), today.Day(), 0, 0, 0, 0, time.UTC)
	case req.StartDate == nil && req.EndDate != nil:
		return fmt.Errorf("end_date membutuhkan start_date")
	case req.StartDate == nil:
		return nil
	default:
		parsedStart, startErr := time.Parse("2006-01-02", *req.StartDate)
		parsedEnd, endErr := time.Parse("2006-01-02", *req.EndDate)
		if startErr != nil || endErr != nil {
			return fmt.Errorf("rentang tanggal tidak valid")
		}
		start, end = parsedStart, parsedEnd
	}
	if start.After(end) {
		return fmt.Errorf("start_date tidak boleh melewati end_date")
	}
	months := (end.Year()-start.Year())*12 + int(end.Month()-start.Month()) + 1
	if months > 120 {
		return fmt.Errorf("maksimal 120 bulan kalender per pencarian")
	}
	startRaw, endRaw := start.Format("2006-01-02"), end.Format("2006-01-02")
	req.StartDate, req.EndDate = &startRaw, &endRaw
	req.Day, req.Month, req.Year = nil, nil, nil
	return nil
}

func reportLocation(name string) (*time.Location, error) {
	offsets := map[string]int{
		"UTC": 0, "Asia/Jakarta": 7, "Asia/Makassar": 8, "Asia/Jayapura": 9,
	}
	hours, ok := offsets[name]
	if !ok {
		return nil, fmt.Errorf("timezone tidak didukung")
	}
	return time.FixedZone(name, hours*60*60), nil
}

func daysInMonth(year int, month time.Month) int {
	return time.Date(year, month+1, 0, 0, 0, 0, 0, time.UTC).Day()
}

func (s Service) discover(ctx context.Context, req Request, offset int, start, end *time.Time, seen map[string]bool) ([]string, int, int, error) {
	if req.MaxArticles <= 0 {
		req.MaxArticles = 50
	}
	if req.MaxPages <= 0 {
		req.MaxPages = 5
	}
	sites := make([]string, len(s.Config.AllowedDomains))
	for i, d := range s.Config.AllowedDomains {
		sites[i] = "site:" + d
	}
	query := req.Query + " (" + strings.Join(sites, " OR ") + ")"
	var urls []string
	skipped := 0
	pages := 0
	if seen == nil {
		seen = map[string]bool{}
	}
	for page := 0; page < req.MaxPages && len(urls) < req.MaxArticles; page++ {
		result, err := s.discoverPage(ctx, query, offset+page*10, start, end, seen)
		if err != nil {
			return nil, pages, skipped, err
		}
		pages++
		skipped += result.Skipped
		remaining := req.MaxArticles - len(urls)
		if remaining > len(result.URLs) {
			remaining = len(result.URLs)
		}
		urls = append(urls, result.URLs[:remaining]...)
		if !result.HasNext {
			break
		}
	}
	return urls, pages, skipped, nil
}

func (s Service) discoverPage(ctx context.Context, query string, offset int, start, end *time.Time, seen map[string]bool) (discoveryPage, error) {
	payload, err := s.fetchPage(ctx, query, offset, start, end)
	if err != nil {
		return discoveryPage{}, err
	}
	rows, _ := payload["organic_results"].([]any)
	result := discoveryPage{}
	for _, value := range rows {
		row, _ := value.(map[string]any)
		link, _ := row["link"].(string)
		if link == "" {
			result.Skipped++
			continue
		}
		target, validErr := s.Policy.Validate(ctx, link)
		if validErr != nil {
			result.Skipped++
			continue
		}
		normalized := target.URL.String()
		path := strings.ToLower(target.URL.Path)
		listing := path == "/" || strings.HasPrefix(path, "/tag/") || strings.HasPrefix(path, "/tags/") || strings.HasPrefix(path, "/topic/") || strings.HasPrefix(path, "/topik/") || strings.HasPrefix(path, "/search") || strings.HasPrefix(path, "/indeks") || strings.HasPrefix(path, "/index")
		if seen[normalized] || listing {
			result.Skipped++
			continue
		}
		seen[normalized] = true
		result.URLs = append(result.URLs, normalized)
	}
	pagination, _ := payload["serpapi_pagination"].(map[string]any)
	result.HasNext = len(rows) > 0 && pagination != nil && pagination["next"] != nil
	return result, nil
}

func (s Service) fetchPage(ctx context.Context, query string, offset int, start, end *time.Time) (map[string]any, error) {
	params := url.Values{"engine": {"google"}, "q": {query}, "gl": {"id"}, "hl": {"id"}, "start": {fmt.Sprint(offset)}, "api_key": {s.Config.SerpAPIKey}}
	if start != nil && end != nil {
		params.Set("tbs", fmt.Sprintf("cdr:1,cd_min:%s,cd_max:%s", start.Format("01/02/2006"), end.Format("01/02/2006")))
	}
	base := s.ProviderURL
	if base == "" {
		base = "https://serpapi.com/search.json"
	}
	req, _ := http.NewRequestWithContext(ctx, http.MethodGet, base+"?"+params.Encode(), nil)
	client := s.Client
	if client == nil {
		client = &http.Client{Timeout: 45 * time.Second, CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	}
	resp, err := client.Do(req)
	if err != nil {
		return nil, &domain.WorkerError{Code: "fetch_failed", Detail: "SerpAPI tidak dapat diakses atau respons tidak valid.", Retryable: true}
	}
	defer resp.Body.Close()
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return nil, &domain.WorkerError{Code: "fetch_failed", Detail: "Pencarian SerpAPI gagal; periksa akun dan kuota.", Retryable: resp.StatusCode >= 500}
	}
	body, err := io.ReadAll(io.LimitReader(resp.Body, 2*1024*1024+1))
	if err != nil || len(body) > 2*1024*1024 {
		return nil, &domain.WorkerError{Code: "fetch_failed", Detail: "SerpAPI tidak dapat diakses atau respons tidak valid.", Retryable: true}
	}
	var payload map[string]any
	if json.Unmarshal(body, &payload) != nil {
		return nil, &domain.WorkerError{Code: "fetch_failed", Detail: "SerpAPI tidak dapat diakses atau respons tidak valid.", Retryable: true}
	}
	if payload["error"] != nil {
		return nil, &domain.WorkerError{Code: "fetch_failed", Detail: "SerpAPI mengembalikan error; periksa akun dan kuota.", Retryable: false}
	}
	if _, ok := payload["organic_results"].([]any); !ok {
		payload["organic_results"] = []any{}
	}
	return payload, nil
}

func (s Service) advance(ctx context.Context, searchID, commandID string) (Result, error) {
	tx, err := s.DB.BeginTx(ctx, pgx.TxOptions{})
	if err != nil {
		return Result{}, err
	}
	defer tx.Rollback(ctx) //nolint:errcheck
	var requestRaw, stateRaw []byte
	var status string
	if err = tx.QueryRow(ctx, "SELECT request_json,state_json,status FROM search_runs WHERE id=$1::uuid FOR UPDATE", searchID).Scan(&requestRaw, &stateRaw, &status); err != nil {
		return Result{}, err
	}
	var req Request
	var state State
	if json.Unmarshal(requestRaw, &req) != nil || json.Unmarshal(stateRaw, &state) != nil {
		return Result{}, bad("checkpoint pencarian tidak valid")
	}
	req.SearchID = searchID
	if commandID != "" && state.LastCommand == commandID && state.LastResult != nil {
		return *state.LastResult, nil
	}
	if status == "discovery_complete" {
		return Result{Status: status, SearchID: &searchID, URLs: []string{}}, nil
	}
	windows, err := monthWindows(*req.StartDate, *req.EndDate)
	if err != nil {
		return Result{}, bad("rentang tanggal tidak valid")
	}
	seen := map[string]bool{}
	for _, v := range state.Seen {
		seen[v] = true
	}
	var collected []string
	pages, skipped := 0, 0
	if len(state.Pending) > 0 {
		take := req.MaxArticles
		if take > len(state.Pending) {
			take = len(state.Pending)
		}
		collected = append(collected, state.Pending[:take]...)
		state.Pending = append([]string(nil), state.Pending[take:]...)
	}
	for len(collected) < req.MaxArticles && state.Month < len(windows) && pages < req.MaxPages {
		window := windows[state.Month]
		lower := window[0].AddDate(0, 0, -1)
		upper := window[1].AddDate(0, 0, 1)
		sites := make([]string, len(s.Config.AllowedDomains))
		for i, d := range s.Config.AllowedDomains {
			sites[i] = "site:" + d
		}
		query := req.Query + " (" + strings.Join(sites, " OR ") + ")"
		page, discoverErr := s.discoverPage(ctx, query, state.Offset, &lower, &upper, seen)
		if discoverErr != nil {
			return Result{}, discoverErr
		}
		pages++
		skipped += page.Skipped
		finger := fingerprint(page.URLs)
		repeated := contains(state.Fingerprints, finger)
		if repeated {
			state.Warnings = append(state.Warnings, window[0].Format("2006-01")+": provider mengulang halaman; dihentikan")
		}
		if !repeated {
			remaining := req.MaxArticles - len(collected)
			if remaining > len(page.URLs) {
				remaining = len(page.URLs)
			}
			collected = append(collected, page.URLs[:remaining]...)
			state.Pending = append(state.Pending, page.URLs[remaining:]...)
		}
		if !page.HasNext || repeated {
			state.Month++
			state.Offset = 0
			state.Fingerprints = nil
		} else {
			state.Offset += 10
			state.Fingerprints = append(state.Fingerprints, finger)
		}
	}
	state.Seen = keys(seen)
	state.Pages += pages
	state.Skipped += skipped
	var jobID *string
	if len(collected) > 0 {
		id := strings.ReplaceAll(uuid.NewString(), "-", "")
		jobID = &id
		state.JobIDs = append(state.JobIDs, id)
		contextJSON := map[string]any{"search_id": searchID, "date_filter": map[string]any{"start_date": *req.StartDate, "end_date": *req.EndDate, "timezone": req.Timezone}}
		if _, err = tx.Exec(ctx, "INSERT INTO jobs(id,command_id,search_id,total,search_context) VALUES($1::uuid,NULLIF($2,'')::uuid,$3::uuid,$4,$5)", id, commandID, searchID, len(collected), contextJSON); err != nil {
			return Result{}, err
		}
		for i, u := range collected {
			if _, err = tx.Exec(ctx, "INSERT INTO job_items(job_id,position,url) VALUES($1::uuid,$2,$3)", id, i, u); err != nil {
				return Result{}, err
			}
		}
	}
	complete := state.Month >= len(windows) && len(state.Pending) == 0
	newStatus := "ready"
	if complete {
		newStatus = "discovery_complete"
	}
	result := Result{Status: map[bool]string{true: "discovery_complete", false: "queued"}[complete && jobID == nil], JobID: jobID, Total: len(collected), PagesFetched: pages, Skipped: skipped, URLs: collected, SearchID: &searchID}
	state.LastCommand = commandID
	state.LastResult = &result
	encoded, _ := json.Marshal(state)
	_, err = tx.Exec(ctx, "UPDATE search_runs SET state_json=$2,status=$3,requests_attempted=requests_attempted+$4,updated_at=now() WHERE id=$1::uuid", searchID, encoded, newStatus, pages)
	if err != nil {
		return Result{}, err
	}
	if err = tx.Commit(ctx); err != nil {
		return Result{}, err
	}
	return result, nil
}

func (s Service) existingJob(ctx context.Context, jobID string) (Result, bool, error) {
	var status string
	var total int
	if err := s.DB.QueryRow(ctx, "SELECT status,total FROM jobs WHERE id=$1::uuid", jobID).Scan(&status, &total); err != nil {
		return Result{}, false, err
	}
	if total == 0 && status != "completed" {
		return Result{}, false, nil
	}
	rows, err := s.DB.Query(ctx, "SELECT url FROM job_items WHERE job_id=$1::uuid ORDER BY position", jobID)
	if err != nil {
		return Result{}, false, err
	}
	defer rows.Close()
	urls := make([]string, 0, total)
	for rows.Next() {
		var raw string
		if err = rows.Scan(&raw); err != nil {
			return Result{}, false, err
		}
		urls = append(urls, raw)
	}
	resultStatus := "queued"
	if total == 0 {
		resultStatus = "no_results"
	}
	return Result{Status: resultStatus, JobID: &jobID, Total: total, URLs: urls}, true, rows.Err()
}

func (s Service) fillExistingJob(ctx context.Context, jobID string, urls []string) error {
	tx, err := s.DB.BeginTx(ctx, pgx.TxOptions{})
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx) //nolint:errcheck
	for i, u := range urls {
		if _, err = tx.Exec(ctx, "INSERT INTO job_items(job_id,position,url) VALUES($1::uuid,$2,$3)", jobID, i, u); err != nil {
			return err
		}
	}
	status := "queued"
	if len(urls) == 0 {
		status = "completed"
	}
	_, err = tx.Exec(ctx, "UPDATE jobs SET total=$2,status=$3,finished_at=CASE WHEN $2=0 THEN now() ELSE NULL END WHERE id=$1::uuid", jobID, len(urls), status)
	if err != nil {
		return err
	}
	return tx.Commit(ctx)
}
func monthWindows(startRaw, endRaw string) ([][2]time.Time, error) {
	start, err := time.Parse("2006-01-02", startRaw)
	if err != nil {
		return nil, err
	}
	end, err := time.Parse("2006-01-02", endRaw)
	if err != nil || start.After(end) {
		return nil, fmt.Errorf("invalid range")
	}
	cursor := time.Date(start.Year(), start.Month(), 1, 0, 0, 0, 0, time.UTC)
	var out [][2]time.Time
	for !cursor.After(end) {
		last := cursor.AddDate(0, 1, -1)
		lower := cursor
		if lower.Before(start) {
			lower = start
		}
		upper := last
		if upper.After(end) {
			upper = end
		}
		out = append(out, [2]time.Time{lower, upper})
		cursor = cursor.AddDate(0, 1, 0)
	}
	return out, nil
}
func fingerprint(v []string) string {
	sum := sha256.Sum256([]byte(strings.Join(v, "\n")))
	return hex.EncodeToString(sum[:])
}
func contains(v []string, s string) bool {
	for _, x := range v {
		if x == s {
			return true
		}
	}
	return false
}
func keys(m map[string]bool) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	sort.Strings(out)
	return out
}
func bad(detail string) error {
	return &domain.WorkerError{Code: "internal_error", Detail: detail, Retryable: false}
}
