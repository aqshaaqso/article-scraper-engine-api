package main

import (
	"context"
	"log"
	"os"
	"strings"
	"time"

	"github.com/jackc/pgx/v5"
)

func main() {
	databaseURL := strings.TrimSpace(os.Getenv("DATABASE_URL"))
	workerID := strings.TrimSpace(os.Getenv("WORKER_ID"))
	if workerID == "" {
		host, _ := os.Hostname()
		workerID = host + "-worker"
	}
	if databaseURL == "" {
		log.Fatal("DATABASE_URL wajib diisi")
	}
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	db, err := pgx.Connect(ctx, databaseURL)
	if err != nil {
		log.Fatal(err)
	}
	defer db.Close(ctx)
	var ok bool
	if err = db.QueryRow(ctx, "SELECT heartbeat_at > now()-interval '30 seconds' FROM worker_heartbeats WHERE worker_id=$1", workerID).Scan(&ok); err != nil || !ok {
		log.Fatal("worker heartbeat tidak sehat")
	}
}
