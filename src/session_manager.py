"""Session management for PrismSSH."""

from typing import Dict, Any, Optional

# Handle imports - try relative first, then absolute
try:
    from .config import Config
    from .logger import Logger
    from .session import SSHSession
    from .exceptions import SessionError
except ImportError:
    from config import Config
    from logger import Logger
    from session import SSHSession
    from exceptions import SessionError


class SSHSessionManager:
    """Manages multiple SSH sessions."""
    
    def __init__(self, config: Config):
        self.config = config
        self.logger = Logger.get_logger(__name__)
        self.sessions: Dict[str, SSHSession] = {}
        self.next_id = 1
        self.host_key_verify_callback = None
        self.pending_verifications: Dict[str, Dict[str, str]] = {}
        
    def set_host_key_verify_callback(self, callback):
        """Set the callback for host key verification."""
        self.host_key_verify_callback = callback
        
    def create_session(self) -> str:
        """Create a new session and return its ID."""
        session_id = f"session_{self.next_id}"
        self.next_id += 1
        
        # Pass the host key verification callback to the session
        self.sessions[session_id] = SSHSession(
            session_id, 
            self.config,
            self.host_key_verify_callback
        )
        self.logger.info(f"Created session {session_id}")
        return session_id
    
    def connect_session(self, session_id: str, connection_params: Dict[str, Any]) -> bool:
        """Connect a session with given parameters."""
        if session_id not in self.sessions:
            self.logger.error(f"Session {session_id} not found")
            return False
        
        session = self.sessions[session_id]
        try:
            return session.connect(
                connection_params['hostname'],
                connection_params.get('port', self.config.default_port),
                connection_params['username'],
                connection_params.get('password'),
                connection_params.get('keyPath')
            )
        except Exception as e:
            self.logger.error(f"Failed to connect session {session_id}: {e}")
            return False
    
    def send_input(self, session_id: str, data: str) -> bool:
        """Send input to a session."""
        if session_id not in self.sessions:
            self.logger.error(f"Session {session_id} not found")
            return False
        
        return self.sessions[session_id].send_input(data)
    
    def get_output(self, session_id: str) -> Optional[str]:
        """Get output from a session."""
        if session_id not in self.sessions:
            self.logger.error(f"Session {session_id} not found")
            return None
        
        return self.sessions[session_id].get_output()
    
    def resize_terminal(self, session_id: str, cols: int, rows: int):
        """Resize a terminal."""
        if session_id not in self.sessions:
            self.logger.error(f"Session {session_id} not found")
            return
        
        self.sessions[session_id].resize(cols, rows)
    
    def disconnect_session(self, session_id: str):
        """Disconnect and remove a session."""
        if session_id not in self.sessions:
            self.logger.warning(f"Session {session_id} not found for disconnection")
            return
        
        self.sessions[session_id].disconnect()
        del self.sessions[session_id]
        self.logger.info(f"Session {session_id} removed")
    
    def get_session(self, session_id: str) -> Optional[SSHSession]:
        """Get a session by ID."""
        return self.sessions.get(session_id)
    
    def get_session_status(self, session_id: str) -> Dict[str, Any]:
        """Get status of a session."""
        if session_id not in self.sessions:
            return {'connected': False, 'id': session_id}
        
        return self.sessions[session_id].get_status()
    
    def get_all_sessions(self) -> Dict[str, SSHSession]:
        """Get all active sessions."""
        return self.sessions.copy()
    
    def disconnect_all(self):
        """Disconnect all sessions."""
        self.logger.info("Disconnecting all sessions")
        session_ids = list(self.sessions.keys())
        for session_id in session_ids:
            self.disconnect_session(session_id)