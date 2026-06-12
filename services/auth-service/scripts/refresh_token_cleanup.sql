-- Cron-friendly cleanup statement for refresh_tokens.
-- Run periodically with a MySQL client, or use the same DELETE in a scheduled event.
DELETE FROM refresh_tokens
WHERE expires_at < NOW() - INTERVAL 1 DAY
   OR revoked_at IS NOT NULL
ORDER BY expires_at ASC, id ASC
LIMIT 10000;
