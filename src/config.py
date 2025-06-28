"""Configuration management for PrismSSH."""

import os
from typing import Dict, Any
from pathlib import Path


class Config:
    """Centralized configuration management."""
    
    def __init__(self):
        self.app_name = "prismssh"
        self.config_dir = Path.home() / f".{self.app_name}"
        self.connections_file = self.config_dir / "connections.json"
        self.key_file = self.config_dir / ".key"
        self.log_file = self.config_dir / "prismssh.log"
        
        # Default settings
        self.default_port = 22
        self.encryption_key_iterations = 100000
        self.config_dir_permissions = 0o700
        self.key_file_permissions = 0o600
        
        # Terminal settings
        self.terminal_font_size = 14
        self.terminal_font_family = 'Consolas, "Courier New", monospace'
        self.terminal_scrollback = 10000
        self.output_poll_interval = 50  # milliseconds
        
        # Window settings
        self.window_width = 1200
        self.window_height = 800
        self.window_min_width = 800
        self.window_min_height = 600
        
        # Connection timeout settings
        self.connection_timeout = 30
        self.keepalive_interval = 30
        
    def ensure_config_dir(self) -> bool:
        """Ensure configuration directory exists."""
        try:
            self.config_dir.mkdir(mode=self.config_dir_permissions, exist_ok=True)
            return True
        except Exception as e:
            print(f"Error creating config directory: {e}")
            return False
    
    def get_app_title(self) -> str:
        """Get application title."""
        return "PrismSSH - Modern SSH Client"