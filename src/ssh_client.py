"""SSH client implementation for PrismSSH."""

import paramiko
import socket
from typing import Optional

# Handle imports - try relative first, then absolute
try:
    from .config import Config
    from .logger import Logger
    from .exceptions import SSHConnectionError, SSHAuthenticationError
except ImportError:
    from config import Config
    from logger import Logger
    from exceptions import SSHConnectionError, SSHAuthenticationError


class SSHClient:
    """Core SSH client using Paramiko."""
    
    def __init__(self, config: Config):
        self.config = config
        self.logger = Logger.get_logger(__name__)
        
        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.channel: Optional[paramiko.Channel] = None
        self.connected = False
    
    def connect(self, hostname: str, port: int = None, username: str = None, 
                password: str = None, key_filename: str = None) -> bool:
        """Connect to SSH server."""
        port = port or self.config.default_port
        
        try:
            connect_kwargs = {
                'hostname': hostname,
                'port': port,
                'username': username,
                'timeout': self.config.connection_timeout,
            }
            
            if password:
                connect_kwargs['password'] = password
            elif key_filename:
                connect_kwargs['key_filename'] = key_filename
            else:
                # Try to use SSH agent or default keys
                connect_kwargs['allow_agent'] = True
                connect_kwargs['look_for_keys'] = True
            
            self.logger.info(f"Connecting to {username}@{hostname}:{port}")
            self.client.connect(**connect_kwargs)
            
            # Set up keepalive
            transport = self.client.get_transport()
            if transport:
                transport.set_keepalive(self.config.keepalive_interval)
            
            self.connected = True
            self.logger.info(f"Successfully connected to {hostname}")
            return True
            
        except paramiko.AuthenticationException as e:
            self.logger.error(f"Authentication failed for {username}@{hostname}: {e}")
            raise SSHAuthenticationError(f"Authentication failed: {str(e)}")
        except paramiko.SSHException as e:
            self.logger.error(f"SSH connection failed to {hostname}: {e}")
            raise SSHConnectionError(f"SSH connection failed: {str(e)}")
        except socket.error as e:
            self.logger.error(f"Socket error connecting to {hostname}: {e}")
            raise SSHConnectionError(f"Network error: {str(e)}")
        except Exception as e:
            self.logger.error(f"Unexpected error connecting to {hostname}: {e}")
            raise SSHConnectionError(f"Connection error: {str(e)}")
    
    def open_shell(self) -> bool:
        """Open an interactive shell session."""
        if not self.connected:
            self.logger.error("Cannot open shell: not connected")
            return False
            
        try:
            self.channel = self.client.invoke_shell()
            self.channel.settimeout(0.0)
            self.logger.info("Shell session opened")
            return True
        except Exception as e:
            self.logger.error(f"Failed to open shell: {e}")
            return False
    
    def get_sftp(self) -> Optional[paramiko.SFTPClient]:
        """Get SFTP client for file operations."""
        if not self.connected:
            self.logger.error("Cannot create SFTP client: not connected")
            return None
        
        try:
            sftp = self.client.open_sftp()
            self.logger.info("SFTP client created")
            return sftp
        except Exception as e:
            self.logger.error(f"Failed to create SFTP client: {e}")
            return None
    
    def close(self):
        """Close the SSH connection."""
        try:
            if self.channel:
                self.channel.close()
                self.channel = None
            
            if self.client:
                self.client.close()
            
            self.connected = False
            self.logger.info("SSH connection closed")
        except Exception as e:
            self.logger.error(f"Error closing connection: {e}")

    def is_connected(self) -> bool:
        """Check if connection is still active."""
        if not self.connected:
            return False
        
        try:
            transport = self.client.get_transport()
            if transport is None or not transport.is_active():
                self.connected = False
                return False
            
            # Additional check: try to get channel status
            if self.channel and self.channel.closed:
                self.connected = False
                return False
                
            return True
        except Exception:
            self.connected = False
            return False