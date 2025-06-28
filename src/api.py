"""API layer for PrismSSH web interface."""

import json
from typing import Dict, Any

# Handle imports - try relative first, then absolute
try:
    from .config import Config
    from .logger import Logger
    from .session_manager import SSHSessionManager
    from .connection_store import ConnectionStore
    from .exceptions import PrismSSHError
except ImportError:
    from config import Config
    from logger import Logger
    from session_manager import SSHSessionManager
    from connection_store import ConnectionStore
    from exceptions import PrismSSHError


class PrismSSHAPI:
    """API exposed to JavaScript frontend."""
    
    def __init__(self, config: Config):
        self.config = config
        self.logger = Logger.get_logger(__name__)
        self.session_manager = SSHSessionManager(config)
        self.connection_store = ConnectionStore(config)
        
        self.logger.info("PrismSSH API initialized")
    
    def create_session(self) -> str:
        """Create a new SSH session."""
        try:
            session_id = self.session_manager.create_session()
            self.logger.info(f"API: Created session {session_id}")
            return session_id
        except Exception as e:
            self.logger.error(f"API: Failed to create session: {e}")
            raise PrismSSHError(f"Failed to create session: {str(e)}")
    
    def connect(self, session_id: str, connection_params: str) -> str:
        """Connect to SSH server."""
        try:
            params = json.loads(connection_params)
            self.logger.info(f"API: Connecting session {session_id} to {params.get('hostname')}")
            
            # Validate required parameters
            required_fields = ['hostname', 'username']
            for field in required_fields:
                if not params.get(field):
                    return json.dumps({
                        'success': False, 
                        'error': f'Missing required field: {field}'
                    })
            
            # Save connection if requested
            if params.get('save', False):
                save_data = {
                    'hostname': params['hostname'],
                    'port': params.get('port', self.config.default_port),
                    'username': params['username'],
                    'password': params.get('password'),
                    'keyPath': params.get('keyPath'),
                    'name': params.get('name', f"{params['username']}@{params['hostname']}")
                }
                
                save_result = self.connection_store.save_connection(save_data)
                if not save_result:
                    self.logger.warning("Failed to save connection")
            
            success = self.session_manager.connect_session(session_id, params)
            
            result = {'success': success}
            if success:
                self.logger.info(f"API: Session {session_id} connected successfully")
            else:
                self.logger.error(f"API: Session {session_id} connection failed")
                result['error'] = 'Connection failed'
            
            return json.dumps(result)
            
        except json.JSONDecodeError as e:
            self.logger.error(f"API: Invalid JSON in connection params: {e}")
            return json.dumps({'success': False, 'error': 'Invalid connection parameters'})
        except Exception as e:
            self.logger.error(f"API: Connection error for session {session_id}: {e}")
            return json.dumps({'success': False, 'error': str(e)})
    
    def get_saved_connections(self) -> str:
        """Get all saved connections."""
        try:
            connections = self.connection_store.load_connections()
            # Convert to list format for frontend
            connection_list = []
            for key, conn in connections.items():
                conn['key'] = key
                connection_list.append(conn)
            
            self.logger.debug(f"API: Returning {len(connection_list)} saved connections")
            return json.dumps(connection_list)
        except Exception as e:
            self.logger.error(f"API: Error loading saved connections: {e}")
            return json.dumps([])
    
    def delete_saved_connection(self, key: str) -> str:
        """Delete a saved connection."""
        try:
            success = self.connection_store.delete_connection(key)
            self.logger.info(f"API: Deleted connection {key}: {success}")
            return json.dumps({'success': success})
        except Exception as e:
            self.logger.error(f"API: Error deleting connection {key}: {e}")
            return json.dumps({'success': False, 'error': str(e)})
    
    def send_input(self, session_id: str, data: str) -> str:
        """Send input to terminal."""
        try:
            success = self.session_manager.send_input(session_id, data)
            return json.dumps({'success': success})
        except Exception as e:
            self.logger.error(f"API: Error sending input to session {session_id}: {e}")
            return json.dumps({'success': False, 'error': str(e)})
    
    def get_output(self, session_id: str) -> str:
        """Get terminal output."""
        try:
            output = self.session_manager.get_output(session_id)
            return json.dumps({'output': output or ''})
        except Exception as e:
            self.logger.error(f"API: Error getting output from session {session_id}: {e}")
            return json.dumps({'output': ''})
    
    def resize_terminal(self, session_id: str, cols: int, rows: int) -> str:
        """Resize terminal."""
        try:
            self.session_manager.resize_terminal(session_id, cols, rows)
            return json.dumps({'success': True})
        except Exception as e:
            self.logger.error(f"API: Error resizing terminal for session {session_id}: {e}")
            return json.dumps({'success': False, 'error': str(e)})
    
    def disconnect(self, session_id: str) -> str:
        """Disconnect session."""
        try:
            self.session_manager.disconnect_session(session_id)
            self.logger.info(f"API: Disconnected session {session_id}")
            return json.dumps({'success': True})
        except Exception as e:
            self.logger.error(f"API: Error disconnecting session {session_id}: {e}")
            return json.dumps({'success': False, 'error': str(e)})
    
    def get_status(self, session_id: str) -> str:
        """Get session status."""
        try:
            status = self.session_manager.get_session_status(session_id)
            return json.dumps(status)
        except Exception as e:
            self.logger.error(f"API: Error getting status for session {session_id}: {e}")
            return json.dumps({'connected': False, 'id': session_id})
    
    # SFTP Methods
    def list_directory(self, session_id: str, path: str) -> str:
        """List directory contents via SFTP."""
        try:
            session = self.session_manager.get_session(session_id)
            if not session:
                return json.dumps({'success': False, 'error': 'Session not found'})
            
            files = session.list_directory(path)
            return json.dumps({'success': True, 'files': files})
        except Exception as e:
            self.logger.error(f"API: Error listing directory {path} for session {session_id}: {e}")
            return json.dumps({'success': False, 'error': str(e)})
    
    def download_file(self, session_id: str, remote_path: str, local_path: str) -> str:
        """Download a file via SFTP."""
        try:
            session = self.session_manager.get_session(session_id)
            if not session:
                return json.dumps({'success': False, 'error': 'Session not found'})
            
            success = session.download_file(remote_path, local_path)
            return json.dumps({'success': success})
        except Exception as e:
            self.logger.error(f"API: Error downloading file {remote_path}: {e}")
            return json.dumps({'success': False, 'error': str(e)})
    
    def upload_file(self, session_id: str, local_path: str, remote_path: str) -> str:
        """Upload a file via SFTP."""
        try:
            session = self.session_manager.get_session(session_id)
            if not session:
                return json.dumps({'success': False, 'error': 'Session not found'})
            
            success = session.upload_file(local_path, remote_path)
            return json.dumps({'success': success})
        except Exception as e:
            self.logger.error(f"API: Error uploading file {local_path}: {e}")
            return json.dumps({'success': False, 'error': str(e)})
    
    def create_directory(self, session_id: str, path: str) -> str:
        """Create a directory via SFTP."""
        try:
            session = self.session_manager.get_session(session_id)
            if not session:
                return json.dumps({'success': False, 'error': 'Session not found'})
            
            success = session.create_directory(path)
            return json.dumps({'success': success})
        except Exception as e:
            self.logger.error(f"API: Error creating directory {path}: {e}")
            return json.dumps({'success': False, 'error': str(e)})
    
    def cleanup(self):
        """Cleanup resources on shutdown."""
        self.logger.info("API: Cleaning up resources")
        self.session_manager.disconnect_all()