CREATE USER IF NOT EXISTS 'copilot_reader'@'%' IDENTIFIED BY 'copilot_readonly_pass';
GRANT SELECT ON ai_factoryops.* TO 'copilot_reader'@'%';
FLUSH PRIVILEGES;
