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
        
        # Set up host key verification callback
        self.session_manager.set_host_key_verify_callback(self._handle_host_key_verification)
        self.pending_verifications = {}
        
        # Track download progress and cancellation
        self.download_progress = {}
        self.download_cancellations = {}
        
        # Set up file watcher for edited files
        try:
            from .file_watcher import FileWatcher
            self.file_watcher = FileWatcher(self._sync_file_callback)
            self.file_watcher.start()
        except ImportError:
            from file_watcher import FileWatcher
            self.file_watcher = FileWatcher(self._sync_file_callback)
            self.file_watcher.start()

        # Window reference for JS calls (set by main.py)
        self.window = None

        self.logger.info("PrismSSH API initialized")

    def set_window(self, window):
        """Set the webview window reference for JS calls."""
        self.window = window
    
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
    
    def delete_file(self, session_id: str, path: str) -> str:
        """Delete a file via SFTP."""
        try:
            session = self.session_manager.get_session(session_id)
            if not session:
                return json.dumps({'success': False, 'error': 'Session not found'})
            
            success = session.delete_file(path)
            return json.dumps({'success': success})
        except Exception as e:
            self.logger.error(f"API: Error deleting file {path}: {e}")
            return json.dumps({'success': False, 'error': str(e)})
    
    def delete_directory(self, session_id: str, path: str) -> str:
        """Delete a directory via SFTP."""
        try:
            session = self.session_manager.get_session(session_id)
            if not session:
                return json.dumps({'success': False, 'error': 'Session not found'})
            
            success = session.delete_directory(path)
            return json.dumps({'success': success})
        except Exception as e:
            self.logger.error(f"API: Error deleting directory {path}: {e}")
            return json.dumps({'success': False, 'error': str(e)})
    
    def rename_file(self, session_id: str, old_path: str, new_path: str) -> str:
        """Rename/move a file via SFTP."""
        try:
            session = self.session_manager.get_session(session_id)
            if not session:
                return json.dumps({'success': False, 'error': 'Session not found'})
            
            success = session.rename_file(old_path, new_path)
            return json.dumps({'success': success})
        except Exception as e:
            self.logger.error(f"API: Error renaming file {old_path}: {e}")
            return json.dumps({'success': False, 'error': str(e)})
    
    def upload_file_content(self, session_id: str, file_content: str, remote_path: str) -> str:
        """Upload file content via SFTP."""
        try:
            session = self.session_manager.get_session(session_id)
            if not session:
                return json.dumps({'success': False, 'error': 'Session not found'})
            
            # Decode base64 content
            import base64
            file_bytes = base64.b64decode(file_content)
            success = session.upload_file_content(file_bytes, remote_path)
            return json.dumps({'success': success})
        except Exception as e:
            self.logger.error(f"API: Error uploading file content to {remote_path}: {e}")
            return json.dumps({'success': False, 'error': str(e)})
    
    def download_file_content(self, session_id: str, remote_path: str) -> str:
        """Download file content via SFTP."""
        try:
            session = self.session_manager.get_session(session_id)
            if not session:
                return json.dumps({'success': False, 'error': 'Session not found'})
            
            file_bytes = session.download_file_content(remote_path)
            
            # Encode as base64 for transfer
            import base64
            file_content = base64.b64encode(file_bytes).decode('utf-8')
            
            return json.dumps({
                'success': True, 
                'content': file_content,
                'size': len(file_bytes)
            })
        except Exception as e:
            self.logger.error(f"API: Error downloading file content from {remote_path}: {e}")
            return json.dumps({'success': False, 'error': str(e)})
    
    def edit_file(self, session_id: str, remote_path: str) -> str:
        """Download file for editing and return temp file path."""
        try:
            session = self.session_manager.get_session(session_id)
            if not session:
                return json.dumps({'success': False, 'error': 'Session not found'})
            
            # Download file content
            file_bytes = session.download_file_content(remote_path)
            
            # Create temp file
            import tempfile
            import os
            from pathlib import Path
            
            # Get file extension to preserve it
            file_name = Path(remote_path).name
            suffix = Path(file_name).suffix or '.txt'
            
            # Create temp file with proper extension
            temp_fd, temp_path = tempfile.mkstemp(suffix=suffix, prefix=f"prism_edit_{file_name}_")
            
            try:
                # Write content to temp file
                with os.fdopen(temp_fd, 'wb') as temp_file:
                    temp_file.write(file_bytes)
                
                # Store mapping for later upload
                if not hasattr(self, 'edit_mappings'):
                    self.edit_mappings = {}
                
                self.edit_mappings[temp_path] = {
                    'session_id': session_id,
                    'remote_path': remote_path,
                    'original_mtime': os.path.getmtime(temp_path)
                }
                
                # Add file to watcher
                self.file_watcher.add_file(temp_path)

                self.logger.info(f"Created temp file for editing: {temp_path}")

                # Open file in default editor
                self._open_file_in_editor(temp_path)

                return json.dumps({
                    'success': True,
                    'temp_path': temp_path,
                    'file_name': file_name
                })
                
            except Exception as e:
                # Clean up temp file if something went wrong
                try:
                    os.unlink(temp_path)
                except:
                    pass
                raise
                
        except Exception as e:
            self.logger.error(f"API: Error creating temp file for {remote_path}: {e}")
            return json.dumps({'success': False, 'error': str(e)})
    
    def _open_file_in_editor(self, file_path: str):
        """Open a file in the system's default editor and track when it closes."""
        import subprocess
        import platform
        import threading

        system = platform.system().lower()

        def wait_for_editor_and_cleanup():
            """Wait for editor to close, then clean up."""
            try:
                if system == 'windows':
                    # Use 'start /wait' to wait for the editor to close
                    subprocess.run(['cmd', '/c', 'start', '/wait', '', file_path], shell=False)
                elif system == 'darwin':
                    # Use 'open -W' to wait for the application to close
                    subprocess.run(['open', '-W', file_path])
                else:
                    # Use xdg-open, but it doesn't wait, so we can't track easily
                    subprocess.run(['xdg-open', file_path])

                self.logger.info(f"Editor closed for: {file_path}")

                # Clean up after editor closes
                self._cleanup_edit_session(file_path)

            except Exception as e:
                self.logger.error(f"Error waiting for editor: {e}")

        try:
            # Run in background thread so we don't block
            thread = threading.Thread(target=wait_for_editor_and_cleanup, daemon=True)
            thread.start()
            self.logger.info(f"Opened file in editor: {file_path}")
        except Exception as e:
            self.logger.error(f"Failed to open file in editor: {e}")

    def _cleanup_edit_session(self, temp_path: str):
        """Clean up after editing session ends."""
        try:
            import os

            # Final sync before cleanup
            self.sync_edited_file(temp_path)

            # Remove from file watcher
            self.file_watcher.remove_file(temp_path)

            # Remove from mappings
            if hasattr(self, 'edit_mappings') and temp_path in self.edit_mappings:
                del self.edit_mappings[temp_path]

            # Delete temp file
            if os.path.exists(temp_path):
                os.unlink(temp_path)
                self.logger.info(f"Cleaned up edit session: {temp_path}")

        except Exception as e:
            self.logger.error(f"Error cleaning up edit session: {e}")

    def sync_edited_file(self, temp_path: str) -> str:
        """Sync edited temp file back to server."""
        try:
            self.logger.info(f"sync_edited_file called for: {temp_path}")

            if not hasattr(self, 'edit_mappings') or temp_path not in self.edit_mappings:
                self.logger.warning(f"No mapping found for: {temp_path}")
                return json.dumps({'success': False, 'error': 'File mapping not found'})

            mapping = self.edit_mappings[temp_path]
            self.logger.info(f"Found mapping: session={mapping['session_id']}, remote={mapping['remote_path']}")

            session = self.session_manager.get_session(mapping['session_id'])

            if not session:
                self.logger.warning(f"Session not found: {mapping['session_id']}")
                return json.dumps({'success': False, 'error': 'Session not found'})

            import os

            # Check if file was modified
            current_mtime = os.path.getmtime(temp_path)
            self.logger.info(f"mtime check: current={current_mtime}, original={mapping['original_mtime']}")

            if current_mtime <= mapping['original_mtime']:
                self.logger.info("No changes detected (mtime not newer)")
                return json.dumps({'success': True, 'message': 'No changes detected'})

            # Read updated content
            with open(temp_path, 'rb') as f:
                file_bytes = f.read()

            self.logger.info(f"Read {len(file_bytes)} bytes from temp file, uploading to {mapping['remote_path']}")

            # Upload back to server
            success = session.upload_file_content(file_bytes, mapping['remote_path'])

            if success:
                # Update the modification time
                mapping['original_mtime'] = current_mtime
                self.logger.info(f"Successfully synced edited file: {mapping['remote_path']}")

                # Show notification in UI
                self._show_sync_notification(mapping['remote_path'])

                return json.dumps({'success': True, 'message': 'File synced to server'})
            else:
                self.logger.error(f"Upload failed for: {mapping['remote_path']}")
                return json.dumps({'success': False, 'error': 'Failed to upload to server'})

        except Exception as e:
            self.logger.error(f"API: Error syncing edited file {temp_path}: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return json.dumps({'success': False, 'error': str(e)})
    
    def _show_sync_notification(self, remote_path: str):
        """Show sync notification in the UI."""
        try:
            if self.window:
                from pathlib import Path
                file_name = Path(remote_path).name
                self.window.evaluate_js(f'showSyncNotification("{file_name}")')
        except Exception as e:
            self.logger.error(f"Error showing sync notification: {e}")

    def _sync_file_callback(self, temp_path: str):
        """Callback for file watcher when a file is modified."""
        try:
            self.logger.info(f"File watcher detected change in: {temp_path}")
            result = self.sync_edited_file(temp_path)
            response = json.loads(result)

            if response.get('success') and response.get('message') == 'File synced to server':
                self.logger.info(f"Auto-synced file: {temp_path}")
            elif response.get('success'):
                pass  # No changes detected, don't log
            else:
                self.logger.warning(f"Failed to auto-sync file {temp_path}: {response.get('error')}")
                
        except Exception as e:
            self.logger.error(f"Error in file sync callback for {temp_path}: {e}")
    
    def cleanup_temp_file(self, temp_path: str) -> str:
        """Clean up temporary edit file."""
        try:
            import os
            
            # Remove from file watcher
            self.file_watcher.remove_file(temp_path)
            
            # Remove from mappings
            if hasattr(self, 'edit_mappings') and temp_path in self.edit_mappings:
                del self.edit_mappings[temp_path]
            
            # Delete temp file
            if os.path.exists(temp_path):
                os.unlink(temp_path)
                self.logger.info(f"Cleaned up temp file: {temp_path}")
            
            return json.dumps({'success': True})
            
        except Exception as e:
            self.logger.error(f"API: Error cleaning up temp file {temp_path}: {e}")
            return json.dumps({'success': False, 'error': str(e)})
    
    def download_file_to_path(self, session_id: str, remote_path: str, local_path: str) -> str:
        """Download file directly to specified local path with progress tracking."""
        try:
            import time
            session = self.session_manager.get_session(session_id)
            if not session:
                return json.dumps({'success': False, 'error': 'Session not found'})
            
            # Create progress tracking for this direct download
            progress_key = f"{session_id}:direct_{int(time.time())}"
            self.download_progress[progress_key] = {
                'downloaded': 0,
                'total': 0,
                'percentage': 0,
                'status': 'downloading',
                'error': None
            }
            
            def progress_callback(downloaded, total, percentage):
                self.download_progress[progress_key] = {
                    'downloaded': downloaded,
                    'total': total,
                    'percentage': percentage,
                    'status': 'downloading',
                    'error': None
                }
            
            # Use the session's download_file method with progress tracking
            success = session.download_file(remote_path, local_path, progress_callback)
            
            # Clean up progress tracking
            if progress_key in self.download_progress:
                del self.download_progress[progress_key]
            
            if success:
                return json.dumps({'success': True, 'message': f'File downloaded to {local_path}'})
            else:
                return json.dumps({'success': False, 'error': 'Download failed'})
                
        except Exception as e:
            self.logger.error(f"API: Error downloading file {remote_path} to {local_path}: {e}")
            # Clean up progress tracking on error
            if 'progress_key' in locals() and progress_key in self.download_progress:
                del self.download_progress[progress_key]
            return json.dumps({'success': False, 'error': str(e)})
    
    def start_direct_download_with_progress(self, session_id: str, remote_path: str, local_path: str, download_id: str) -> str:
        """Start a direct download to path with REAL progress tracking."""
        try:
            import threading
            
            session = self.session_manager.get_session(session_id)
            if not session:
                return json.dumps({'success': False, 'error': 'Session not found'})
            
            # Initialize progress tracking
            progress_key = f"{session_id}:{download_id}"
            self.download_progress[progress_key] = {
                'downloaded': 0,
                'total': 0,
                'percentage': 0,
                'status': 'starting',
                'error': None
            }
            self.download_cancellations[progress_key] = False
            
            def progress_callback(downloaded, total, percentage):
                # Check for cancellation FIRST before updating progress
                if self.download_cancellations.get(progress_key, False):
                    self.download_progress[progress_key]['status'] = 'cancelled'
                    raise Exception("Download cancelled by user")
                
                self.download_progress[progress_key] = {
                    'downloaded': downloaded,
                    'total': total,
                    'percentage': percentage,
                    'status': 'downloading',
                    'error': None
                }
            
            def download_thread():
                try:
                    self.download_progress[progress_key]['status'] = 'downloading'
                    
                    # Use direct file download - no content transfer through memory
                    success = session.download_file(remote_path, local_path, progress_callback)
                    
                    if not self.download_cancellations.get(progress_key, False):
                        if success:
                            self.download_progress[progress_key].update({
                                'status': 'completed',
                                'percentage': 100
                            })
                        else:
                            self.download_progress[progress_key].update({
                                'status': 'error',
                                'error': 'Download failed'
                            })
                    
                except Exception as e:
                    self.download_progress[progress_key].update({
                        'status': 'error',
                        'error': str(e)
                    })
            
            # Start download in background thread
            thread = threading.Thread(target=download_thread, daemon=True)
            thread.start()
            
            return json.dumps({'success': True, 'download_id': download_id})
            
        except Exception as e:
            self.logger.error(f"API: Error starting direct download: {e}")
            return json.dumps({'success': False, 'error': str(e)})
    
    def show_save_file_dialog(self, filename: str) -> str:
        """Show REAL native OS save file dialog."""
        try:
            import os
            import platform
            from pathlib import Path
            
            # Get file extension for filter
            file_ext = Path(filename).suffix.lower()
            
            # Get default directory
            default_dir = os.path.expanduser('~/Downloads')
            if not os.path.exists(default_dir):
                default_dir = os.path.expanduser('~')
            
            default_path = os.path.join(default_dir, filename)
            
            system = platform.system().lower()
            
            if system == 'windows':
                # Use Windows native dialog
                import tkinter as tk
                from tkinter import filedialog
                
                # Create hidden root window
                root = tk.Tk()
                root.withdraw()
                root.attributes('-topmost', True)
                
                # Set file type filter
                if file_ext:
                    filetypes = [
                        (f'{file_ext.upper()[1:]} files', f'*{file_ext}'),
                        ('All files', '*.*')
                    ]
                else:
                    filetypes = [('All files', '*.*')]
                
                # Show Windows save dialog
                result = filedialog.asksaveasfilename(
                    title=f'Save {filename}',
                    initialfile=filename,
                    initialdir=default_dir,
                    filetypes=filetypes,
                    defaultextension=file_ext if file_ext else ''
                )
                
                root.destroy()
                
                if result:
                    return json.dumps({'success': True, 'path': result})
                else:
                    return json.dumps({'success': False, 'cancelled': True})
                    
            elif system == 'linux':
                # Use Linux native dialog (zenity, kdialog, or tkinter)
                try:
                    # Try zenity first (GNOME)
                    import subprocess
                    
                    cmd = [
                        'zenity', '--file-selection', '--save',
                        '--title', f'Save {filename}',
                        '--filename', default_path
                    ]
                    
                    if file_ext:
                        cmd.extend(['--file-filter', f'{file_ext.upper()[1:]} files | *{file_ext}'])
                        cmd.extend(['--file-filter', 'All files | *'])
                    
                    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
                    
                    if result.returncode == 0 and result.stdout.strip():
                        return json.dumps({'success': True, 'path': result.stdout.strip()})
                    elif result.returncode == 1:  # User cancelled
                        return json.dumps({'success': False, 'cancelled': True})
                    else:
                        raise Exception("Zenity failed")
                        
                except:
                    try:
                        # Try kdialog (KDE)
                        cmd = [
                            'kdialog', '--getsavefilename', default_path,
                            '--title', f'Save {filename}'
                        ]
                        
                        if file_ext:
                            cmd.append(f'*{file_ext}|{file_ext.upper()[1:]} files')
                        
                        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
                        
                        if result.returncode == 0 and result.stdout.strip():
                            return json.dumps({'success': True, 'path': result.stdout.strip()})
                        elif result.returncode == 1:  # User cancelled
                            return json.dumps({'success': False, 'cancelled': True})
                        else:
                            raise Exception("KDialog failed")
                            
                    except:
                        # Fallback to tkinter on Linux
                        import tkinter as tk
                        from tkinter import filedialog
                        
                        root = tk.Tk()
                        root.withdraw()
                        root.attributes('-topmost', True)
                        
                        if file_ext:
                            filetypes = [
                                (f'{file_ext.upper()[1:]} files', f'*{file_ext}'),
                                ('All files', '*.*')
                            ]
                        else:
                            filetypes = [('All files', '*.*')]
                        
                        result = filedialog.asksaveasfilename(
                            title=f'Save {filename}',
                            initialfile=filename,
                            initialdir=default_dir,
                            filetypes=filetypes,
                            defaultextension=file_ext if file_ext else ''
                        )
                        
                        root.destroy()
                        
                        if result:
                            return json.dumps({'success': True, 'path': result})
                        else:
                            return json.dumps({'success': False, 'cancelled': True})
                            
            elif system == 'darwin':
                # Use macOS native dialog
                import subprocess
                
                cmd = [
                    'osascript', '-e',
                    f'''
                    tell application "System Events"
                        set theFile to choose file name with prompt "Save {filename}" default name "{filename}" default location (path to downloads folder)
                        return POSIX path of theFile
                    end tell
                    '''
                ]
                
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
                
                if result.returncode == 0 and result.stdout.strip():
                    return json.dumps({'success': True, 'path': result.stdout.strip()})
                else:
                    return json.dumps({'success': False, 'cancelled': True})
            
            else:
                raise Exception(f"Unsupported platform: {system}")
                
        except Exception as e:
            self.logger.error(f"API: Error showing native save dialog: {e}")
            return json.dumps({
                'success': False, 
                'error': str(e),
                'fallback_needed': True
            })
    
    def start_download_with_progress(self, session_id: str, remote_path: str, download_id: str) -> str:
        """Start a download with progress tracking."""
        try:
            import threading
            
            session = self.session_manager.get_session(session_id)
            if not session:
                return json.dumps({'success': False, 'error': 'Session not found'})
            
            # Initialize progress tracking
            progress_key = f"{session_id}:{download_id}"
            self.download_progress[progress_key] = {
                'downloaded': 0,
                'total': 0,
                'percentage': 0,
                'status': 'starting',
                'error': None
            }
            self.download_cancellations[progress_key] = False
            
            def progress_callback(downloaded, total, percentage):
                # Check for cancellation FIRST before updating progress
                if self.download_cancellations.get(progress_key, False):
                    self.download_progress[progress_key]['status'] = 'cancelled'
                    raise Exception("Download cancelled by user")
                
                self.download_progress[progress_key] = {
                    'downloaded': downloaded,
                    'total': total,
                    'percentage': percentage,
                    'status': 'downloading',
                    'error': None
                }
            
            def download_thread():
                try:
                    self.download_progress[progress_key]['status'] = 'downloading'
                    content = session.download_file_content(remote_path, progress_callback)
                    
                    if not self.download_cancellations.get(progress_key, False):
                        # Encode as base64 for transfer
                        import base64
                        file_content = base64.b64encode(content).decode('utf-8')
                        
                        self.download_progress[progress_key].update({
                            'status': 'completed',
                            'content': file_content,
                            'size': len(content)
                        })
                    
                except Exception as e:
                    self.download_progress[progress_key].update({
                        'status': 'error',
                        'error': str(e)
                    })
            
            # Start download in background thread
            thread = threading.Thread(target=download_thread, daemon=True)
            thread.start()
            
            return json.dumps({'success': True, 'download_id': download_id})
            
        except Exception as e:
            self.logger.error(f"API: Error starting download: {e}")
            return json.dumps({'success': False, 'error': str(e)})
    
    def cancel_download(self, session_id: str, download_id: str) -> str:
        """Cancel an ongoing download."""
        try:
            progress_key = f"{session_id}:{download_id}"
            self.download_cancellations[progress_key] = True
            
            if progress_key in self.download_progress:
                self.download_progress[progress_key]['status'] = 'cancelled'
            
            return json.dumps({'success': True})
        except Exception as e:
            self.logger.error(f"API: Error cancelling download: {e}")
            return json.dumps({'success': False, 'error': str(e)})
    
    def get_download_progress(self, session_id: str, download_id: str) -> str:
        """Get download progress for a file."""
        try:
            progress_key = f"{session_id}:{download_id}"
            progress = self.download_progress.get(progress_key, {})
            return json.dumps(progress)
        except Exception as e:
            self.logger.error(f"API: Error getting download progress: {e}")
            return json.dumps({})
    
    def get_file_info(self, session_id: str, remote_path: str) -> str:
        """Get file information via SFTP."""
        try:
            session = self.session_manager.get_session(session_id)
            if not session:
                return json.dumps({'success': False, 'error': 'Session not found'})
            
            file_info = session.get_file_info(remote_path)
            return json.dumps({'success': True, 'info': file_info})
        except Exception as e:
            self.logger.error(f"API: Error getting file info for {remote_path}: {e}")
            return json.dumps({'success': False, 'error': str(e)})
    
    def get_encryption_status(self) -> str:
        """Get encryption status for frontend warning."""
        try:
            status = self.connection_store.get_encryption_status()
            return json.dumps(status)
        except Exception as e:
            self.logger.error(f"API: Error getting encryption status: {e}")
            return json.dumps({'available': False, 'warning_needed': True})
    
    def mark_encryption_warning_shown(self) -> str:
        """Mark encryption warning as shown."""
        try:
            self.connection_store.mark_encryption_warning_shown()
            return json.dumps({'success': True})
        except Exception as e:
            self.logger.error(f"API: Error marking encryption warning: {e}")
            return json.dumps({'success': False})

    def _handle_host_key_verification(self, hostname: str, key_type: str, fingerprint: str) -> bool:
        """Handle host key verification internally."""
        # Store verification details
        verification_id = f"{hostname}_{key_type}"
        self.pending_verifications[verification_id] = {
            'hostname': hostname,
            'key_type': key_type,
            'fingerprint': fingerprint,
            'verified': False
        }
        
        # Wait for user verification (with timeout)
        import time
        timeout = 60  # 60 seconds timeout
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            if verification_id in self.pending_verifications:
                if self.pending_verifications[verification_id].get('verified'):
                    del self.pending_verifications[verification_id]
                    return True
                elif self.pending_verifications[verification_id].get('rejected'):
                    del self.pending_verifications[verification_id]
                    return False
            time.sleep(0.1)
        
        # Timeout - reject
        if verification_id in self.pending_verifications:
            del self.pending_verifications[verification_id]
        return False
    
    def get_pending_host_verification(self, session_id: str) -> str:
        """Check if there's a pending host key verification."""
        try:
            # Find any pending verification
            for verification_id, details in self.pending_verifications.items():
                if not details.get('verified') and not details.get('rejected'):
                    return json.dumps({
                        'pending': True,
                        'hostname': details['hostname'],
                        'key_type': details['key_type'],
                        'fingerprint': details['fingerprint'],
                        'verification_id': verification_id
                    })
            
            return json.dumps({'pending': False})
        except Exception as e:
            self.logger.error(f"API: Error checking host verification: {e}")
            return json.dumps({'pending': False})
    
    def verify_host_key(self, verification_id: str, accepted: bool) -> str:
        """Verify or reject a host key."""
        try:
            if verification_id in self.pending_verifications:
                if accepted:
                    self.pending_verifications[verification_id]['verified'] = True
                else:
                    self.pending_verifications[verification_id]['rejected'] = True
                
                return json.dumps({'success': True})
            else:
                return json.dumps({'success': False, 'error': 'Verification not found'})
        except Exception as e:
            self.logger.error(f"API: Error verifying host key: {e}")
            return json.dumps({'success': False, 'error': str(e)})
    
    # System Monitor Methods
    def get_system_info(self, session_id: str) -> str:
        """Get basic system information."""
        try:
            session = self.session_manager.get_session(session_id)
            if not session:
                return json.dumps({'success': False, 'error': 'Session not found'})
            
            info = session.get_system_info()
            return json.dumps({'success': True, 'info': info})
        except Exception as e:
            self.logger.error(f"API: Error getting system info for session {session_id}: {e}")
            return json.dumps({'success': False, 'error': str(e)})
    
    def get_system_stats(self, session_id: str) -> str:
        """Get real-time system statistics."""
        try:
            session = self.session_manager.get_session(session_id)
            if not session:
                return json.dumps({'success': False, 'error': 'Session not found'})
            
            stats = session.get_system_stats()
            return json.dumps({'success': True, 'stats': stats})
        except Exception as e:
            self.logger.error(f"API: Error getting system stats for session {session_id}: {e}")
            return json.dumps({'success': False, 'error': str(e)})
    
    def get_process_list(self, session_id: str) -> str:
        """Get running processes."""
        try:
            session = self.session_manager.get_session(session_id)
            if not session:
                return json.dumps({'success': False, 'error': 'Session not found'})
            
            processes = session.get_process_list()
            return json.dumps({'success': True, 'processes': processes})
        except Exception as e:
            self.logger.error(f"API: Error getting process list for session {session_id}: {e}")
            return json.dumps({'success': False, 'error': str(e)})
    
    def get_disk_usage(self, session_id: str) -> str:
        """Get disk usage information."""
        try:
            session = self.session_manager.get_session(session_id)
            if not session:
                return json.dumps({'success': False, 'error': 'Session not found'})
            
            disk_info = session.get_disk_usage()
            return json.dumps({'success': True, 'disk_usage': disk_info})
        except Exception as e:
            self.logger.error(f"API: Error getting disk usage for session {session_id}: {e}")
            return json.dumps({'success': False, 'error': str(e)})
    
    def get_network_info(self, session_id: str) -> str:
        """Get network interface information."""
        try:
            session = self.session_manager.get_session(session_id)
            if not session:
                return json.dumps({'success': False, 'error': 'Session not found'})
            
            network_info = session.get_network_info()
            return json.dumps({'success': True, 'network_info': network_info})
        except Exception as e:
            self.logger.error(f"API: Error getting network info for session {session_id}: {e}")
            return json.dumps({'success': False, 'error': str(e)})
    
    # Port Forwarding Methods
    def create_local_port_forward(self, session_id: str, local_port: int, remote_host: str, remote_port: int) -> str:
        """Create a local port forward."""
        try:
            session = self.session_manager.get_session(session_id)
            if not session:
                return json.dumps({'success': False, 'error': 'Session not found'})
            
            forward_id = session.create_local_port_forward(local_port, remote_host, remote_port)
            return json.dumps({'success': True, 'forward_id': forward_id})
        except Exception as e:
            self.logger.error(f"API: Error creating local port forward: {e}")
            return json.dumps({'success': False, 'error': str(e)})
    
    def create_remote_port_forward(self, session_id: str, remote_port: int, local_host: str, local_port: int) -> str:
        """Create a remote port forward."""
        try:
            session = self.session_manager.get_session(session_id)
            if not session:
                return json.dumps({'success': False, 'error': 'Session not found'})
            
            forward_id = session.create_remote_port_forward(remote_port, local_host, local_port)
            return json.dumps({'success': True, 'forward_id': forward_id})
        except Exception as e:
            self.logger.error(f"API: Error creating remote port forward: {e}")
            return json.dumps({'success': False, 'error': str(e)})
    
    def create_dynamic_port_forward(self, session_id: str, local_port: int) -> str:
        """Create a dynamic port forward (SOCKS proxy)."""
        try:
            session = self.session_manager.get_session(session_id)
            if not session:
                return json.dumps({'success': False, 'error': 'Session not found'})
            
            forward_id = session.create_dynamic_port_forward(local_port)
            return json.dumps({'success': True, 'forward_id': forward_id})
        except Exception as e:
            self.logger.error(f"API: Error creating dynamic port forward: {e}")
            return json.dumps({'success': False, 'error': str(e)})
    
    def stop_port_forward(self, session_id: str, forward_id: str) -> str:
        """Stop a port forward."""
        try:
            session = self.session_manager.get_session(session_id)
            if not session:
                return json.dumps({'success': False, 'error': 'Session not found'})
            
            success = session.stop_port_forward(forward_id)
            return json.dumps({'success': success})
        except Exception as e:
            self.logger.error(f"API: Error stopping port forward: {e}")
            return json.dumps({'success': False, 'error': str(e)})
    
    def list_port_forwards(self, session_id: str) -> str:
        """List all port forwards for a session."""
        try:
            session = self.session_manager.get_session(session_id)
            if not session:
                return json.dumps({'success': False, 'error': 'Session not found'})
            
            forwards = session.list_port_forwards()
            return json.dumps({'success': True, 'forwards': forwards})
        except Exception as e:
            self.logger.error(f"API: Error listing port forwards: {e}")
            return json.dumps({'success': False, 'error': str(e)})

    def clipboard_copy(self, text: str) -> str:
        """Copy text to system clipboard."""
        try:
            import subprocess
            import platform

            system = platform.system().lower()

            if system == 'windows':
                # Use clip.exe on Windows
                process = subprocess.Popen(['clip'], stdin=subprocess.PIPE)
                process.communicate(text.encode('utf-16le'))
            elif system == 'darwin':
                # Use pbcopy on macOS
                process = subprocess.Popen(['pbcopy'], stdin=subprocess.PIPE)
                process.communicate(text.encode('utf-8'))
            else:
                # Try xclip or xsel on Linux
                try:
                    process = subprocess.Popen(['xclip', '-selection', 'clipboard'], stdin=subprocess.PIPE)
                    process.communicate(text.encode('utf-8'))
                except FileNotFoundError:
                    process = subprocess.Popen(['xsel', '--clipboard', '--input'], stdin=subprocess.PIPE)
                    process.communicate(text.encode('utf-8'))

            return json.dumps({'success': True})
        except Exception as e:
            self.logger.error(f"API: Error copying to clipboard: {e}")
            return json.dumps({'success': False, 'error': str(e)})

    def clipboard_paste(self) -> str:
        """Get text from system clipboard."""
        try:
            import subprocess
            import platform

            system = platform.system().lower()

            if system == 'windows':
                # Use PowerShell on Windows
                result = subprocess.run(
                    ['powershell', '-command', 'Get-Clipboard'],
                    capture_output=True, text=True
                )
                text = result.stdout.rstrip('\r\n')
            elif system == 'darwin':
                # Use pbpaste on macOS
                result = subprocess.run(['pbpaste'], capture_output=True, text=True)
                text = result.stdout
            else:
                # Try xclip or xsel on Linux
                try:
                    result = subprocess.run(
                        ['xclip', '-selection', 'clipboard', '-o'],
                        capture_output=True, text=True
                    )
                    text = result.stdout
                except FileNotFoundError:
                    result = subprocess.run(
                        ['xsel', '--clipboard', '--output'],
                        capture_output=True, text=True
                    )
                    text = result.stdout

            return json.dumps({'success': True, 'text': text})
        except Exception as e:
            self.logger.error(f"API: Error reading from clipboard: {e}")
            return json.dumps({'success': False, 'error': str(e)})

    def cleanup(self):
        """Cleanup resources on shutdown."""
        self.logger.info("API: Cleaning up resources")
        
        # Stop file watcher
        if hasattr(self, 'file_watcher'):
            self.file_watcher.stop()
        
        # Clean up any remaining temp files
        if hasattr(self, 'edit_mappings'):
            import os
            for temp_path in list(self.edit_mappings.keys()):
                try:
                    if os.path.exists(temp_path):
                        os.unlink(temp_path)
                except Exception as e:
                    self.logger.error(f"Error cleaning up temp file {temp_path}: {e}")
        
        self.session_manager.disconnect_all()