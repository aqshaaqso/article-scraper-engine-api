package store

import (
	"context"
	"encoding/json"
	"fmt"
	"time"

	"github.com/aqshaaqso/article-scraper-engine-api/internal/domain"
	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

const SchemaVersion = 4

type Store struct{ DB *pgxpool.Pool }

type JobItem struct {
	ID    int64
	JobID string
	URL   string
}

func Open(ctx context.Context, databaseURL string) (*Store, error) {
	db, err := pgxpool.New(ctx, databaseURL)
	if err != nil {
		return nil, err
	}
	if err = db.Ping(ctx); err != nil {
		db.Close()
		return nil, err
	}
	s := &Store{DB: db}
	if err = s.CheckSchema(ctx); err != nil {
		db.Close()
		return nil, err
	}
	return s, nil
}

func (s *Store) CheckSchema(ctx context.Context) error {
	var version int
	err := s.DB.QueryRow(ctx, "SELECT COALESCE(MAX(version),0) FROM schema_migrations").Scan(&version)
	if err != nil {
		return fmt.Errorf("schema belum dimigrasikan: %w", err)
	}
	if version != SchemaVersion {
		return fmt.Errorf("versi schema %d tidak kompatibel; dibutuhkan %d", version, SchemaVersion)
	}
	return nil
}

func (s *Store) WorkerHeartbeat(ctx context.Context, workerID string) error {
	_, err := s.DB.Exec(ctx, `INSERT INTO worker_heartbeats(worker_id) VALUES($1)
		ON CONFLICT(worker_id) DO UPDATE SET heartbeat_at=now()`, workerID)
	return err
}

func (s *Store) ClaimCommand(ctx context.Context, owner string, lease time.Duration, maxAttempts int) (*domain.Command, error) {
	tx, err := s.DB.BeginTx(ctx, pgx.TxOptions{})
	if err != nil {
		return nil, err
	}
	defer tx.Rollback(ctx) //nolint:errcheck
	_, err = tx.Exec(ctx, `UPDATE commands SET status='failed',error_code='internal_error',
		error_detail='Batas retry worker tercapai',lease_owner=NULL,lease_until=NULL,finished_at=now(),updated_at=now()
		WHERE status='running' AND lease_until<now() AND attempts >= $1`, maxAttempts)
	if err != nil {
		return nil, err
	}
	_, err = tx.Exec(ctx, `UPDATE jobs j SET status='completed',error_code=c.error_code,
		error_detail=c.error_detail,finished_at=now() FROM commands c
		WHERE j.command_id=c.id AND c.status='failed' AND c.type='search' AND j.status<>'completed'`)
	if err != nil {
		return nil, err
	}
	_, err = tx.Exec(ctx, `UPDATE search_runs s SET status='failed',updated_at=now()
		FROM commands c WHERE c.status='failed' AND c.payload ? 'search_id'
		AND s.id=(c.payload->>'search_id')::uuid AND s.status<>'failed'`)
	if err != nil {
		return nil, err
	}
	row := tx.QueryRow(ctx, `
		SELECT id::text,type,payload,attempts FROM commands
		WHERE attempts < $1 AND (status='queued' OR (status='running' AND lease_until < now()))
		ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1`, maxAttempts)
	var c domain.Command
	if err = row.Scan(&c.ID, &c.Type, &c.Payload, &c.Attempts); err != nil {
		if err == pgx.ErrNoRows {
			return nil, nil
		}
		return nil, err
	}
	_, err = tx.Exec(ctx, `UPDATE commands SET status='running',lease_owner=$2,
		lease_until=now()+$3::interval,attempts=attempts+1,started_at=COALESCE(started_at,now()),updated_at=now()
		WHERE id=$1::uuid`, c.ID, owner, interval(lease))
	if err != nil {
		return nil, err
	}
	if err = tx.Commit(ctx); err != nil {
		return nil, err
	}
	c.Attempts++
	return &c, nil
}

func (s *Store) Heartbeat(ctx context.Context, id, owner string, lease time.Duration) error {
	tag, err := s.DB.Exec(ctx, `UPDATE commands SET lease_until=now()+$3::interval,updated_at=now()
		WHERE id=$1::uuid AND lease_owner=$2 AND status='running'`, id, owner, interval(lease))
	if err != nil {
		return err
	}
	if tag.RowsAffected() != 1 {
		return fmt.Errorf("lease command hilang")
	}
	return nil
}

func (s *Store) HeartbeatItem(ctx context.Context, id int64, owner string, lease time.Duration) error {
	tag, err := s.DB.Exec(ctx, `UPDATE job_items SET lease_until=now()+$3::interval
		WHERE id=$1 AND lease_owner=$2 AND status='running'`, id, owner, interval(lease))
	if err != nil {
		return err
	}
	if tag.RowsAffected() != 1 {
		return fmt.Errorf("lease item hilang")
	}
	return nil
}

func (s *Store) ReserveDomain(ctx context.Context, domain string, delay time.Duration) (time.Duration, error) {
	tx, err := s.DB.BeginTx(ctx, pgx.TxOptions{})
	if err != nil {
		return 0, err
	}
	defer tx.Rollback(ctx) //nolint:errcheck
	var next time.Time
	err = tx.QueryRow(ctx, `SELECT next_allowed_at FROM domain_rate_limits WHERE domain=$1 FOR UPDATE`, domain).Scan(&next)
	if err != nil && err != pgx.ErrNoRows {
		return 0, err
	}
	now := time.Now().UTC()
	start := now
	if err == nil && next.After(now) {
		start = next
	}
	_, err = tx.Exec(ctx, `INSERT INTO domain_rate_limits(domain,next_allowed_at,updated_at)
		VALUES($1,$2,now()) ON CONFLICT(domain) DO UPDATE SET next_allowed_at=EXCLUDED.next_allowed_at,updated_at=now()`, domain, start.Add(delay))
	if err != nil {
		return 0, err
	}
	if err = tx.Commit(ctx); err != nil {
		return 0, err
	}
	return max(0, time.Until(start)), nil
}

func (s *Store) CompleteCommand(ctx context.Context, id, owner string, result any) error {
	payload, err := json.Marshal(result)
	if err != nil {
		return err
	}
	tag, err := s.DB.Exec(ctx, `UPDATE commands SET status='completed',result_json=$3,error_code=NULL,error_detail=NULL,
		lease_owner=NULL,lease_until=NULL,finished_at=now(),updated_at=now()
		WHERE id=$1::uuid AND lease_owner=$2 AND status='running'`, id, owner, payload)
	if err != nil {
		return err
	}
	if tag.RowsAffected() != 1 {
		return fmt.Errorf("lease command hilang")
	}
	return nil
}

func (s *Store) ClaimItem(ctx context.Context, owner string, lease time.Duration, maxAttempts int) (*JobItem, error) {
	tx, err := s.DB.BeginTx(ctx, pgx.TxOptions{})
	if err != nil {
		return nil, err
	}
	defer tx.Rollback(ctx) //nolint:errcheck
	_, err = tx.Exec(ctx, `WITH exhausted AS (
		UPDATE job_items SET status='failed',error_code='internal_error',error_detail='Batas retry worker tercapai',
		lease_owner=NULL,lease_until=NULL,finished_at=now()
		WHERE status='running' AND lease_until<now() AND attempts >= $1 RETURNING job_id)
		UPDATE jobs j SET completed=x.completed,succeeded=x.succeeded,failed=x.failed,
		status=CASE WHEN x.completed=j.total THEN 'completed' ELSE 'running' END,
		finished_at=CASE WHEN x.completed=j.total THEN now() ELSE NULL END FROM
		(SELECT i.job_id,COUNT(*) FILTER(WHERE i.status IN('success','failed')) completed,
		COUNT(*) FILTER(WHERE i.status='success') succeeded,COUNT(*) FILTER(WHERE i.status='failed') failed
		FROM job_items i WHERE i.job_id IN(SELECT job_id FROM exhausted) GROUP BY i.job_id)x WHERE j.id=x.job_id`, maxAttempts)
	if err != nil {
		return nil, err
	}
	var item JobItem
	err = tx.QueryRow(ctx, `SELECT id,job_id::text,url FROM job_items
		WHERE attempts < $1 AND (status='queued' OR (status='running' AND lease_until < now()))
		ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1`, maxAttempts).Scan(&item.ID, &item.JobID, &item.URL)
	if err != nil {
		if err == pgx.ErrNoRows {
			return nil, nil
		}
		return nil, err
	}
	_, err = tx.Exec(ctx, `UPDATE job_items SET status='running',lease_owner=$2,lease_until=now()+$3::interval,
		attempts=attempts+1,started_at=COALESCE(started_at,now()) WHERE id=$1`, item.ID, owner, interval(lease))
	if err != nil {
		return nil, err
	}
	_, err = tx.Exec(ctx, `UPDATE jobs SET status='running',started_at=COALESCE(started_at,now()) WHERE id=$1::uuid`, item.JobID)
	if err != nil {
		return nil, err
	}
	if err = tx.Commit(ctx); err != nil {
		return nil, err
	}
	return &item, nil
}

func (s *Store) FinishItem(ctx context.Context, item JobItem, owner, normalized, host string, article domain.Article, workErr *domain.WorkerError, maxAttempts int) error {
	tx, err := s.DB.BeginTx(ctx, pgx.TxOptions{})
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx) //nolint:errcheck
	status, code, detail := "success", "", ""
	var payload []byte
	dateStatus, dateBasis := "not_filtered", ""
	var included *bool
	if workErr != nil {
		status = "failed"
		code = workErr.Code
		detail = workErr.Detail
		if workErr.Retryable {
			var attempts int
			if err = tx.QueryRow(ctx, "SELECT attempts FROM job_items WHERE id=$1", item.ID).Scan(&attempts); err != nil {
				return err
			}
			if attempts < maxAttempts {
				status = "queued"
			}
		}
	} else if payload, err = json.Marshal(article); err != nil {
		return err
	}
	if status != "queued" {
		dateStatus, included, dateBasis, err = s.classifyDate(ctx, tx, item.JobID, article)
		if err != nil {
			return err
		}
	}
	result, err := tx.Exec(ctx, `UPDATE job_items SET status=$3,normalized_url=NULLIF($4,''),domain=NULLIF($5,''),
		error_code=NULLIF($6,''),error_detail=NULLIF($7,''),article_json=$8,lease_owner=NULL,lease_until=NULL,
		finished_at=CASE WHEN $3='queued' THEN NULL ELSE now() END,date_status=$9,included=$10,
		date_basis=NULLIF($11,'') WHERE id=$1 AND lease_owner=$2`, item.ID, owner, status, normalized, host, code, detail, payload, dateStatus, included, dateBasis)
	if err != nil {
		return err
	}
	if result.RowsAffected() != 1 {
		return fmt.Errorf("lease item %d tidak lagi dimiliki worker %s", item.ID, owner)
	}
	if status != "queued" {
		_, err = tx.Exec(ctx, `UPDATE jobs SET completed=completed+1,
			succeeded=succeeded+CASE WHEN $2='success' THEN 1 ELSE 0 END,
			failed=failed+CASE WHEN $2='failed' THEN 1 ELSE 0 END,
			status=CASE WHEN completed+1=total THEN 'completed' ELSE 'running' END,
			finished_at=CASE WHEN completed+1=total THEN now() ELSE NULL END
			WHERE id=$1::uuid`, item.JobID, status)
		if err != nil {
			return err
		}
	}
	return tx.Commit(ctx)
}

func (s *Store) classifyDate(ctx context.Context, tx pgx.Tx, jobID string, article domain.Article) (string, *bool, string, error) {
	var raw []byte
	err := tx.QueryRow(ctx, "SELECT search_context FROM jobs WHERE id=$1::uuid", jobID).Scan(&raw)
	if err != nil {
		return "", nil, "", err
	}
	if len(raw) == 0 {
		return "not_filtered", nil, "", nil
	}
	var context struct {
		DateFilter *struct {
			StartDate string `json:"start_date"`
			EndDate   string `json:"end_date"`
			Timezone  string `json:"timezone"`
		} `json:"date_filter"`
	}
	if json.Unmarshal(raw, &context) != nil || context.DateFilter == nil {
		return "not_filtered", nil, "", nil
	}
	start, e1 := time.Parse("2006-01-02", context.DateFilter.StartDate)
	end, e2 := time.Parse("2006-01-02", context.DateFilter.EndDate)
	if e1 != nil || e2 != nil {
		return "", nil, "", fmt.Errorf("date_filter job tidak valid")
	}
	var published time.Time
	basis := "source_date"
	if article.PublicationTime.UTC != nil {
		locations := map[string]*time.Location{"UTC": time.UTC, "Asia/Jakarta": time.FixedZone("WIB", 7*3600), "Asia/Makassar": time.FixedZone("WITA", 8*3600), "Asia/Jayapura": time.FixedZone("WIT", 9*3600)}
		location := locations[context.DateFilter.Timezone]
		if location == nil {
			location = time.UTC
		}
		published = article.PublicationTime.UTC.In(location)
		basis = "report_timezone"
	} else if article.PublicationTime.Date != nil {
		published, _ = time.Parse("2006-01-02", *article.PublicationTime.Date)
	} else {
		v := false
		return "unknown", &v, "", nil
	}
	day := time.Date(published.Year(), published.Month(), published.Day(), 0, 0, 0, 0, time.UTC)
	v := !day.Before(start) && !day.After(end)
	if v {
		return "in_range", &v, basis, nil
	}
	return "out_of_range", &v, basis, nil
}

func (s *Store) FailCommand(ctx context.Context, id, owner, code, detail string, retry bool, maxAttempts int) error {
	tx, err := s.DB.BeginTx(ctx, pgx.TxOptions{})
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx) //nolint:errcheck
	result, err := tx.Exec(ctx, `UPDATE commands SET status=CASE WHEN $5 AND attempts < $6 THEN 'queued' ELSE 'failed' END,
		error_code=$3,error_detail=$4,lease_owner=NULL,lease_until=NULL,
		finished_at=CASE WHEN $5 AND attempts < $6 THEN NULL ELSE now() END,updated_at=now()
		WHERE id=$1::uuid AND lease_owner=$2`, id, owner, code, detail, retry, maxAttempts)
	if err != nil {
		return err
	}
	if result.RowsAffected() != 1 {
		return fmt.Errorf("lease command %s tidak lagi dimiliki worker %s", id, owner)
	}
	// A regular search owns a placeholder job. On its terminal failure, expose
	// the safe provider error through that job instead of leaving it queued.
	_, err = tx.Exec(ctx, `UPDATE jobs j SET status='completed',error_code=$2,error_detail=$3,
		finished_at=now() FROM commands c WHERE c.id=$1::uuid AND j.command_id=c.id
		AND c.type='search' AND c.status='failed'`, id, code, detail)
	if err != nil {
		return err
	}
	_, err = tx.Exec(ctx, `UPDATE search_runs s SET status='failed',updated_at=now()
		FROM commands c WHERE c.id=$1::uuid AND c.status='failed'
		AND c.payload ? 'search_id' AND s.id=(c.payload->>'search_id')::uuid`, id)
	if err != nil {
		return err
	}
	return tx.Commit(ctx)
}

func interval(d time.Duration) string { return fmt.Sprintf("%f seconds", d.Seconds()) }
