package main

import (
	"context"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"

	"github.com/jackc/pgx/v5"
)

func main() {
	databaseURL := strings.TrimSpace(os.Getenv("DATABASE_URL"))
	if databaseURL == "" {
		log.Fatal("DATABASE_URL wajib diisi")
	}
	ctx := context.Background()
	db, err := pgx.Connect(ctx, databaseURL)
	if err != nil {
		log.Fatalf("connect database: %v", err)
	}
	defer db.Close(ctx)
	lockKey := int64(7300410031)
	if _, err = db.Exec(ctx, "SELECT pg_advisory_lock($1)", lockKey); err != nil {
		log.Fatalf("migration lock: %v", err)
	}
	defer db.Exec(ctx, "SELECT pg_advisory_unlock($1)", lockKey) //nolint:errcheck
	if _, err = db.Exec(ctx, `CREATE TABLE IF NOT EXISTS schema_migrations (
		version bigint PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())`); err != nil {
		log.Fatalf("initialize migration table: %v", err)
	}

	entries, err := os.ReadDir("migrations")
	if err != nil {
		log.Fatal(err)
	}
	sort.Slice(entries, func(i, j int) bool { return entries[i].Name() < entries[j].Name() })
	for _, entry := range entries {
		if entry.IsDir() || filepath.Ext(entry.Name()) != ".sql" {
			continue
		}
		version, versionErr := migrationVersion(entry.Name())
		if versionErr != nil {
			log.Fatal(versionErr)
		}
		var applied bool
		if err = db.QueryRow(ctx, "SELECT EXISTS(SELECT 1 FROM schema_migrations WHERE version=$1)", version).Scan(&applied); err != nil {
			log.Fatal(err)
		}
		if applied {
			fmt.Printf("skipped %s\n", entry.Name())
			continue
		}
		body, readErr := os.ReadFile(filepath.Join("migrations", entry.Name()))
		if readErr != nil {
			log.Fatal(readErr)
		}
		if _, execErr := db.Exec(ctx, string(body)); execErr != nil {
			log.Fatalf("apply %s: %v", entry.Name(), execErr)
		}
		fmt.Printf("applied %s\n", entry.Name())
	}
}

func migrationVersion(name string) (int64, error) {
	prefix, _, ok := strings.Cut(name, "_")
	if !ok {
		return 0, fmt.Errorf("nama migration %q harus diawali nomor dan underscore", name)
	}
	version, err := strconv.ParseInt(prefix, 10, 64)
	if err != nil || version <= 0 {
		return 0, fmt.Errorf("versi migration %q tidak valid", name)
	}
	return version, nil
}
