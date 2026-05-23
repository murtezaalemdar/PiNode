CREATE TABLE IF NOT EXISTS "nodes" (
  "id" TEXT PRIMARY KEY,
  "name" TEXT,
  "port_prefix" TEXT,
  "created_at" TEXT,
  "auto_restart" INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS "settings" (
  "key" TEXT PRIMARY KEY,
  "value" TEXT
);
