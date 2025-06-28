"""Connection storage and encryption management for PrismSSH."""

import json
import os
import base64
from typing import Dict, Any, Optional
from pathlib import Path

# Handle imports - try relative first, then absolute
try:
    from .config import Config
    from .logger import Logger
    from .exceptions import EncryptionError, ConfigurationError
except ImportError:
    from config import Config
    from logger import Logger
    from exceptions import EncryptionError, ConfigurationError

try:
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    ENCRYPTION_AVAILABLE = True
except ImportError:
    ENCRYPTION_AVAILABLE = False


class ConnectionStore:
    """Manages saved SSH connections with optional encrypted password storage."""
    
    def __init__(self, config: Config):
        self.config = config
        self.logger = Logger.get_logger(__name__)
        self.cipher = self._get_cipher() if ENCRYPTION_AVAILABLE else None
        
        if not ENCRYPTION_AVAILABLE:
            self.logger.warning(
                "Cryptography package not installed. Passwords will be stored in plain text. "
                "Install with: pip install cryptography"
            )
        
        self._ensure_config_dir()
    
    def _ensure_config_dir(self) -> bool:
        """Create config directory if it doesn't exist."""
        try:
            self.config.config_dir.mkdir(
                mode=self.config.config_dir_permissions, 
                parents=True, 
                exist_ok=True
            )
            return True
        except Exception as e:
            self.logger.error(f"Error creating config directory: {e}")
            raise ConfigurationError(f"Failed to create config directory: {e}")
    
    def _get_cipher(self) -> Optional[Fernet]:
        """Get or create encryption cipher for passwords."""
        if not ENCRYPTION_AVAILABLE:
            return None
            
        try:
            if self.config.key_file.exists():
                with open(self.config.key_file, 'rb') as f:
                    key = f.read()
            else:
                # Generate a new key
                salt = os.urandom(16)
                kdf = PBKDF2HMAC(
                    algorithm=hashes.SHA256(),
                    length=32,
                    salt=salt,
                    iterations=self.config.encryption_key_iterations,
                )
                key = base64.urlsafe_b64encode(kdf.derive(b"prismssh-local-key"))
                
                with open(self.config.key_file, 'wb') as f:
                    os.chmod(self.config.key_file, self.config.key_file_permissions)
                    f.write(key)
            
            return Fernet(key)
        except Exception as e:
            self.logger.error(f"Error setting up encryption: {e}")
            raise EncryptionError(f"Failed to setup encryption: {e}")
    
    def save_connection(self, connection: Dict[str, Any]) -> bool:
        """Save a connection profile."""
        try:
            connections = self.load_connections()
            
            # Encrypt password if encryption is available and password exists
            if self.cipher and connection.get('password'):
                try:
                    connection['password'] = self.cipher.encrypt(
                        connection['password'].encode()
                    ).decode()
                    connection['password_encrypted'] = True
                except Exception as e:
                    self.logger.error(f"Error encrypting password: {e}")
                    # Store in plain text if encryption fails
                    connection['password_encrypted'] = False
            
            # Use hostname@username as key
            key = f"{connection['hostname']}@{connection['username']}"
            connections[key] = connection
            
            # Ensure directory exists before writing
            self._ensure_config_dir()
            
            with open(self.config.connections_file, 'w') as f:
                json.dump(connections, f, indent=2)
                
            self.logger.info(f"Connection saved: {key}")
            return True
            
        except Exception as e:
            self.logger.error(f"Error saving connection: {e}")
            return False
    
    def load_connections(self) -> Dict[str, Any]:
        """Load all saved connections."""
        if not self.config.connections_file.exists():
            return {}
        
        try:
            with open(self.config.connections_file, 'r') as f:
                connections = json.load(f)
            
            # Decrypt passwords if cipher is available
            for key, conn in connections.items():
                if conn.get('password_encrypted') and conn.get('password') and self.cipher:
                    try:
                        conn['password'] = self.cipher.decrypt(
                            conn['password'].encode()
                        ).decode()
                    except Exception as e:
                        self.logger.error(f"Error decrypting password for {key}: {e}")
                        # If decryption fails, remove the password
                        conn['password'] = ''
                    conn.pop('password_encrypted', None)
                elif conn.get('password_encrypted') and not self.cipher:
                    # Encrypted password but no cipher available
                    self.logger.warning(
                        f"Cannot decrypt password for {key} (install cryptography package)"
                    )
                    conn['password'] = ''
                    conn.pop('password_encrypted', None)
            
            return connections
        except Exception as e:
            self.logger.error(f"Error loading connections: {e}")
            return {}
    
    def delete_connection(self, key: str) -> bool:
        """Delete a saved connection."""
        try:
            connections = self.load_connections()
            if key in connections:
                del connections[key]
                with open(self.config.connections_file, 'w') as f:
                    json.dump(connections, f, indent=2)
                self.logger.info(f"Connection deleted: {key}")
                return True
            else:
                self.logger.warning(f"Connection not found: {key}")
                return False
        except Exception as e:
            self.logger.error(f"Error deleting connection: {e}")
            return False
    
    def get_connection(self, key: str) -> Optional[Dict[str, Any]]:
        """Get a specific connection by key."""
        connections = self.load_connections()
        return connections.get(key)