USE ai_factoryops;

CREATE TABLE IF NOT EXISTS dlq_messages (
  id BIGINT AUTO_INCREMENT PRIMARY KEY,
  timestamp DATETIME(6) NOT NULL,
  error_type VARCHAR(128) NOT NULL,
  error_message TEXT NOT NULL,
  retry_count INT NOT NULL DEFAULT 0,
  original_payload JSON NOT NULL,
  status VARCHAR(32) NOT NULL DEFAULT 'pending',
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE INDEX idx_dlq_messages_created_at ON dlq_messages(created_at);
CREATE INDEX idx_dlq_messages_error_type ON dlq_messages(error_type);
CREATE INDEX idx_dlq_messages_status_created ON dlq_messages(status, created_at);
