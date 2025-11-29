"""Logging configuration for PrismSSH."""

import logging
import sys
from pathlib import Path
from typing import Optional


class Logger:
    """Centralized logging management."""

    def __init__(self, log_file: Optional[str] = None, log_level: int = logging.INFO):
        self.log_file = log_file
        self.log_level = log_level
        self._setup_logging()

    def _setup_logging(self):
        """Setup logging configuration."""
        # Create formatter
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )

        # Setup root logger
        root_logger = logging.getLogger()
        root_logger.setLevel(self.log_level)

        # Clear existing handlers
        root_logger.handlers.clear()

        # Console handler
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(self.log_level)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)

        # File handler if log file is specified
        if self.log_file:
            try:
                # Ensure log directory exists
                log_path = Path(self.log_file)
                log_path.parent.mkdir(parents=True, exist_ok=True)

                file_handler = logging.FileHandler(self.log_file)
                file_handler.setLevel(self.log_level)
                file_handler.setFormatter(formatter)
                root_logger.addHandler(file_handler)
            except Exception as e:
                print(f"Warning: Could not setup file logging: {e}")
    
    @staticmethod
    def get_logger(name: str) -> logging.Logger:
        """Get logger for specific module."""
        return logging.getLogger(name)