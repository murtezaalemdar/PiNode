CREATE TABLE nodes 
                 (id TEXT PRIMARY KEY, name TEXT, port_prefix TEXT, created_at TEXT, auto_restart INTEGER DEFAULT 0, downtime_minutes INTEGER DEFAULT 0);
CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);
