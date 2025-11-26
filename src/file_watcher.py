"""File watcher for monitoring edited files and auto-syncing to server."""

import threading
import time
import os
from typing import Dict, Callable, Optional
from pathlib import Path

# Handle imports - try relative first, then absolute
try:
    from .logger import Logger
except ImportError:
    from logger import Logger


class FileWatcher:
    """Watches temporary files for changes and triggers sync callbacks."""
    
    def __init__(self, sync_callback: Callable[[str], None]):
        self.sync_callback = sync_callback
        self.logger = Logger.get_logger(__name__)
        self.watched_files: Dict[str, float] = {}  # path -> last_mtime
        self.running = False
        self.thread: Optional[threading.Thread] = None
        self.check_interval = 2.0  # Check every 2 seconds
    
    def start(self):
        """Start the file watcher thread."""
        if self.running:
            return
        
        self.running = True
        self.thread = threading.Thread(target=self._watch_loop, daemon=True)
        self.thread.start()
        self.logger.info("File watcher started")
    
    def stop(self):
        """Stop the file watcher thread."""
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=1)
        self.logger.info("File watcher stopped")
    
    def add_file(self, file_path: str):
        """Add a file to watch for changes."""
        try:
            if os.path.exists(file_path):
                self.watched_files[file_path] = os.path.getmtime(file_path)
                self.logger.info(f"Watching file: {file_path}")
            else:
                self.logger.warning(f"Cannot watch non-existent file: {file_path}")
        except Exception as e:
            self.logger.error(f"Error adding file to watch: {e}")
    
    def remove_file(self, file_path: str):
        """Remove a file from watching."""
        if file_path in self.watched_files:
            del self.watched_files[file_path]
            self.logger.info(f"Stopped watching file: {file_path}")
    
    def _watch_loop(self):
        """Main watch loop that checks for file changes."""
        while self.running:
            try:
                self._check_files()
                time.sleep(self.check_interval)
            except Exception as e:
                self.logger.error(f"Error in file watcher loop: {e}")
                time.sleep(self.check_interval)
    
    def _check_files(self):
        """Check all watched files for modifications."""
        files_to_remove = []
        
        for file_path, last_mtime in list(self.watched_files.items()):
            try:
                if not os.path.exists(file_path):
                    # File was deleted, remove from watch list
                    files_to_remove.append(file_path)
                    continue
                
                current_mtime = os.path.getmtime(file_path)
                if current_mtime > last_mtime:
                    # File was modified
                    self.watched_files[file_path] = current_mtime
                    self.logger.info(f"File changed: {file_path}")
                    
                    # Trigger sync callback
                    try:
                        self.sync_callback(file_path)
                    except Exception as e:
                        self.logger.error(f"Error in sync callback for {file_path}: {e}")
                
            except Exception as e:
                self.logger.error(f"Error checking file {file_path}: {e}")
        
        # Remove deleted files from watch list
        for file_path in files_to_remove:
            self.remove_file(file_path)