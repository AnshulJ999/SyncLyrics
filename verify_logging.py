import logging
import logging.handlers
import logging_config
import os

# Setup logging
logging_config.setup_logging()

logger = logging_config.get_logger("test_logger")
logger.info("Test info message")

# Check handlers
root_logger = logging.getLogger()
handlers = root_logger.handlers

result = []
result.append(f"Handlers: {handlers}")

found_rotating = False
for h in handlers:
    if isinstance(h, logging.handlers.RotatingFileHandler):
        result.append(f"Found RotatingFileHandler: {h.baseFilename}, maxBytes={h.maxBytes}, backupCount={h.backupCount}")
        found_rotating = True

if not found_rotating:
    result.append("ERROR: RotatingFileHandler not found!")
else:
    result.append("SUCCESS: RotatingFileHandler found.")

# Check if file exists
log_file = os.path.join(logging_config.LOGS_DIR, "app.log")
if os.path.exists(log_file):
    result.append(f"Log file exists: {log_file}")
else:
    result.append("Log file not found")

with open("verification_result.txt", "w") as f:
    f.write("\n".join(result))
