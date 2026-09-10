package main

import "testing"

func TestMigrationVersion(t *testing.T) {
	version, err := migrationVersion("004_metrics_permissions.sql")
	if err != nil || version != 4 {
		t.Fatalf("version=%d err=%v", version, err)
	}
	for _, name := range []string{"migration.sql", "000_invalid.sql", "x_bad.sql"} {
		if _, err = migrationVersion(name); err == nil {
			t.Fatalf("expected %s to fail", name)
		}
	}
}
