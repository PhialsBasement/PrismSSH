"""SSH session management for PrismSSH."""

import threading
import queue
import time
import stat
from typing import Dict, Any, Optional, List

# Handle imports - try relative first, then absolute
try:
    from .config import Config
    from .logger import Logger
    from .ssh_client import SSHClient
    from .exceptions import SessionError, SFTPError
except ImportError:
    from config import Config
    from logger import Logger
    from ssh_client import SSHClient
    from exceptions import SessionError, SFTPError


class SSHSession:
    """Represents a single SSH session with terminal and SFTP capabilities."""
    
    def __init__(self, session_id: str, config: Config):
        self.id = session_id
        self.config = config
        self.logger = Logger.get_logger(__name__)
        
        self.client = SSHClient(config)
        self.channel = None
        self.sftp = None
        self.output_queue = queue.Queue()
        self.connected = False
        self.thread = None
        self.running = False
        
        # Connection info
        self.hostname = ""
        self.username = ""
        self.port = 22
        
    def connect(self, hostname: str, port: int, username: str, 
                password: str = None, key_path: str = None) -> bool:
        """Connect to SSH server and start session."""
        try:
            # Store connection info
            self.hostname = hostname
            self.username = username
            self.port = port
            
            # Connect SSH client
            if self.client.connect(hostname, port, username, password, key_path):
                if self.client.open_shell():
                    self.channel = self.client.channel
                    self.connected = True
                    self.running = True
                    
                    # Start the output reading thread
                    self.thread = threading.Thread(target=self._read_output, daemon=True)
                    self.thread.start()
                    
                    # Initialize SFTP
                    try:
                        self.sftp = self.client.get_sftp()
                    except Exception as e:
                        self.logger.warning(f"Failed to initialize SFTP: {e}")
                    
                    self.logger.info(f"Session {self.id} connected to {username}@{hostname}")
                    return True
                else:
                    self.logger.error(f"Failed to open shell for session {self.id}")
            
            return False
            
        except Exception as e:
            self.logger.error(f"Session {self.id} connection failed: {e}")
            raise SessionError(f"Failed to connect session: {str(e)}")
    
    def _read_output(self):
        """Read output from SSH channel in a separate thread."""
        while self.running and self.channel:
            if self.channel.recv_ready():
                try:
                    data = self.channel.recv(4096)
                    if data:
                        self.output_queue.put(data.decode('utf-8', errors='replace'))
                    else:
                        self.running = False
                        self.connected = False
                        self.logger.info(f"Session {self.id} output stream ended")
                        break
                except Exception as e:
                    self.logger.error(f"Error reading output for session {self.id}: {e}")
                    self.running = False
                    self.connected = False
                    break
            
            # Check if connection is still alive
            if not self.client.is_connected():
                self.running = False
                self.connected = False
                self.logger.info(f"Session {self.id} connection lost")
                break
                
            time.sleep(0.01)
    
    def send_input(self, data: str) -> bool:
        """Send input to the SSH channel."""
        if not self.channel or not self.connected:
            self.logger.warning(f"Cannot send input to session {self.id}: not connected")
            return False
            
        try:
            # Check for logout/exit commands
            if self._is_logout_command(data.strip()):
                self.logger.info(f"Session {self.id} logout command detected")
                
            self.channel.send(data.encode('utf-8'))
            return True
        except Exception as e:
            self.logger.error(f"Error sending input to session {self.id}: {e}")
            return False
    
    def _is_logout_command(self, command: str) -> bool:
        """Check if command is a logout/exit command."""
        logout_commands = [
            'exit', 'logout', 'quit', 'bye', 
            'exit\r', 'logout\r', 'quit\r', 'bye\r',
            'exit\n', 'logout\n', 'quit\n', 'bye\n'
        ]
        return command.lower() in logout_commands
    
    def resize(self, cols: int, rows: int):
        """Resize the terminal."""
        if not self.channel:
            return
            
        try:
            self.channel.resize_pty(width=cols, height=rows)
            self.logger.debug(f"Session {self.id} terminal resized to {cols}x{rows}")
        except Exception as e:
            self.logger.error(f"Error resizing terminal for session {self.id}: {e}")
    
    def get_output(self) -> str:
        """Get all pending output."""
        output = []
        while not self.output_queue.empty():
            try:
                output.append(self.output_queue.get_nowait())
            except queue.Empty:
                break
        return ''.join(output)
    
    def list_directory(self, path: str) -> List[Dict[str, Any]]:
        """List files in a directory via SFTP."""
        if not self.sftp:
            raise SFTPError("SFTP not available")
        
        try:
            files = []
            for item in self.sftp.listdir_attr(path):
                file_info = {
                    'name': item.filename,
                    'size': self._format_size(item.st_size),
                    'date': time.strftime('%b %d %H:%M', time.localtime(item.st_mtime)),
                    'type': 'directory' if stat.S_ISDIR(item.st_mode) else 'file',
                    'permissions': stat.filemode(item.st_mode),
                    'raw_size': item.st_size
                }
                files.append(file_info)
            
            # Sort directories first, then files
            files.sort(key=lambda x: (x['type'] != 'directory', x['name'].lower()))
            return files
        except Exception as e:
            self.logger.error(f"Error listing directory {path}: {e}")
            raise SFTPError(f"Failed to list directory: {str(e)}")
    
    def _format_size(self, size: int) -> str:
        """Format file size in human readable format."""
        for unit in ['B', 'K', 'M', 'G', 'T']:
            if size < 1024.0:
                if unit == 'B':
                    return f"{size:.0f}{unit}"
                return f"{size:.1f}{unit}"
            size /= 1024.0
        return f"{size:.1f}P"
    
    def download_file(self, remote_path: str, local_path: str) -> bool:
        """Download a file via SFTP."""
        if not self.sftp:
            raise SFTPError("SFTP not available")
        
        try:
            self.sftp.get(remote_path, local_path)
            self.logger.info(f"Downloaded {remote_path} to {local_path}")
            return True
        except Exception as e:
            self.logger.error(f"Error downloading file {remote_path}: {e}")
            raise SFTPError(f"Failed to download file: {str(e)}")
    
    def upload_file(self, local_path: str, remote_path: str) -> bool:
        """Upload a file via SFTP."""
        if not self.sftp:
            raise SFTPError("SFTP not available")
        
        try:
            self.sftp.put(local_path, remote_path)
            self.logger.info(f"Uploaded {local_path} to {remote_path}")
            return True
        except Exception as e:
            self.logger.error(f"Error uploading file {local_path}: {e}")
            raise SFTPError(f"Failed to upload file: {str(e)}")
    
    def create_directory(self, path: str) -> bool:
        """Create a directory via SFTP."""
        if not self.sftp:
            raise SFTPError("SFTP not available")
        
        try:
            self.sftp.mkdir(path)
            self.logger.info(f"Created directory {path}")
            return True
        except Exception as e:
            self.logger.error(f"Error creating directory {path}: {e}")
            raise SFTPError(f"Failed to create directory: {str(e)}")
    
    def disconnect(self):
        """Disconnect the session."""
        self.logger.info(f"Disconnecting session {self.id}")
        
        self.running = False
        if self.thread and self.thread.is_alive():
            self.thread.join(timeout=1)
        
        if self.sftp:
            try:
                self.sftp.close()
            except Exception as e:
                self.logger.error(f"Error closing SFTP: {e}")
            self.sftp = None
        
        if self.client:
            self.client.close()
        
        self.connected = False
        self.logger.info(f"Session {self.id} disconnected")
    
    def get_status(self) -> Dict[str, Any]:
        """Get session status information."""
        return {
            'id': self.id,
            'connected': self.connected and self.client.is_connected(),
            'hostname': self.hostname,
            'username': self.username,
            'port': self.port
        }