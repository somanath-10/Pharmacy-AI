"""Structured logging configuration."""
import logging
import sys


class JsonFormatter(logging.Formatter):
    def format(self, record):
        import json

        base = {
            "ts": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            base["exc"] = self.formatException(record.exc_info)[:1000]
        return json.dumps(base, default=str)


def setup_logging():
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root.handlers = [handler]
    logging.getLogger("uvicorn.access").handlers = [handler]
