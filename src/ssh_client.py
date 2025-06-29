"""SSH client implementation for PrismSSH."""

import paramiko
import socket
import hashlib
import binascii
from typing import Optional, Dict, Any, Callable
from pathlib import Path

# Handle imports - try relative first, then absolute
try:
    from .config import Config
    from .logger import Logger
    from .exceptions import SSHConnectionError, SSHAuthenticationError
except ImportError:
    from config import Config
    from logger import Logger
    from exceptions import SSHConnectionError, SSHAuthenticationError


class HostKeyPolicy(paramiko.MissingHostKeyPolicy):
    """Custom host key policy that prompts for verification."""
    
    def __init__(self, verify_callback: Callable[[str, str, str], bool]):
        self.verify_callback = verify_callback
    
    def missing_host_key(self, client, hostname, key):
        # Generate fingerprint
        fingerprint = self._get_fingerprint(key)
        key_type = key.get_name()
        
        # Call the verification callback
        if self.verify_callback(hostname, key_type, fingerprint):
            client.get_host_keys().add(hostname, key_type, key)
            return
        else:
            raise SSHConnectionError("Host key verification failed")
    
    def _get_fingerprint(self, key) -> str:
        """Generate SHA256 fingerprint of the key."""
        key_bytes = key.asbytes()
        hash_obj = hashlib.sha256(key_bytes)
        fingerprint = binascii.b2a_base64(hash_obj.digest()).decode('utf-8').strip()
        return f"SHA256:{fingerprint}"


class SSHClient:
    """Core SSH client using Paramiko."""
    
    def __init__(self, config: Config):
        self.config = config
        self.logger = Logger.get_logger(__name__)
        
        self.client = paramiko.SSHClient()
        self.channel: Optional[paramiko.Channel] = None
        self.connected = False
        self.host_key_verify_callback: Optional[Callable] = None
        
        # Load known hosts
        self._load_known_hosts()
    
    def _load_known_hosts(self):
        """Load known hosts file."""
        known_hosts_file = self.config.config_dir / "known_hosts"
        try:
            if known_hosts_file.exists():
                self.client.load_host_keys(str(known_hosts_file))
                self.logger.info("Loaded known hosts file")
            else:
                self.logger.info("No known hosts file found")
        except Exception as e:
            self.logger.warning(f"Error loading known hosts: {e}")
    
    def _save_known_hosts(self):
        """Save known hosts file."""
        known_hosts_file = self.config.config_dir / "known_hosts"
        try:
            # Ensure config directory exists
            self.config.config_dir.mkdir(parents=True, exist_ok=True)
            self.client.save_host_keys(str(known_hosts_file))
            known_hosts_file.chmod(0o600)  # Secure permissions
            self.logger.info("Saved known hosts file")
        except Exception as e:
            self.logger.error(f"Error saving known hosts: {e}")
    
    def set_host_key_verify_callback(self, callback: Callable[[str, str, str], bool]):
        """Set the callback for host key verification."""
        self.host_key_verify_callback = callback
    
    def connect(self, hostname: str, port: int = None, username: str = None, 
                password: str = None, key_filename: str = None) -> bool:
        """Connect to SSH server."""
        # Validate inputs
        if not self._validate_hostname(hostname):
            raise SSHConnectionError("Invalid hostname format")
        
        port = self._validate_port(port or self.config.default_port)
        
        # Set host key policy based on whether we have a callback
        if self.host_key_verify_callback:
            self.client.set_missing_host_key_policy(HostKeyPolicy(self.host_key_verify_callback))
        else:
            # Check if host is already known
            host_keys = self.client.get_host_keys()
            if not host_keys.lookup(hostname):
                self.logger.warning("No host key verification callback set, rejecting unknown host")
                raise SSHConnectionError("Host key verification required for unknown hosts")
        
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
            
            # Log connection attempt without sensitive details
            self.logger.info(f"Connecting to SSH server at {hostname}:{port}")
            self.client.connect(**connect_kwargs)
            
            # Save known hosts after successful connection
            self._save_known_hosts()
            
            # Set up keepalive
            transport = self.client.get_transport()
            if transport:
                transport.set_keepalive(self.config.keepalive_interval)
            
            self.connected = True
            self.logger.info("Successfully connected to SSH server")
            return True
            
        except paramiko.AuthenticationException as e:
            self.logger.error("Authentication failed")
            raise SSHAuthenticationError(f"Authentication failed: {str(e)}")
        except paramiko.SSHException as e:
            self.logger.error("SSH connection failed")
            raise SSHConnectionError(f"SSH connection failed: {str(e)}")
        except socket.error as e:
            self.logger.error("Socket error during connection")
            raise SSHConnectionError(f"Network error: {str(e)}")
        except Exception as e:
            self.logger.error("Unexpected error during connection")
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
            
            # Optimize SFTP for large file transfers
            # Increase window size for better performance
            try:
                transport = self.client.get_transport()
                if transport:
                    # Set much larger window size for better throughput
                    transport.default_window_size = 16777216  # 16MB window
                    transport.packetizer.REKEY_BYTES = pow(2, 40)  # 1TB
                    transport.packetizer.REKEY_PACKETS = pow(2, 40)  # Large number
                    
                    # Set TCP no delay for faster small packets
                    transport.sock.setsockopt(6, 1, 1)  # TCP_NODELAY
                    
                    # Set larger socket buffer sizes
                    try:
                        transport.sock.setsockopt(1, 7, 1048576)  # SO_RCVBUF = 1MB
                        transport.sock.setsockopt(1, 8, 1048576)  # SO_SNDBUF = 1MB
                    except:
                        pass
                    
                # Set SFTP specific optimizations
                if hasattr(sftp, 'MAX_PACKET_SIZE'):
                    sftp.MAX_PACKET_SIZE = 65536  # 64KB packets (maximum)
                    
                # Enable request pipelining for better performance
                if hasattr(sftp, '_set_pipelined'):
                    sftp._set_pipelined(True)
                    
            except Exception as opt_e:
                self.logger.warning(f"Failed to apply SFTP optimizations: {opt_e}")
            
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
    
    def _validate_hostname(self, hostname: str) -> bool:
        """Validate hostname format."""
        import re
        if not hostname or not isinstance(hostname, str):
            return False
        
        # Allow IP addresses
        ip_pattern = r'^(\d{1,3}\.){3}\d{1,3}$'
        if re.match(ip_pattern, hostname):
            # Validate IP octets
            octets = hostname.split('.')
            return all(0 <= int(octet) <= 255 for octet in octets)
        
        # RFC 1123 hostname validation
        hostname_pattern = r'^(?=.{1,253}$)(?!-)[a-zA-Z0-9-]{1,63}(?<!-)(\.[a-zA-Z0-9-]{1,63})*$'
        return bool(re.match(hostname_pattern, hostname))
    
    def _validate_port(self, port: Any) -> int:
        """Validate port number."""
        try:
            port_num = int(port)
            if not 1 <= port_num <= 65535:
                raise SSHConnectionError(f"Port must be between 1 and 65535, got {port_num}")
            return port_num
        except (ValueError, TypeError):
            raise SSHConnectionError(f"Invalid port number: {port}")