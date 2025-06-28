import webview
import json
import threading
import queue
import time
from typing import Dict, Any, Optional, List
import paramiko
import socket
import select
import struct
import sys
import os
import base64
import stat

# Try to import cryptography, but make it optional
try:
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    ENCRYPTION_AVAILABLE = True
except ImportError:
    ENCRYPTION_AVAILABLE = False
    print("Warning: cryptography package not installed. Passwords will be stored in plain text.")
    print("Install with: pip install cryptography")


class ConnectionStore:
    """Manages saved SSH connections with optional encrypted password storage"""
    
    def __init__(self):
        self.config_dir = os.path.expanduser("~/.prismssh")
        self.connections_file = os.path.join(self.config_dir, "connections.json")
        self.key_file = os.path.join(self.config_dir, ".key")
        self._ensure_config_dir()
        self.cipher = self._get_cipher() if ENCRYPTION_AVAILABLE else None
        
    def _ensure_config_dir(self):
        """Create config directory if it doesn't exist"""
        try:
            if not os.path.exists(self.config_dir):
                os.makedirs(self.config_dir, mode=0o700)
        except Exception as e:
            print(f"Error creating config directory: {e}")
    
    def _get_cipher(self):
        """Get or create encryption cipher for passwords"""
        if not ENCRYPTION_AVAILABLE:
            return None
            
        try:
            if os.path.exists(self.key_file):
                with open(self.key_file, 'rb') as f:
                    key = f.read()
            else:
                # Generate a new key
                salt = os.urandom(16)
                kdf = PBKDF2HMAC(
                    algorithm=hashes.SHA256(),
                    length=32,
                    salt=salt,
                    iterations=100000,
                )
                key = base64.urlsafe_b64encode(kdf.derive(b"prismssh-local-key"))
                with open(self.key_file, 'wb') as f:
                    os.chmod(self.key_file, 0o600)
                    f.write(key)
            
            return Fernet(key)
        except Exception as e:
            print(f"Error setting up encryption: {e}")
            return None
    
    def save_connection(self, connection: Dict[str, Any]):
        """Save a connection profile"""
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
                    print(f"Error encrypting password: {e}")
                    # Store in plain text if encryption fails
                    connection['password_encrypted'] = False
            
            # Use hostname@username as key
            key = f"{connection['hostname']}@{connection['username']}"
            connections[key] = connection
            
            # Ensure directory exists before writing
            self._ensure_config_dir()
            
            with open(self.connections_file, 'w') as f:
                json.dump(connections, f, indent=2)
                
            print(f"Connection saved: {key}")
            return True
            
        except Exception as e:
            print(f"Error saving connection: {e}")
            return False
    
    def load_connections(self) -> Dict[str, Any]:
        """Load all saved connections"""
        if not os.path.exists(self.connections_file):
            return {}
        
        try:
            with open(self.connections_file, 'r') as f:
                connections = json.load(f)
            
            # Decrypt passwords if cipher is available
            for key, conn in connections.items():
                if conn.get('password_encrypted') and conn.get('password') and self.cipher:
                    try:
                        conn['password'] = self.cipher.decrypt(
                            conn['password'].encode()
                        ).decode()
                    except Exception as e:
                        print(f"Error decrypting password for {key}: {e}")
                        # If decryption fails, remove the password
                        conn['password'] = ''
                    conn.pop('password_encrypted', None)
                elif conn.get('password_encrypted') and not self.cipher:
                    # Encrypted password but no cipher available
                    print(f"Warning: Cannot decrypt password for {key} (install cryptography package)")
                    conn['password'] = ''
                    conn.pop('password_encrypted', None)
            
            return connections
        except Exception as e:
            print(f"Error loading connections: {e}")
            return {}
    
    def delete_connection(self, key: str):
        """Delete a saved connection"""
        try:
            connections = self.load_connections()
            if key in connections:
                del connections[key]
                with open(self.connections_file, 'w') as f:
                    json.dump(connections, f, indent=2)
                print(f"Connection deleted: {key}")
        except Exception as e:
            print(f"Error deleting connection: {e}")
    
    def get_connection(self, key: str) -> Optional[Dict[str, Any]]:
        """Get a specific connection by key"""
        connections = self.load_connections()
        return connections.get(key)


class SSHClient:
    """Core SSH client using Paramiko"""
    
    def __init__(self):
        self.client = paramiko.SSHClient()
        self.client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        self.channel: Optional[paramiko.Channel] = None
        self.connected = False
        
    def connect(self, hostname: str, port: int = 22, username: str = None, 
                password: str = None, key_filename: str = None) -> bool:
        """Connect to SSH server"""
        try:
            connect_kwargs = {
                'hostname': hostname,
                'port': port,
                'username': username,
            }
            
            if password:
                connect_kwargs['password'] = password
            elif key_filename:
                connect_kwargs['key_filename'] = key_filename
            else:
                # Try to use SSH agent or default keys
                connect_kwargs['allow_agent'] = True
                connect_kwargs['look_for_keys'] = True
            
            self.client.connect(**connect_kwargs)
            self.connected = True
            return True
            
        except paramiko.AuthenticationException:
            print("Authentication failed")
            return False
        except paramiko.SSHException as e:
            print(f"SSH connection failed: {str(e)}")
            return False
        except socket.error as e:
            print(f"Socket error: {str(e)}")
            return False
        except Exception as e:
            print(f"Connection error: {str(e)}")
            return False
    
    def open_shell(self) -> bool:
        """Open an interactive shell session"""
        if not self.connected:
            return False
            
        try:
            self.channel = self.client.invoke_shell()
            self.channel.settimeout(0.0)
            return True
        except Exception as e:
            print(f"Failed to open shell: {str(e)}")
            return False
    
    def close(self):
        """Close the SSH connection"""
        if self.channel:
            self.channel.close()
        if self.client:
            self.client.close()
        self.connected = False


class SSHSession:
    """Represents a single SSH session"""
    def __init__(self, session_id: str):
        self.id = session_id
        self.client = SSHClient()
        self.channel = None
        self.sftp = None
        self.output_queue = queue.Queue()
        self.connected = False
        self.thread = None
        self.running = False
        self.command_buffer = ""
        self.logout_detected = False
        
    def connect(self, hostname: str, port: int, username: str, password: str = None, key_path: str = None):
        """Connect to SSH server"""
        if self.client.connect(hostname, port, username, password, key_path):
            if self.client.open_shell():
                self.channel = self.client.channel
                self.connected = True
                self.running = True
                # Start the output reading thread
                self.thread = threading.Thread(target=self._read_output)
                self.thread.daemon = True
                self.thread.start()
                
                # Initialize SFTP
                try:
                    self.sftp = self.client.client.open_sftp()
                except Exception as e:
                    print(f"Failed to open SFTP: {e}")
                
                return True
        return False
    
    def _read_output(self):
        """Read output from SSH channel in a separate thread"""
        while self.running and self.channel:
            if self.channel.recv_ready():
                try:
                    data = self.channel.recv(4096)
                    if data:
                        self.output_queue.put(data.decode('utf-8', errors='replace'))
                    else:
                        # Server closed connection
                        self.running = False
                        self.connected = False
                        self.output_queue.put('\r\n\r\n[Connection closed by server]\r\n')
                        break
                except Exception as e:
                    print(f"Error reading output: {e}")
                    self.running = False
                    self.connected = False
                    self.output_queue.put(f'\r\n\r\n[Connection lost: {str(e)}]\r\n')
                    break
            # Check if channel is closed
            if self.channel.closed:
                self.running = False
                self.connected = False
                self.output_queue.put('\r\n\r\n[Connection closed]\r\n')
                break
            time.sleep(0.01)
    
    def send_input(self, data: str):
        """Send input to the SSH channel"""
        if self.channel and self.connected:
            try:
                # Check for logout commands
                self.check_logout_command(data)
                self.channel.send(data.encode('utf-8'))
                return True
            except Exception as e:
                print(f"Error sending input: {e}")
                self.connected = False
                return False
        return False
    
    def resize(self, cols: int, rows: int):
        """Resize the terminal"""
        if self.channel:
            try:
                self.channel.resize_pty(width=cols, height=rows)
            except Exception as e:
                print(f"Error resizing terminal: {e}")
    
    def get_output(self):
        """Get all pending output"""
        output = []
        while not self.output_queue.empty():
            try:
                output.append(self.output_queue.get_nowait())
            except queue.Empty:
                break
        return ''.join(output)
    
    def check_logout_command(self, data: str):
        """Check if user entered a logout command"""
        # Build command buffer (handle char-by-char input)
        if data == '\r' or data == '\n':
            # Command completed, check if it's a logout command
            cmd = self.command_buffer.strip().lower()
            if cmd in ['exit', 'logout', 'quit', 'logoff']:
                self.logout_detected = True
            self.command_buffer = ""
        elif data == '\x7f' or data == '\x08':  # Backspace
            if self.command_buffer:
                self.command_buffer = self.command_buffer[:-1]
        elif data == '\x03':  # Ctrl+C
            self.command_buffer = ""
        elif len(data) == 1 and ord(data) >= 32 and ord(data) <= 126:
            # Regular printable character
            self.command_buffer += data
    
    def list_directory(self, path: str) -> List[Dict[str, Any]]:
        """List files in a directory via SFTP"""
        if not self.sftp:
            return []
        
        try:
            files = []
            for item in self.sftp.listdir_attr(path):
                file_info = {
                    'name': item.filename,
                    'size': self._format_size(item.st_size),
                    'date': time.strftime('%b %d', time.localtime(item.st_mtime)),
                    'type': 'directory' if stat.S_ISDIR(item.st_mode) else 'file',
                    'permissions': stat.filemode(item.st_mode),
                    'raw_size': item.st_size
                }
                files.append(file_info)
            
            # Sort directories first, then files
            files.sort(key=lambda x: (x['type'] != 'directory', x['name'].lower()))
            return files
        except Exception as e:
            print(f"Error listing directory {path}: {e}")
            return []
    
    def _format_size(self, size: int) -> str:
        """Format file size in human readable format"""
        for unit in ['B', 'K', 'M', 'G', 'T']:
            if size < 1024.0:
                return f"{size:.1f}{unit}"
            size /= 1024.0
        return f"{size:.1f}P"
    
    def download_file(self, remote_path: str, local_path: str) -> bool:
        """Download a file via SFTP"""
        if not self.sftp:
            return False
        
        try:
            self.sftp.get(remote_path, local_path)
            return True
        except Exception as e:
            print(f"Error downloading file: {e}")
            return False
    
    def upload_file(self, local_path: str, remote_path: str) -> bool:
        """Upload a file via SFTP"""
        if not self.sftp:
            return False
        
        try:
            self.sftp.put(local_path, remote_path)
            return True
        except Exception as e:
            print(f"Error uploading file: {e}")
            return False
    
    def create_directory(self, path: str) -> bool:
        """Create a directory via SFTP"""
        if not self.sftp:
            return False
        
        try:
            self.sftp.mkdir(path)
            return True
        except Exception as e:
            print(f"Error creating directory: {e}")
            return False
    
    def execute_command(self, command: str) -> str:
        """Execute a command and return its output"""
        if not self.client or not self.client.connected:
            return ""
        
        try:
            stdin, stdout, stderr = self.client.client.exec_command(command)
            output = stdout.read().decode('utf-8', errors='replace')
            error = stderr.read().decode('utf-8', errors='replace')
            
            if error:
                print(f"Command error: {error}")
                return error
            return output
        except Exception as e:
            print(f"Error executing command: {e}")
            return ""
    
    def get_system_info(self) -> Dict[str, Any]:
        """Get comprehensive system information"""
        if not self.client or not self.client.connected:
            return {}
        
        try:
            info = {}
            
            # CPU information
            cpu_info = self.execute_command("top -bn1 | grep 'Cpu(s)' | awk '{print $2}' | sed 's/%us,//'")
            cpu_count = self.execute_command("nproc")
            cpu_model = self.execute_command("cat /proc/cpuinfo | grep 'model name' | head -1 | cut -d':' -f2")
            
            info['cpu'] = {
                'usage': float(cpu_info.strip()) if cpu_info.strip() else 0.0,
                'cores': int(cpu_count.strip()) if cpu_count.strip().isdigit() else 1,
                'model': cpu_model.strip()
            }
            
            # Memory information
            mem_info = self.execute_command("free -m | grep '^Mem:'")
            if mem_info:
                parts = mem_info.split()
                if len(parts) >= 3:
                    total = int(parts[1])
                    used = int(parts[2])
                    available = int(parts[6]) if len(parts) > 6 else total - used
                    
                    info['memory'] = {
                        'total': total,
                        'used': used,
                        'available': available,
                        'percentage': round((used / total) * 100, 1) if total > 0 else 0
                    }
            
            # Disk information
            disk_info = self.execute_command("df -h / | tail -1")
            if disk_info:
                parts = disk_info.split()
                if len(parts) >= 5:
                    info['disk'] = {
                        'total': parts[1],
                        'used': parts[2],
                        'available': parts[3],
                        'percentage': int(parts[4].replace('%', '')) if parts[4] != '-' else 0
                    }
            
            # Load average
            load_avg = self.execute_command("uptime | awk -F'load average:' '{print $2}'")
            if load_avg:
                loads = [float(x.strip()) for x in load_avg.split(',') if x.strip()]
                info['load_average'] = loads[:3] if loads else [0.0, 0.0, 0.0]
            
            # Uptime
            uptime = self.execute_command("uptime -p")
            info['uptime'] = uptime.strip()
            
            # Network interfaces
            network = self.execute_command("ip -s link show | grep -E '^[0-9]+:' -A1")
            info['network'] = network.strip()
            
            return info
            
        except Exception as e:
            print(f"Error getting system info: {e}")
            return {}
    
    def get_process_list(self, limit: int = 20) -> List[Dict[str, Any]]:
        """Get list of running processes"""
        if not self.client or not self.client.connected:
            return []
        
        try:
            # Try multiple command variations for compatibility
            commands = [
                f"ps aux --sort=-%cpu | head -{limit + 1}",  # Linux with GNU ps
                f"ps aux | sort -k3 -nr | head -{limit + 1}",  # Alternative sorting
                f"ps aux | head -{limit + 1}"  # Basic fallback
            ]
            
            output = ""
            for cmd in commands:
                output = self.execute_command(cmd)
                if output and "USER" in output:  # Check if we got a valid ps output
                    break
            
            print(f"Process command output: {output[:500]}...")  # Debug output
            
            processes = []
            lines = output.strip().split('\n')  # Use \n instead of \\n
            
            print(f"Found {len(lines)} lines in ps output")  # Debug
            
            # Skip header line
            for i, line in enumerate(lines[1:], 1):
                if not line.strip():
                    continue
                    
                # Split into parts, but be more flexible with the command part
                parts = line.split(None, 10)
                print(f"Line {i}: {len(parts)} parts - {line[:100]}...")  # Debug
                
                if len(parts) >= 10:  # Reduced from 11 to handle cases without command
                    try:
                        cpu_str = parts[2].replace('%', '')
                        mem_str = parts[3].replace('%', '')
                        
                        processes.append({
                            'user': parts[0],
                            'pid': parts[1],
                            'cpu': float(cpu_str) if cpu_str.replace('.', '').replace('-', '').isdigit() else 0.0,
                            'memory': float(mem_str) if mem_str.replace('.', '').replace('-', '').isdigit() else 0.0,
                            'vsz': parts[4],
                            'rss': parts[5],
                            'tty': parts[6],
                            'stat': parts[7],
                            'start': parts[8],
                            'time': parts[9],
                            'command': parts[10] if len(parts) > 10 else f"{parts[0]} process"
                        })
                    except (ValueError, IndexError) as e:
                        print(f"Error parsing process line: {e} - {line}")
                        continue
            
            print(f"Successfully parsed {len(processes)} processes")  # Debug
            return processes
            
        except Exception as e:
            print(f"Error getting process list: {e}")
            return []
    
    def disconnect(self):
        """Disconnect the session"""
        self.running = False
        if self.thread:
            self.thread.join(timeout=1)
        if self.sftp:
            self.sftp.close()
        if self.client:
            self.client.close()
        self.connected = False


class SSHManager:
    """Manages multiple SSH sessions"""
    def __init__(self):
        self.sessions: Dict[str, SSHSession] = {}
        self.next_id = 1
        
    def create_session(self) -> str:
        """Create a new session and return its ID"""
        session_id = f"session_{self.next_id}"
        self.next_id += 1
        self.sessions[session_id] = SSHSession(session_id)
        return session_id
    
    def connect_session(self, session_id: str, connection_params: Dict[str, Any]) -> bool:
        """Connect a session with given parameters"""
        if session_id in self.sessions:
            session = self.sessions[session_id]
            return session.connect(
                connection_params['hostname'],
                connection_params.get('port', 22),
                connection_params['username'],
                connection_params.get('password'),
                connection_params.get('keyPath')
            )
        return False
    
    def send_input(self, session_id: str, data: str) -> bool:
        """Send input to a session"""
        if session_id in self.sessions:
            return self.sessions[session_id].send_input(data)
        return False
    
    def get_output(self, session_id: str) -> Optional[str]:
        """Get output from a session"""
        if session_id in self.sessions:
            return self.sessions[session_id].get_output()
        return None
    
    def resize_terminal(self, session_id: str, cols: int, rows: int):
        """Resize a terminal"""
        if session_id in self.sessions:
            self.sessions[session_id].resize(cols, rows)
    
    def disconnect_session(self, session_id: str):
        """Disconnect a session"""
        if session_id in self.sessions:
            self.sessions[session_id].disconnect()
            del self.sessions[session_id]
    
    def get_session_status(self, session_id: str) -> Dict[str, Any]:
        """Get status of a session"""
        if session_id in self.sessions:
            session = self.sessions[session_id]
            return {
                'connected': session.connected,
                'id': session_id,
                'logout_detected': session.logout_detected
            }
        return {'connected': False, 'id': session_id, 'logout_detected': False}


class API:
    """API exposed to JavaScript"""
    def __init__(self):
        self.ssh_manager = SSHManager()
        self.connection_store = ConnectionStore()
    
    def create_session(self):
        """Create a new SSH session"""
        return self.ssh_manager.create_session()
    
    def connect(self, session_id: str, connection_params: str):
        """Connect to SSH server"""
        try:
            params = json.loads(connection_params)
            
            # Save connection if requested
            if params.get('save', False):
                save_result = self.connection_store.save_connection({
                    'hostname': params['hostname'],
                    'port': params.get('port', 22),
                    'username': params['username'],
                    'password': params.get('password'),
                    'keyPath': params.get('keyPath'),
                    'name': params.get('name', f"{params['username']}@{params['hostname']}")
                })
                if not save_result:
                    print("Warning: Failed to save connection")
            
            success = self.ssh_manager.connect_session(session_id, params)
            return json.dumps({'success': success})
        except Exception as e:
            print(f"Connection error: {e}")
            return json.dumps({'success': False, 'error': str(e)})
    
    def get_saved_connections(self):
        """Get all saved connections"""
        connections = self.connection_store.load_connections()
        # Convert to list format for frontend
        connection_list = []
        for key, conn in connections.items():
            conn['key'] = key
            connection_list.append(conn)
        return json.dumps(connection_list)
    
    def delete_saved_connection(self, key: str):
        """Delete a saved connection"""
        self.connection_store.delete_connection(key)
        return json.dumps({'success': True})
    
    def send_input(self, session_id: str, data: str):
        """Send input to terminal"""
        success = self.ssh_manager.send_input(session_id, data)
        return json.dumps({'success': success})
    
    def get_output(self, session_id: str):
        """Get terminal output"""
        output = self.ssh_manager.get_output(session_id)
        return json.dumps({'output': output or ''})
    
    def resize_terminal(self, session_id: str, cols: int, rows: int):
        """Resize terminal"""
        self.ssh_manager.resize_terminal(session_id, cols, rows)
        return json.dumps({'success': True})
    
    def disconnect(self, session_id: str):
        """Disconnect session"""
        self.ssh_manager.disconnect_session(session_id)
        return json.dumps({'success': True})
    
    def get_status(self, session_id: str):
        """Get session status"""
        status = self.ssh_manager.get_session_status(session_id)
        return json.dumps(status)
    
    # SFTP Methods
    def list_directory(self, session_id: str, path: str):
        """List directory contents via SFTP"""
        if session_id in self.ssh_manager.sessions:
            files = self.ssh_manager.sessions[session_id].list_directory(path)
            return json.dumps({'success': True, 'files': files})
        return json.dumps({'success': False, 'error': 'Session not found'})
    
    def download_file(self, session_id: str, remote_path: str, local_path: str):
        """Download a file via SFTP"""
        if session_id in self.ssh_manager.sessions:
            success = self.ssh_manager.sessions[session_id].download_file(remote_path, local_path)
            return json.dumps({'success': success})
        return json.dumps({'success': False, 'error': 'Session not found'})
    
    def upload_file(self, session_id: str, local_path: str, remote_path: str):
        """Upload a file via SFTP"""
        if session_id in self.ssh_manager.sessions:
            success = self.ssh_manager.sessions[session_id].upload_file(local_path, remote_path)
            return json.dumps({'success': success})
        return json.dumps({'success': False, 'error': 'Session not found'})
    
    def create_directory(self, session_id: str, path: str):
        """Create a directory via SFTP"""
        if session_id in self.ssh_manager.sessions:
            success = self.ssh_manager.sessions[session_id].create_directory(path)
            return json.dumps({'success': success})
        return json.dumps({'success': False, 'error': 'Session not found'})
    
    # System Monitoring Methods
    def get_system_info(self, session_id: str):
        """Get system information"""
        if session_id in self.ssh_manager.sessions:
            info = self.ssh_manager.sessions[session_id].get_system_info()
            return json.dumps({'success': True, 'info': info})
        return json.dumps({'success': False, 'error': 'Session not found'})
    
    def get_process_list(self, session_id: str, limit: int = 20):
        """Get list of running processes"""
        if session_id in self.ssh_manager.sessions:
            processes = self.ssh_manager.sessions[session_id].get_process_list(limit)
            return json.dumps({'success': True, 'processes': processes})
        return json.dumps({'success': False, 'error': 'Session not found'})
    
    def execute_command(self, session_id: str, command: str):
        """Execute a command on the remote system"""
        if session_id in self.ssh_manager.sessions:
            output = self.ssh_manager.sessions[session_id].execute_command(command)
            return json.dumps({'success': True, 'output': output})
        return json.dumps({'success': False, 'error': 'Session not found'})


# HTML/CSS/JS for the GUI
HTML_CONTENT = """
<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <title>PrismSSH - Modern SSH Client</title>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <link rel="stylesheet" href="https://unpkg.com/xterm@5.3.0/css/xterm.css">
    <script src="https://unpkg.com/xterm@5.3.0/lib/xterm.js"></script>
    <script src="https://unpkg.com/@xterm/addon-fit@0.8.0/lib/addon-fit.js"></script>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }
        
        body {
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
            background: #0a0a0a;
            color: #e0e0e0;
            height: 100vh;
            overflow: hidden;
        }
        
        .app {
            display: flex;
            height: 100vh;
            background: linear-gradient(135deg, #0a0a0a 0%, #1a1a1a 100%);
        }
        
        .sidebar {
            width: 300px;
            background: rgba(20, 20, 20, 0.9);
            border-right: 1px solid rgba(255, 255, 255, 0.1);
            display: flex;
            flex-direction: column;
            backdrop-filter: blur(10px);
            overflow: hidden;
        }
        
        .logo {
            padding: 20px 24px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.1);
            flex-shrink: 0;
        }
        
        .logo h1 {
            font-size: 28px;
            font-weight: 700;
            background: linear-gradient(135deg, #00d4ff 0%, #0099ff 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }
        
        .sidebar-content {
            flex: 1;
            overflow-y: auto;
            overflow-x: hidden;
        }
        
        .collapsible-section {
            border-bottom: 1px solid rgba(255, 255, 255, 0.1);
        }
        
        .section-header {
            padding: 16px 24px;
            cursor: pointer;
            display: flex;
            align-items: center;
            justify-content: space-between;
            transition: background 0.2s ease;
            user-select: none;
        }
        
        .section-header:hover {
            background: rgba(255, 255, 255, 0.05);
        }
        
        .section-title {
            font-size: 13px;
            font-weight: 600;
            color: #a0a0a0;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        
        .section-chevron {
            width: 16px;
            height: 16px;
            transition: transform 0.3s ease;
            color: #666;
        }
        
        .section-chevron.open {
            transform: rotate(180deg);
        }
        
        .section-content {
            max-height: 0;
            overflow: hidden;
            transition: max-height 0.3s ease;
        }
        
        .section-content.open {
            max-height: 1000px;
        }
        
        .connect-form {
            padding: 0 24px 24px 24px;
        }
        
        .form-group {
            margin-bottom: 16px;
        }
        
        .form-group label {
            display: block;
            margin-bottom: 8px;
            font-size: 12px;
            font-weight: 500;
            color: #a0a0a0;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        
        .form-group input, .form-group select {
            width: 100%;
            padding: 10px 14px;
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 6px;
            color: #fff;
            font-size: 14px;
            transition: all 0.3s ease;
        }
        
        .form-group input:focus, .form-group select:focus {
            outline: none;
            border-color: #0099ff;
            background: rgba(255, 255, 255, 0.08);
            box-shadow: 0 0 0 2px rgba(0, 153, 255, 0.1);
        }
        
        .checkbox-group {
            display: flex;
            align-items: center;
            gap: 8px;
            margin-bottom: 16px;
        }
        
        .checkbox-group input[type="checkbox"] {
            width: 16px;
            height: 16px;
            margin: 0;
        }
        
        .checkbox-group label {
            margin: 0;
            font-size: 13px;
            font-weight: 400;
            color: #ccc;
            text-transform: none;
            letter-spacing: normal;
            cursor: pointer;
        }
        
        .connect-btn {
            width: 100%;
            padding: 12px;
            background: linear-gradient(135deg, #00d4ff 0%, #0099ff 100%);
            border: none;
            border-radius: 6px;
            color: #fff;
            font-size: 14px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.3s ease;
            position: relative;
            overflow: hidden;
        }
        
        .connect-btn:hover {
            transform: translateY(-1px);
            box-shadow: 0 5px 15px rgba(0, 153, 255, 0.3);
        }
        
        .connect-btn:active {
            transform: translateY(0);
        }
        
        .saved-connections {
            padding: 0 16px 16px 16px;
        }
        
        .saved-connection-item {
            padding: 10px 12px;
            background: rgba(255, 255, 255, 0.05);
            border-radius: 6px;
            margin-bottom: 8px;
            cursor: pointer;
            transition: all 0.2s ease;
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 8px;
            font-size: 13px;
        }
        
        .saved-connection-item:hover {
            background: rgba(255, 255, 255, 0.08);
            transform: translateX(2px);
        }
        
        .saved-connection-info {
            flex: 1;
            min-width: 0;
        }
        
        .saved-connection-name {
            font-weight: 500;
            margin-bottom: 2px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        
        .saved-connection-details {
            font-size: 11px;
            color: #666;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        
        .saved-connection-actions {
            display: flex;
            gap: 4px;
            opacity: 0;
            transition: opacity 0.2s ease;
            flex-shrink: 0;
        }
        
        .saved-connection-item:hover .saved-connection-actions {
            opacity: 1;
        }
        
        .action-btn {
            padding: 4px 8px;
            background: rgba(255, 255, 255, 0.1);
            border: 1px solid rgba(255, 255, 255, 0.2);
            border-radius: 4px;
            color: #fff;
            font-size: 11px;
            cursor: pointer;
            transition: all 0.2s ease;
            white-space: nowrap;
        }
        
        .action-btn:hover {
            background: rgba(255, 255, 255, 0.2);
        }
        
        .action-btn.delete {
            color: #ff4444;
            border-color: rgba(255, 68, 68, 0.3);
        }
        
        .action-btn.delete:hover {
            background: rgba(255, 68, 68, 0.2);
        }
        
        input[type="checkbox"] {
            accent-color: #0099ff;
        }
        
        .sessions-list {
            padding: 0 16px 16px 16px;
        }
        
        .empty-message {
            text-align: center;
            color: #666;
            padding: 20px;
            font-size: 12px;
        }
            flex: 1;
            overflow-y: auto;
            padding: 16px;
        }
        
        .sessions-title {
            font-size: 12px;
            font-weight: 600;
            color: #666;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 12px;
        }
        
        .session-item {
            padding: 12px 16px;
            background: rgba(255, 255, 255, 0.05);
            border-radius: 8px;
            margin-bottom: 8px;
            cursor: pointer;
            transition: all 0.2s ease;
            display: flex;
            align-items: center;
            gap: 12px;
        }
        
        .session-item:hover {
            background: rgba(255, 255, 255, 0.08);
            transform: translateX(4px);
        }
        
        .session-item.active {
            background: rgba(0, 153, 255, 0.2);
            border: 1px solid rgba(0, 153, 255, 0.3);
        }
        
        .session-status {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: #00ff88;
            box-shadow: 0 0 8px rgba(0, 255, 136, 0.5);
        }
        
        .session-info {
            flex: 1;
        }
        
        .session-name {
            font-size: 14px;
            font-weight: 500;
        }
        
        .session-host {
            font-size: 12px;
            color: #666;
        }
        
        .main-content {
            flex: 1;
            display: flex;
            flex-direction: column;
            position: relative;
        }
        
        .content-area {
            flex: 1;
            display: flex;
            position: relative;
        }
        
        .terminal-section {
            flex: 1;
            display: flex;
            flex-direction: column;
        }
        
        .right-sidebar {
            width: 0;
            background: rgba(20, 20, 20, 0.9);
            border-left: 1px solid rgba(255, 255, 255, 0.1);
            display: flex;
            transition: width 0.3s ease;
            overflow: hidden;
        }
        
        .right-sidebar.open {
            width: 350px;
        }
        
        .tool-icons {
            width: 48px;
            background: rgba(10, 10, 10, 0.9);
            border-left: 1px solid rgba(255, 255, 255, 0.1);
            display: flex;
            flex-direction: column;
            padding: 8px 0;
            gap: 4px;
        }
        
        .tool-icon {
            width: 48px;
            height: 48px;
            display: flex;
            align-items: center;
            justify-content: center;
            cursor: pointer;
            transition: all 0.2s ease;
            position: relative;
            color: #666;
        }
        
        .tool-icon:hover {
            background: rgba(255, 255, 255, 0.05);
            color: #fff;
        }
        
        .tool-icon.active {
            background: rgba(0, 153, 255, 0.2);
            color: #00d4ff;
        }
        
        .tool-icon svg {
            width: 24px;
            height: 24px;
        }
        
        .tool-tooltip {
            position: absolute;
            right: 100%;
            top: 50%;
            transform: translateY(-50%);
            background: rgba(0, 0, 0, 0.9);
            color: #fff;
            padding: 6px 12px;
            border-radius: 4px;
            font-size: 12px;
            white-space: nowrap;
            pointer-events: none;
            opacity: 0;
            transition: opacity 0.2s ease;
            margin-right: 8px;
        }
        
        .tool-icon:hover .tool-tooltip {
            opacity: 1;
        }
        
        .tool-panel {
            flex: 1;
            display: none;
            flex-direction: column;
            overflow: hidden;
        }
        
        .tool-panel.active {
            display: flex;
        }
        
        .tool-header {
            padding: 16px 20px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.1);
            display: flex;
            align-items: center;
            justify-content: space-between;
        }
        
        .tool-title {
            font-size: 16px;
            font-weight: 600;
        }
        
        .tool-close {
            width: 24px;
            height: 24px;
            cursor: pointer;
            color: #666;
            transition: color 0.2s ease;
        }
        
        .tool-close:hover {
            color: #fff;
        }
        
        .tool-content {
            flex: 1;
            overflow: hidden;
            padding: 16px;
            position: relative;
        }
        
        /* SFTP File Browser Styles */
        .file-browser {
            position: absolute;
            top: 16px;
            left: 16px;
            right: 16px;
            bottom: 16px;
            display: flex;
            flex-direction: column;
        }
        
        .file-path {
            padding: 12px 16px;
            background: rgba(255, 255, 255, 0.05);
            border-radius: 6px;
            margin-bottom: 16px;
            font-family: 'Consolas', monospace;
            font-size: 13px;
            color: #00d4ff;
        }
        
        .file-actions {
            display: flex;
            gap: 8px;
            margin-bottom: 16px;
        }
        
        .file-action-btn {
            padding: 8px 16px;
            background: rgba(255, 255, 255, 0.05);
            border: 1px solid rgba(255, 255, 255, 0.1);
            border-radius: 6px;
            color: #fff;
            font-size: 12px;
            cursor: pointer;
            transition: all 0.2s ease;
        }
        
        .file-action-btn:hover {
            background: rgba(255, 255, 255, 0.1);
            border-color: rgba(255, 255, 255, 0.2);
        }
        
        .file-list-container {
            flex: 1;
            position: relative;
            min-height: 0;
            overflow: hidden;
        }
        
        .file-list {
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            overflow-y: auto;
            overflow-x: hidden;
        }
        
        .file-list.loading {
            opacity: 0.6;
            pointer-events: none;
        }
        
        .file-browser-loading {
            position: absolute;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            display: none;
        }
        
        .file-browser-loading.active {
            display: block;
        }
        
        .file-browser-spinner {
            width: 30px;
            height: 30px;
            border: 3px solid rgba(0, 153, 255, 0.1);
            border-top-color: #0099ff;
            border-radius: 50%;
            animation: spin 0.8s linear infinite;
        }
        
        .file-item {
            padding: 10px 12px;
            background: rgba(255, 255, 255, 0.03);
            border-radius: 6px;
            margin-bottom: 4px;
            cursor: pointer;
            transition: all 0.2s ease;
            display: flex;
            align-items: center;
            gap: 12px;
        }
        
        .file-item:hover {
            background: rgba(255, 255, 255, 0.08);
            transform: translateX(2px);
        }
        
        .file-item.selected {
            background: rgba(0, 153, 255, 0.2);
            border: 1px solid rgba(0, 153, 255, 0.3);
        }
        
        .file-icon {
            width: 20px;
            height: 20px;
            flex-shrink: 0;
        }
        
        .file-name {
            flex: 1;
            font-size: 13px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }
        
        .file-size {
            font-size: 11px;
            color: #666;
            flex-shrink: 0;
        }
        
        .file-date {
            font-size: 11px;
            color: #666;
            flex-shrink: 0;
        }
        
        .upload-area {
            margin-top: 16px;
            padding: 20px;
            border: 2px dashed rgba(255, 255, 255, 0.2);
            border-radius: 8px;
            text-align: center;
            transition: all 0.3s ease;
        }
        
        .upload-area.dragover {
            background: rgba(0, 153, 255, 0.1);
            border-color: #0099ff;
        }
        
        .upload-text {
            font-size: 13px;
            color: #666;
            margin-bottom: 8px;
        }
        
        .upload-button {
            padding: 8px 16px;
            background: linear-gradient(135deg, #00d4ff 0%, #0099ff 100%);
            border: none;
            border-radius: 6px;
            color: #fff;
            font-size: 12px;
            cursor: pointer;
            transition: all 0.2s ease;
        }
        
        .upload-button:hover {
            transform: translateY(-1px);
            box-shadow: 0 5px 15px rgba(0, 153, 255, 0.3);
        }
        
        .tabs-container {
            display: flex;
            background: rgba(20, 20, 20, 0.6);
            border-bottom: 1px solid rgba(255, 255, 255, 0.1);
            padding: 0 16px;
            backdrop-filter: blur(10px);
        }
        
        .tab {
            padding: 12px 24px;
            cursor: pointer;
            transition: all 0.2s ease;
            position: relative;
            font-size: 14px;
            font-weight: 500;
            color: #888;
        }
        
        .tab:hover {
            color: #ccc;
        }
        
        .tab.active {
            color: #fff;
        }
        
        .tab.active::after {
            content: '';
            position: absolute;
            bottom: 0;
            left: 0;
            right: 0;
            height: 2px;
            background: linear-gradient(90deg, #00d4ff 0%, #0099ff 100%);
        }
        
        .terminal-container {
            flex: 1;
            padding: 24px;
            position: relative;
            display: flex;
            flex-direction: column;
        }
        
        .terminal-wrapper {
            flex: 1;
            background: #000;
            border-radius: 12px;
            overflow: hidden;
            box-shadow: 0 20px 50px rgba(0, 0, 0, 0.5);
            border: 1px solid rgba(255, 255, 255, 0.1);
            position: relative;
            padding: 8px;
        }
        
        #terminal {
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
        }
        
        /* xterm.js specific styles - force full height */
        .xterm {
            height: 100%;
            width: 100%;
        }
        
        .xterm-viewport {
            height: 100% !important;
            width: 100% !important;
        }
        
        .xterm-screen {
            height: 100% !important;
            width: 100% !important;
        }
        
        .xterm-helpers {
            height: 100% !important;
        }
        
        .welcome-screen {
            display: flex;
            flex-direction: column;
            align-items: center;
            justify-content: center;
            height: 100%;
            text-align: center;
        }
        
        .welcome-icon {
            font-size: 80px;
            margin-bottom: 24px;
            opacity: 0.2;
        }
        
        .welcome-title {
            font-size: 32px;
            font-weight: 700;
            margin-bottom: 16px;
            background: linear-gradient(135deg, #00d4ff 0%, #0099ff 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
        }
        
        .welcome-subtitle {
            font-size: 16px;
            color: #666;
        }
        
        .status-bar {
            background: rgba(20, 20, 20, 0.9);
            border-top: 1px solid rgba(255, 255, 255, 0.1);
            padding: 8px 24px;
            display: flex;
            align-items: center;
            gap: 24px;
            font-size: 12px;
            color: #666;
            backdrop-filter: blur(10px);
            flex-shrink: 0;
        }
        
        .status-item {
            display: flex;
            align-items: center;
            gap: 8px;
        }
        
        .status-indicator {
            width: 6px;
            height: 6px;
            border-radius: 50%;
            background: #00ff88;
        }
        
        /* Scrollbar styling */
        ::-webkit-scrollbar {
            width: 8px;
            height: 8px;
        }
        
        ::-webkit-scrollbar-track {
            background: rgba(255, 255, 255, 0.02);
        }
        
        ::-webkit-scrollbar-thumb {
            background: rgba(255, 255, 255, 0.1);
            border-radius: 4px;
        }
        
        ::-webkit-scrollbar-thumb:hover {
            background: rgba(255, 255, 255, 0.2);
        }
        
        /* Loading animation */
        .connecting {
            position: absolute;
            top: 50%;
            left: 50%;
            transform: translate(-50%, -50%);
            text-align: center;
        }
        
        .spinner {
            width: 50px;
            height: 50px;
            border: 3px solid rgba(0, 153, 255, 0.1);
            border-top-color: #0099ff;
            border-radius: 50%;
            animation: spin 1s linear infinite;
            margin: 0 auto 16px;
        }
        
        @keyframes spin {
            to { transform: rotate(360deg); }
        }
        
        /* System Monitor Styles */
        .monitor-grid {
            position: absolute;
            top: 16px;
            left: 16px;
            right: 16px;
            bottom: 16px;
            display: grid;
            grid-template-columns: 1fr 1fr;
            grid-template-rows: auto auto 1fr;
            gap: 16px;
            overflow-y: auto;
        }
        
        .monitor-card {
            background: rgba(255, 255, 255, 0.05);
            border-radius: 8px;
            padding: 16px;
            border: 1px solid rgba(255, 255, 255, 0.1);
        }
        
        .monitor-card.full-width {
            grid-column: 1 / -1;
        }
        
        .monitor-card h3 {
            font-size: 14px;
            font-weight: 600;
            color: #00d4ff;
            margin-bottom: 12px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
        }
        
        .system-overview {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 12px;
        }
        
        .metric-item {
            background: rgba(255, 255, 255, 0.03);
            border-radius: 6px;
            padding: 12px;
        }
        
        .metric-label {
            font-size: 11px;
            color: #888;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            margin-bottom: 4px;
        }
        
        .metric-value {
            font-size: 18px;
            font-weight: 600;
            color: #fff;
        }
        
        .metric-subtext {
            font-size: 11px;
            color: #666;
            margin-top: 2px;
        }
        
        .progress-bar {
            width: 100%;
            height: 6px;
            background: rgba(255, 255, 255, 0.1);
            border-radius: 3px;
            margin-top: 8px;
            overflow: hidden;
        }
        
        .progress-fill {
            height: 100%;
            background: linear-gradient(90deg, #00d4ff 0%, #0099ff 100%);
            transition: width 0.3s ease;
            border-radius: 3px;
        }
        
        .progress-fill.warning {
            background: linear-gradient(90deg, #ffaa00 0%, #ff8800 100%);
        }
        
        .progress-fill.danger {
            background: linear-gradient(90deg, #ff4444 0%, #cc0000 100%);
        }
        
        .process-table {
            width: 100%;
            border-collapse: collapse;
            font-size: 12px;
        }
        
        .process-table th {
            background: rgba(255, 255, 255, 0.1);
            padding: 8px;
            text-align: left;
            font-weight: 600;
            color: #ccc;
            border-bottom: 1px solid rgba(255, 255, 255, 0.2);
        }
        
        .process-table td {
            padding: 6px 8px;
            border-bottom: 1px solid rgba(255, 255, 255, 0.05);
            color: #ddd;
        }
        
        .process-table tr:hover {
            background: rgba(255, 255, 255, 0.05);
        }
        
        .process-table .cpu-cell {
            text-align: right;
            font-family: monospace;
        }
        
        .process-table .mem-cell {
            text-align: right;
            font-family: monospace;
        }
        
        .process-table .command-cell {
            max-width: 200px;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        
        .monitor-controls {
            display: flex;
            gap: 8px;
            margin-bottom: 12px;
        }
        
        .monitor-btn {
            padding: 6px 12px;
            background: rgba(255, 255, 255, 0.1);
            border: 1px solid rgba(255, 255, 255, 0.2);
            border-radius: 4px;
            color: #fff;
            font-size: 11px;
            cursor: pointer;
            transition: all 0.2s ease;
        }
        
        .monitor-btn:hover {
            background: rgba(255, 255, 255, 0.2);
        }
        
        .monitor-btn.active {
            background: rgba(0, 153, 255, 0.3);
            border-color: #0099ff;
        }
        
        .loading-indicator {
            display: flex;
            align-items: center;
            justify-content: center;
            height: 100px;
            color: #666;
        }
        
        .loading-spinner {
            width: 20px;
            height: 20px;
            border: 2px solid rgba(0, 153, 255, 0.2);
            border-top-color: #0099ff;
            border-radius: 50%;
            animation: spin 1s linear infinite;
            margin-right: 8px;
        }
    </style>
</head>
<body>
    <div class="app">
        <div class="sidebar">
            <div class="logo">
                <h1>PrismSSH</h1>
            </div>
            
            <div class="sidebar-content">
                <!-- New Connection Section -->
                <div class="collapsible-section">
                    <div class="section-header" onclick="toggleSection('newConnection')">
                        <span class="section-title">New Connection</span>
                        <svg class="section-chevron open" id="newConnectionChevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <path d="M6 9l6 6 6-6"/>
                        </svg>
                    </div>
                    <div class="section-content open" id="newConnectionContent">
                        <div class="connect-form">
                            <div class="form-group">
                                <label>Hostname</label>
                                <input type="text" id="hostname" placeholder="example.com" />
                            </div>
                            
                            <div class="form-group">
                                <label>Port</label>
                                <input type="number" id="port" placeholder="22" value="22" />
                            </div>
                            
                            <div class="form-group">
                                <label>Username</label>
                                <input type="text" id="username" placeholder="root" />
                            </div>
                            
                            <div class="form-group">
                                <label>Authentication</label>
                                <select id="authType">
                                    <option value="password">Password</option>
                                    <option value="key">Private Key</option>
                                </select>
                            </div>
                            
                            <div class="form-group" id="passwordGroup">
                                <label>Password</label>
                                <input type="password" id="password" placeholder="••••••••" />
                            </div>
                            
                            <div class="form-group" id="keyGroup" style="display: none;">
                                <label>Private Key Path</label>
                                <input type="text" id="keyPath" placeholder="/path/to/key" />
                            </div>
                            
                            <div class="checkbox-group">
                                <input type="checkbox" id="saveConnection" />
                                <label for="saveConnection">Save connection</label>
                            </div>
                            
                            <button class="connect-btn" onclick="connect()">
                                Connect
                            </button>
                        </div>
                    </div>
                </div>
                
                <!-- Saved Connections Section -->
                <div class="collapsible-section">
                    <div class="section-header" onclick="toggleSection('savedConnections')">
                        <span class="section-title">Saved Connections</span>
                        <svg class="section-chevron open" id="savedConnectionsChevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <path d="M6 9l6 6 6-6"/>
                        </svg>
                    </div>
                    <div class="section-content open" id="savedConnectionsContent">
                        <div class="saved-connections">
                            <div id="savedConnectionsList"></div>
                        </div>
                    </div>
                </div>
                
                <!-- Active Sessions Section -->
                <div class="collapsible-section">
                    <div class="section-header" onclick="toggleSection('activeSessions')">
                        <span class="section-title">Active Sessions</span>
                        <svg class="section-chevron" id="activeSessionsChevron" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <path d="M6 9l6 6 6-6"/>
                        </svg>
                    </div>
                    <div class="section-content" id="activeSessionsContent">
                        <div class="sessions-list">
                            <div id="sessionsList"></div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
        
        <div class="main-content">
            <div class="tabs-container" id="tabsContainer" style="display: none;">
            </div>
            
            <div class="content-area">
                <div class="terminal-section">
                    <div class="terminal-container">
                        <div id="welcomeScreen" class="welcome-screen">
                            <div class="welcome-icon">🔷</div>
                            <div class="welcome-title">Welcome to PrismSSH</div>
                            <div class="welcome-subtitle">Connect to a server to get started</div>
                        </div>
                        
                        <div id="terminalWrapper" class="terminal-wrapper" style="display: none;">
                            <div id="terminal"></div>
                        </div>
                        
                        <div id="connectingScreen" class="connecting" style="display: none;">
                            <div class="spinner"></div>
                            <div>Connecting...</div>
                        </div>
                    </div>
                    
                    <div class="status-bar" id="statusBar" style="display: none;">
                        <div class="status-item">
                            <div class="status-indicator"></div>
                            <span>Connected</span>
                        </div>
                        <div class="status-item">
                            <span id="statusHost">-</span>
                        </div>
                    </div>
                </div>
                
                <div class="right-sidebar" id="rightSidebar">
                    <!-- Tool Panels -->
                    <div class="tool-panel" id="sftpPanel">
                        <div class="tool-header">
                            <div class="tool-title">SFTP File Browser</div>
                            <svg class="tool-close" onclick="closeToolPanel()" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <path d="M18 6L6 18M6 6l12 12"/>
                            </svg>
                        </div>
                        <div class="tool-content">
                            <div class="file-browser">
                                <div class="file-path" id="currentPath">/home/user</div>
                                <div class="file-actions">
                                    <button class="file-action-btn" onclick="navigateUp()">⬆ Up</button>
                                    <button class="file-action-btn" onclick="refreshFiles()">🔄 Refresh</button>
                                    <button class="file-action-btn" onclick="createNewFolder()">📁 New Folder</button>
                                </div>
                                <div class="file-list-container">
                                    <div class="file-list" id="fileList">
                                        <!-- Files will be populated here -->
                                    </div>
                                    <div class="file-browser-loading" id="fileBrowserLoading">
                                        <div class="file-browser-spinner"></div>
                                    </div>
                                </div>
                                <div class="upload-area" id="uploadArea">
                                    <div class="upload-text">Drag files here or</div>
                                    <button class="upload-button" onclick="selectFiles()">Browse Files</button>
                                    <input type="file" id="fileInput" style="display: none;" multiple onchange="handleFileSelect(event)">
                                </div>
                            </div>
                        </div>
                    </div>
                    
                    <div class="tool-panel" id="portForwardPanel">
                        <div class="tool-header">
                            <div class="tool-title">Port Forwarding</div>
                            <svg class="tool-close" onclick="closeToolPanel()" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <path d="M18 6L6 18M6 6l12 12"/>
                            </svg>
                        </div>
                        <div class="tool-content">
                            <div style="text-align: center; color: #666; padding: 40px;">
                                Port forwarding coming soon...
                            </div>
                        </div>
                    </div>
                    
                    <div class="tool-panel" id="monitorPanel">
                        <div class="tool-header">
                            <div class="tool-title">System Monitor</div>
                            <svg class="tool-close" onclick="closeToolPanel()" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <path d="M18 6L6 18M6 6l12 12"/>
                            </svg>
                        </div>
                        <div class="tool-content">
                            <div class="monitor-grid">
                                <!-- System Overview -->
                                <div class="monitor-card full-width">
                                    <h3>System Overview</h3>
                                    <div class="monitor-controls">
                                        <button class="monitor-btn active" onclick="setMonitorRefreshRate(5)">5s</button>
                                        <button class="monitor-btn" onclick="setMonitorRefreshRate(10)">10s</button>
                                        <button class="monitor-btn" onclick="setMonitorRefreshRate(30)">30s</button>
                                        <button class="monitor-btn" onclick="pauseMonitoring()">Pause</button>
                                    </div>
                                    <div class="system-overview" id="systemOverview">
                                        <div class="loading-indicator">
                                            <div class="loading-spinner"></div>
                                            Loading system information...
                                        </div>
                                    </div>
                                </div>
                                
                                <!-- CPU & Memory -->
                                <div class="monitor-card">
                                    <h3>CPU & Memory Details</h3>
                                    <div id="cpuMemoryDetails">
                                        <div class="loading-indicator">
                                            <div class="loading-spinner"></div>
                                            Loading...
                                        </div>
                                    </div>
                                </div>
                                
                                <!-- Disk & Network -->
                                <div class="monitor-card">
                                    <h3>Disk & Network</h3>
                                    <div id="diskNetworkDetails">
                                        <div class="loading-indicator">
                                            <div class="loading-spinner"></div>
                                            Loading...
                                        </div>
                                    </div>
                                </div>
                                
                                <!-- Process List -->
                                <div class="monitor-card full-width">
                                    <h3>Top Processes</h3>
                                    <div class="monitor-controls">
                                        <button class="monitor-btn active" onclick="sortProcesses('cpu')">Sort by CPU</button>
                                        <button class="monitor-btn" onclick="sortProcesses('memory')">Sort by Memory</button>
                                        <button class="monitor-btn" onclick="refreshProcesses()">Refresh</button>
                                        <button class="monitor-btn" onclick="testProcessCommand()">Test PS</button>
                                    </div>
                                    <div id="processList">
                                        <div class="loading-indicator">
                                            <div class="loading-spinner"></div>
                                            Loading processes...
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
                
                <!-- Tool Icons Bar -->
                <div class="tool-icons">
                    <div class="tool-icon" onclick="openTool('sftp')" id="sftpIcon">
                        <span class="tool-tooltip">SFTP Browser</span>
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <path d="M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/>
                            <polyline points="13 2 13 9 20 9"/>
                        </svg>
                    </div>
                    <div class="tool-icon" onclick="openTool('portForward')" id="portForwardIcon">
                        <span class="tool-tooltip">Port Forwarding</span>
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <path d="M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3a2 2 0 0 0 2-2v-3M3 16v3a2 2 0 0 0 2 2h3"/>
                            <path d="M12 16l4-4-4-4M16 12H8"/>
                        </svg>
                    </div>
                    <div class="tool-icon" onclick="openTool('monitor')" id="monitorIcon">
                        <span class="tool-tooltip">System Monitor</span>
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <path d="M22 12h-4l-3 9L9 3l-3 9H2"/>
                        </svg>
                    </div>
                </div>
            </div>
        </div>
    </div>
    
    <script>
        let currentSessionId = null;
        let currentTerminal = null;
        let sessions = {};
        let outputPollingInterval = null;
        let fitAddon = null;
        let currentTool = null;
        let currentPath = '/';
        let isLoadingFiles = false;
        let monitorInterval = null;
        let monitorRefreshRate = 5000; // 5 seconds default
        let monitorPaused = false;
        let currentProcessSort = 'cpu';
        
        // Tool panel functions
        function openTool(toolName) {
            // Check if we have an active session
            if (!currentSessionId || !sessions[currentSessionId]) {
                alert('Please connect to a server first');
                return;
            }
            
            // Close current tool if clicking the same icon
            if (currentTool === toolName) {
                closeToolPanel();
                return;
            }
            
            // Reset all tool icons
            document.querySelectorAll('.tool-icon').forEach(icon => {
                icon.classList.remove('active');
            });
            
            // Hide all tool panels
            document.querySelectorAll('.tool-panel').forEach(panel => {
                panel.classList.remove('active');
            });
            
            // Open the selected tool
            currentTool = toolName;
            document.getElementById(toolName + 'Icon').classList.add('active');
            document.getElementById(toolName + 'Panel').classList.add('active');
            document.getElementById('rightSidebar').classList.add('open');
            
            // Initialize tool based on type
            if (toolName === 'sftp') {
                initializeSFTP();
            } else if (toolName === 'monitor') {
                initializeSystemMonitor();
            }
            
            // Resize terminal after sidebar opens
            setTimeout(() => {
                if (sessions[currentSessionId]?.calculateSize) {
                    sessions[currentSessionId].calculateSize();
                }
            }, 350); // After animation completes
        }
        
        function closeToolPanel() {
            // Stop monitoring if it was active
            if (currentTool === 'monitor') {
                stopSystemMonitoring();
            }
            
            currentTool = null;
            document.getElementById('rightSidebar').classList.remove('open');
            document.querySelectorAll('.tool-icon').forEach(icon => {
                icon.classList.remove('active');
            });
            document.querySelectorAll('.tool-panel').forEach(panel => {
                panel.classList.remove('active');
            });
            
            // Resize terminal after sidebar closes
            setTimeout(() => {
                if (sessions[currentSessionId]?.calculateSize) {
                    sessions[currentSessionId].calculateSize();
                }
            }, 350);
        }
        
        // SFTP Functions
        async function initializeSFTP() {
            currentPath = '/home/' + sessions[currentSessionId].username;
            document.getElementById('currentPath').textContent = currentPath;
            await listFiles(currentPath);
        }
        
        async function listFiles(path) {
            // Prevent multiple simultaneous requests
            if (isLoadingFiles) {
                console.log('Already loading files, please wait...');
                return;
            }
            
            isLoadingFiles = true;
            const fileList = document.getElementById('fileList');
            const loadingIndicator = document.getElementById('fileBrowserLoading');
            
            // Show loading state
            fileList.classList.add('loading');
            loadingIndicator.classList.add('active');
            
            try {
                const result = JSON.parse(
                    await window.pywebview.api.list_directory(currentSessionId, path)
                );
                
                if (!result.success) {
                    console.error('Failed to list directory:', result.error);
                    fileList.innerHTML = '<div class="empty-message">Error loading files</div>';
                    return;
                }
                
                fileList.innerHTML = '';
                
                // Add parent directory if not at root
                if (path !== '/') {
                    const parentItem = document.createElement('div');
                    parentItem.className = 'file-item';
                    parentItem.innerHTML = `
                        <svg class="file-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>
                        </svg>
                        <span class="file-name">..</span>
                        <span class="file-size">-</span>
                        <span class="file-date">-</span>
                    `;
                    parentItem.ondblclick = () => {
                        if (!isLoadingFiles) navigateUp();
                    };
                    parentItem.onclick = () => selectFile(parentItem);
                    fileList.appendChild(parentItem);
                }
                
                // Add files and directories
                result.files.forEach(file => {
                    const item = document.createElement('div');
                    item.className = 'file-item';
                    item.innerHTML = `
                        <svg class="file-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            ${file.type === 'directory' ? 
                                '<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>' :
                                '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/>'
                            }
                        </svg>
                        <span class="file-name" title="${file.name}">${file.name}</span>
                        <span class="file-size">${file.size}</span>
                        <span class="file-date">${file.date}</span>
                    `;
                    
                    if (file.type === 'directory') {
                        item.ondblclick = () => {
                            if (!isLoadingFiles) navigateToFolder(file.name);
                        };
                    } else {
                        item.ondblclick = () => {
                            if (!isLoadingFiles) downloadFile(file.name);
                        };
                    }
                    
                    item.onclick = () => selectFile(item);
                    fileList.appendChild(item);
                });
                
                // Scroll to top after loading
                fileList.scrollTop = 0;
                
            } catch (error) {
                console.error('Error listing files:', error);
                fileList.innerHTML = '<div class="empty-message">Error loading files</div>';
            } finally {
                // Hide loading state
                isLoadingFiles = false;
                fileList.classList.remove('loading');
                loadingIndicator.classList.remove('active');
            }
        }
        
        function selectFile(element) {
            document.querySelectorAll('.file-item').forEach(item => {
                item.classList.remove('selected');
            });
            element.classList.add('selected');
        }
        
        function navigateUp() {
            if (isLoadingFiles) return;
            
            const parts = currentPath.split('/').filter(p => p);
            parts.pop();
            currentPath = '/' + parts.join('/');
            if (currentPath === '/') currentPath = '/';
            document.getElementById('currentPath').textContent = currentPath;
            listFiles(currentPath);
        }
        
        function navigateToFolder(folderName) {
            if (isLoadingFiles) return;
            
            currentPath = currentPath.endsWith('/') ? 
                currentPath + folderName : 
                currentPath + '/' + folderName;
            document.getElementById('currentPath').textContent = currentPath;
            listFiles(currentPath);
        }
        
        function refreshFiles() {
            if (isLoadingFiles) return;
            listFiles(currentPath);
        }
        
        function createNewFolder() {
            const folderName = prompt('Enter folder name:');
            if (folderName) {
                const fullPath = currentPath.endsWith('/') ? 
                    currentPath + folderName : 
                    currentPath + '/' + folderName;
                
                window.pywebview.api.create_directory(currentSessionId, fullPath).then(result => {
                    const res = JSON.parse(result);
                    if (res.success) {
                        refreshFiles();
                    } else {
                        alert('Failed to create folder');
                    }
                });
            }
        }
        
        async function downloadFile(fileName) {
            const remotePath = currentPath.endsWith('/') ? 
                currentPath + fileName : 
                currentPath + '/' + fileName;
            
            // For now, just log - you'd need to implement file save dialog
            console.log('Downloading:', remotePath);
            alert('File download functionality will be implemented with file save dialog');
        }
        
        function selectFiles() {
            document.getElementById('fileInput').click();
        }
        
        function handleFileSelect(event) {
            const files = event.target.files;
            if (files.length > 0) {
                // Upload each file
                Array.from(files).forEach(file => {
                    const remotePath = currentPath.endsWith('/') ? 
                        currentPath + file.name : 
                        currentPath + '/' + file.name;
                    
                    console.log('Uploading:', file.name, 'to', remotePath);
                    // You'd need to implement actual file upload here
                    alert('File upload functionality will be implemented');
                });
            }
        }
        
        // Drag and drop support
        const setupDragDrop = () => {
            const uploadArea = document.getElementById('uploadArea');
            if (!uploadArea) return;
            
            uploadArea.addEventListener('dragover', (e) => {
                e.preventDefault();
                uploadArea.classList.add('dragover');
            });
            
            uploadArea.addEventListener('dragleave', () => {
                uploadArea.classList.remove('dragover');
            });
            
            uploadArea.addEventListener('drop', (e) => {
                e.preventDefault();
                uploadArea.classList.remove('dragover');
                const files = e.dataTransfer.files;
                if (files.length > 0) {
                    console.log('Dropped files:', files);
                    // Implement SFTP upload
                }
            });
        };
        
        // System Monitor Functions
        async function initializeSystemMonitor() {
            console.log('Initializing system monitor...');
            startSystemMonitoring();
        }
        
        function startSystemMonitoring() {
            if (monitorInterval) {
                clearInterval(monitorInterval);
            }
            
            if (!monitorPaused) {
                // Load data immediately
                updateSystemInfo();
                updateProcessList();
                
                // Set up periodic updates
                monitorInterval = setInterval(() => {
                    if (!monitorPaused && currentTool === 'monitor') {
                        updateSystemInfo();
                        updateProcessList();
                    }
                }, monitorRefreshRate);
            }
        }
        
        function stopSystemMonitoring() {
            if (monitorInterval) {
                clearInterval(monitorInterval);
                monitorInterval = null;
            }
        }
        
        async function updateSystemInfo() {
            if (!currentSessionId) return;
            
            try {
                const result = JSON.parse(await window.pywebview.api.get_system_info(currentSessionId));
                if (result.success) {
                    displaySystemInfo(result.info);
                } else {
                    console.error('Failed to get system info:', result.error);
                }
            } catch (error) {
                console.error('Error getting system info:', error);
            }
        }
        
        function displaySystemInfo(info) {
            const overview = document.getElementById('systemOverview');
            const cpuMemory = document.getElementById('cpuMemoryDetails');
            const diskNetwork = document.getElementById('diskNetworkDetails');
            
            // System Overview
            overview.innerHTML = `
                <div class="metric-item">
                    <div class="metric-label">CPU Usage</div>
                    <div class="metric-value">${info.cpu?.usage?.toFixed(1) || 0}%</div>
                    <div class="progress-bar">
                        <div class="progress-fill ${getUsageClass(info.cpu?.usage || 0)}" 
                             style="width: ${info.cpu?.usage || 0}%"></div>
                    </div>
                </div>
                <div class="metric-item">
                    <div class="metric-label">Memory Usage</div>
                    <div class="metric-value">${info.memory?.percentage || 0}%</div>
                    <div class="metric-subtext">${info.memory?.used || 0}MB / ${info.memory?.total || 0}MB</div>
                    <div class="progress-bar">
                        <div class="progress-fill ${getUsageClass(info.memory?.percentage || 0)}" 
                             style="width: ${info.memory?.percentage || 0}%"></div>
                    </div>
                </div>
                <div class="metric-item">
                    <div class="metric-label">Disk Usage</div>
                    <div class="metric-value">${info.disk?.percentage || 0}%</div>
                    <div class="metric-subtext">${info.disk?.used || 0} / ${info.disk?.total || 0}</div>
                    <div class="progress-bar">
                        <div class="progress-fill ${getUsageClass(info.disk?.percentage || 0)}" 
                             style="width: ${info.disk?.percentage || 0}%"></div>
                    </div>
                </div>
                <div class="metric-item">
                    <div class="metric-label">Load Average</div>
                    <div class="metric-value">${(info.load_average || [0])[0]?.toFixed(2) || '0.00'}</div>
                    <div class="metric-subtext">1m: ${(info.load_average || [0, 0, 0])[0]?.toFixed(2) || '0.00'}, 
                                              5m: ${(info.load_average || [0, 0, 0])[1]?.toFixed(2) || '0.00'}, 
                                              15m: ${(info.load_average || [0, 0, 0])[2]?.toFixed(2) || '0.00'}</div>
                </div>
            `;
            
            // CPU & Memory Details
            cpuMemory.innerHTML = `
                <div class="metric-item">
                    <div class="metric-label">CPU Model</div>
                    <div class="metric-value" style="font-size: 12px; line-height: 1.3;">
                        ${info.cpu?.model || 'Unknown'}
                    </div>
                    <div class="metric-subtext">${info.cpu?.cores || 1} cores</div>
                </div>
                <div class="metric-item">
                    <div class="metric-label">Memory Available</div>
                    <div class="metric-value">${info.memory?.available || 0}MB</div>
                    <div class="metric-subtext">Available for new processes</div>
                </div>
                <div class="metric-item">
                    <div class="metric-label">Uptime</div>
                    <div class="metric-value" style="font-size: 12px; line-height: 1.3;">
                        ${info.uptime || 'Unknown'}
                    </div>
                </div>
            `;
            
            // Disk & Network
            diskNetwork.innerHTML = `
                <div class="metric-item">
                    <div class="metric-label">Disk Available</div>
                    <div class="metric-value">${info.disk?.available || '0'}</div>
                    <div class="metric-subtext">Free space remaining</div>
                </div>
                <div class="metric-item">
                    <div class="metric-label">Network Status</div>
                    <div class="metric-value" style="font-size: 10px; line-height: 1.2; max-height: 60px; overflow-y: auto;">
                        ${info.network ? info.network.substring(0, 200) + '...' : 'No data'}
                    </div>
                </div>
            `;
        }
        
        async function updateProcessList() {
            if (!currentSessionId) return;
            
            try {
                const result = JSON.parse(await window.pywebview.api.get_process_list(currentSessionId, 15));
                console.log('Process list result:', result); // Debug log
                
                if (result.success) {
                    console.log('Number of processes:', result.processes?.length || 0);
                    displayProcessList(result.processes);
                } else {
                    console.error('Failed to get process list:', result.error);
                    document.getElementById('processList').innerHTML = 
                        `<div class="loading-indicator">Error: ${result.error}</div>`;
                }
            } catch (error) {
                console.error('Error getting process list:', error);
                document.getElementById('processList').innerHTML = 
                    `<div class="loading-indicator">Error loading processes: ${error.message}</div>`;
            }
        }
        
        function displayProcessList(processes) {
            const container = document.getElementById('processList');
            
            console.log('Displaying processes:', processes); // Debug log
            
            if (!processes || processes.length === 0) {
                container.innerHTML = '<div class="loading-indicator">No processes found. Check console for errors.</div>';
                return;
            }
            
            // Sort processes based on current sort criteria
            processes.sort((a, b) => {
                if (currentProcessSort === 'cpu') {
                    return b.cpu - a.cpu;
                } else if (currentProcessSort === 'memory') {
                    return b.memory - a.memory;
                }
                return 0;
            });
            
            const tableHTML = `
                <table class="process-table">
                    <thead>
                        <tr>
                            <th>PID</th>
                            <th>User</th>
                            <th>CPU%</th>
                            <th>MEM%</th>
                            <th>Command</th>
                        </tr>
                    </thead>
                    <tbody>
                        ${processes.map(proc => `
                            <tr>
                                <td>${proc.pid}</td>
                                <td>${proc.user}</td>
                                <td class="cpu-cell">${proc.cpu.toFixed(1)}%</td>
                                <td class="mem-cell">${proc.memory.toFixed(1)}%</td>
                                <td class="command-cell" title="${proc.command}">${proc.command}</td>
                            </tr>
                        `).join('')}
                    </tbody>
                </table>
            `;
            
            container.innerHTML = tableHTML;
        }
        
        function getUsageClass(percentage) {
            if (percentage >= 90) return 'danger';
            if (percentage >= 75) return 'warning';
            return '';
        }
        
        function setMonitorRefreshRate(seconds) {
            monitorRefreshRate = seconds * 1000;
            monitorPaused = false;
            
            // Update button states
            document.querySelectorAll('.monitor-btn').forEach(btn => {
                btn.classList.remove('active');
            });
            event.target.classList.add('active');
            
            startSystemMonitoring();
        }
        
        function pauseMonitoring() {
            monitorPaused = !monitorPaused;
            const btn = event.target;
            
            if (monitorPaused) {
                stopSystemMonitoring();
                btn.textContent = 'Resume';
                btn.classList.add('active');
            } else {
                startSystemMonitoring();
                btn.textContent = 'Pause';
                btn.classList.remove('active');
            }
        }
        
        function sortProcesses(sortBy) {
            currentProcessSort = sortBy;
            
            // Update button states
            document.querySelectorAll('.monitor-controls .monitor-btn').forEach(btn => {
                btn.classList.remove('active');
            });
            event.target.classList.add('active');
            
            updateProcessList();
        }
        
        function refreshProcesses() {
            updateProcessList();
        }
        
        async function testProcessCommand() {
            if (!currentSessionId) return;
            
            try {
                const result = JSON.parse(await window.pywebview.api.execute_command(currentSessionId, "ps aux | head -5"));
                console.log('Test ps command result:', result);
                
                if (result.success) {
                    alert('PS command output (first 5 lines):\\n' + result.output);
                } else {
                    alert('PS command failed: ' + result.error);
                }
            } catch (error) {
                console.error('Error testing PS command:', error);
                alert('Error testing PS command: ' + error.message);
            }
        }
        
        // Toggle collapsible sections
        function toggleSection(sectionName) {
            const content = document.getElementById(sectionName + 'Content');
            const chevron = document.getElementById(sectionName + 'Chevron');
            
            content.classList.toggle('open');
            chevron.classList.toggle('open');
        }
        
        // Load saved connections on startup
        async function loadSavedConnections() {
            try {
                console.log('Calling get_saved_connections...');
                const response = await window.pywebview.api.get_saved_connections();
                console.log('Response from get_saved_connections:', response);
                const connections = JSON.parse(response);
                console.log('Parsed connections:', connections);
                updateSavedConnectionsList(connections);
            } catch (error) {
                console.error('Error loading saved connections:', error);
            }
        }
        
        function updateSavedConnectionsList(connections) {
            const container = document.getElementById('savedConnectionsList');
            container.innerHTML = '';
            
            if (connections.length === 0) {
                container.innerHTML = '<div class="empty-message">No saved connections</div>';
                return;
            }
            
            connections.forEach(conn => {
                const item = document.createElement('div');
                item.className = 'saved-connection-item';
                item.innerHTML = `
                    <div class="saved-connection-info" onclick="loadConnection('${conn.key}')">
                        <div class="saved-connection-name">${conn.name || conn.key}</div>
                        <div class="saved-connection-details">${conn.hostname}:${conn.port}</div>
                    </div>
                    <div class="saved-connection-actions">
                        <button class="action-btn" onclick="quickConnect('${conn.key}'); event.stopPropagation();">Connect</button>
                        <button class="action-btn delete" onclick="deleteConnection('${conn.key}'); event.stopPropagation();">Delete</button>
                    </div>
                `;
                container.appendChild(item);
            });
        }
        
        async function loadConnection(key) {
            const connections = JSON.parse(await window.pywebview.api.get_saved_connections());
            const conn = connections.find(c => c.key === key);
            if (conn) {
                document.getElementById('hostname').value = conn.hostname;
                document.getElementById('port').value = conn.port || 22;
                document.getElementById('username').value = conn.username;
                
                if (conn.password) {
                    document.getElementById('authType').value = 'password';
                    document.getElementById('password').value = conn.password;
                    document.getElementById('passwordGroup').style.display = 'block';
                    document.getElementById('keyGroup').style.display = 'none';
                } else if (conn.keyPath) {
                    document.getElementById('authType').value = 'key';
                    document.getElementById('keyPath').value = conn.keyPath;
                    document.getElementById('passwordGroup').style.display = 'none';
                    document.getElementById('keyGroup').style.display = 'block';
                }
            }
        }
        
        async function quickConnect(key) {
            await loadConnection(key);
            await connect();
        }
        
        async function deleteConnection(key) {
            if (confirm('Are you sure you want to delete this saved connection?')) {
                await window.pywebview.api.delete_saved_connection(key);
                await loadSavedConnections();
            }
        }
        
        // Wait for page to load
        window.addEventListener('DOMContentLoaded', () => {
            console.log('Page loaded, checking Terminal availability...');
            
            // Check if Terminal is available
            if (typeof Terminal === 'undefined') {
                console.error('Terminal library not loaded!');
                alert('Error: Terminal library failed to load. Please check your internet connection and refresh the page.');
                return;
            }
            
            console.log('Terminal library loaded successfully');
            
            // Wait for pywebview API to be ready
            function waitForAPI() {
                if (window.pywebview && window.pywebview.api) {
                    console.log('PyWebView API ready, loading saved connections...');
                    // Load saved connections
                    loadSavedConnections();
                    // Setup drag and drop
                    setupDragDrop();
                } else {
                    console.log('Waiting for PyWebView API...');
                    setTimeout(waitForAPI, 100);
                }
            }
            
            waitForAPI();
            
            // Handle auth type change
            document.getElementById('authType').addEventListener('change', (e) => {
                if (e.target.value === 'password') {
                    document.getElementById('passwordGroup').style.display = 'block';
                    document.getElementById('keyGroup').style.display = 'none';
                } else {
                    document.getElementById('passwordGroup').style.display = 'none';
                    document.getElementById('keyGroup').style.display = 'block';
                }
            });
        });
        
        async function connect() {
            const hostname = document.getElementById('hostname').value;
            const port = document.getElementById('port').value || 22;
            const username = document.getElementById('username').value;
            const authType = document.getElementById('authType').value;
            const password = authType === 'password' ? document.getElementById('password').value : null;
            const keyPath = authType === 'key' ? document.getElementById('keyPath').value : null;
            const saveConnection = document.getElementById('saveConnection').checked;
            
            if (!hostname || !username) {
                alert('Please fill in hostname and username');
                return;
            }
            
            // Show connecting screen
            document.getElementById('welcomeScreen').style.display = 'none';
            document.getElementById('connectingScreen').style.display = 'block';
            
            try {
                // Create new session
                const sessionId = await window.pywebview.api.create_session();
                
                // Connect
                const connectionParams = JSON.stringify({
                    hostname,
                    port: parseInt(port),
                    username,
                    password,
                    keyPath,
                    save: saveConnection
                });
                
                const result = JSON.parse(await window.pywebview.api.connect(sessionId, connectionParams));
                
                if (result.success) {
                    // Create terminal
                    createTerminalForSession(sessionId, hostname);
                    
                    // Add to sessions list
                    sessions[sessionId] = {
                        id: sessionId,
                        hostname,
                        username,
                        terminal: currentTerminal
                    };
                    
                    updateSessionsList();
                    switchToSession(sessionId);
                    
                    // Start polling for output
                    startOutputPolling(sessionId);
                    
                    // Reload saved connections if a new one was saved
                    if (saveConnection) {
                        await loadSavedConnections();
                    }
                } else {
                    alert('Connection failed: ' + (result.error || 'Unknown error'));
                    document.getElementById('connectingScreen').style.display = 'none';
                    document.getElementById('welcomeScreen').style.display = 'flex';
                }
            } catch (error) {
                alert('Connection error: ' + error);
                document.getElementById('connectingScreen').style.display = 'none';
                document.getElementById('welcomeScreen').style.display = 'flex';
            }
        }
        
        function createTerminalForSession(sessionId, hostname) {
            try {
                // Clear the terminal container first
                const terminalElement = document.getElementById('terminal');
                terminalElement.innerHTML = '';
                
                const terminal = new Terminal({
                    cursorBlink: true,
                    fontSize: 14,
                    fontFamily: 'Consolas, "Courier New", monospace',
                    theme: {
                        background: '#000000',
                        foreground: '#ffffff',
                        cursor: '#00ff88'
                    },
                    scrollback: 10000,
                    convertEol: true,
                    windowsMode: true
                });
                
                // Create fit addon
                let terminalFitAddon = null;
                if (typeof FitAddon !== 'undefined') {
                    terminalFitAddon = new FitAddon.FitAddon();
                    terminal.loadAddon(terminalFitAddon);
                }
                
                // Open terminal
                terminal.open(terminalElement);
                
                // Get container dimensions and calculate rows/cols
                const calculateTerminalSize = () => {
                    const wrapper = document.getElementById('terminalWrapper');
                    if (!wrapper) return;
                    
                    const rect = wrapper.getBoundingClientRect();
                    const padding = 16; // Account for padding
                    const availableHeight = rect.height - padding * 2;
                    const availableWidth = rect.width - padding * 2;
                    
                    // Estimate character dimensions
                    const charHeight = 17; // Approximate line height for 14px font
                    const charWidth = 8; // Approximate char width
                    
                    const rows = Math.floor(availableHeight / charHeight);
                    const cols = Math.floor(availableWidth / charWidth);
                    
                    console.log(`Terminal size: ${cols}x${rows} (${availableWidth}x${availableHeight}px)`);
                    
                    // Manually resize terminal
                    if (rows > 0 && cols > 0) {
                        terminal.resize(cols, rows);
                    }
                    
                    // Then try fit addon
                    if (terminalFitAddon) {
                        try {
                            terminalFitAddon.fit();
                        } catch (e) {
                            console.error('Fit addon error:', e);
                        }
                    }
                };
                
                // Calculate size after delays
                setTimeout(calculateTerminalSize, 50);
                setTimeout(calculateTerminalSize, 200);
                setTimeout(calculateTerminalSize, 500);
                
                terminal.focus();
                
                // Handle input
                terminal.onData(async (data) => {
                    await window.pywebview.api.send_input(sessionId, data);
                });
                
                // Handle resize
                terminal.onResize(async ({ cols, rows }) => {
                    console.log(`Terminal resized to ${cols}x${rows}`);
                    await window.pywebview.api.resize_terminal(sessionId, cols, rows);
                });
                
                currentTerminal = terminal;
                sessions[sessionId] = { 
                    ...sessions[sessionId], 
                    terminal, 
                    fitAddon: terminalFitAddon,
                    calculateSize: calculateTerminalSize
                };
                
                // Set up resize observer
                const resizeObserver = new ResizeObserver(() => {
                    if (currentSessionId === sessionId) {
                        calculateTerminalSize();
                    }
                });
                
                const wrapper = document.getElementById('terminalWrapper');
                if (wrapper) {
                    resizeObserver.observe(wrapper);
                }
                
            } catch (error) {
                console.error('Error creating terminal:', error);
                alert('Failed to create terminal: ' + error.message);
            }
        }
        
        async function startOutputPolling(sessionId) {
            if (outputPollingInterval) {
                clearInterval(outputPollingInterval);
            }
            
            outputPollingInterval = setInterval(async () => {
                if (currentSessionId === sessionId) {
                    try {
                        // Get output
                        const result = JSON.parse(await window.pywebview.api.get_output(sessionId));
                        if (result.output) {
                            currentTerminal.write(result.output);
                        }
                        
                        // Check session status
                        const statusResult = JSON.parse(await window.pywebview.api.get_status(sessionId));
                        if (!statusResult.connected || statusResult.logout_detected) {
                            // Session disconnected
                            handleSessionDisconnect(sessionId, statusResult.logout_detected);
                        }
                    } catch (error) {
                        console.error('Error polling output:', error);
                    }
                }
            }, 50); // Poll every 50ms
        }
        
        function handleSessionDisconnect(sessionId, wasLogout) {
            if (!sessions[sessionId]) return;
            
            // Stop polling
            if (outputPollingInterval) {
                clearInterval(outputPollingInterval);
                outputPollingInterval = null;
            }
            
            // Update UI
            const message = wasLogout ? 
                '\\r\\n\\r\\n[Session ended - User logged out]\\r\\n' : 
                '\\r\\n\\r\\n[Session ended - Connection lost]\\r\\n';
            
            if (sessions[sessionId].terminal) {
                sessions[sessionId].terminal.write(message);
            }
            
            // Update status
            sessions[sessionId].connected = false;
            updateSessionsList();
            
            // Update status bar
            if (currentSessionId === sessionId) {
                document.getElementById('statusBar').style.display = 'none';
                
                // Show reconnect option
                setTimeout(() => {
                    if (confirm('Connection lost. Would you like to reconnect?')) {
                        reconnectSession(sessionId);
                    }
                }, 500);
            }
            
            // Remove session after delay
            setTimeout(() => {
                if (sessions[sessionId] && !sessions[sessionId].connected) {
                    delete sessions[sessionId];
                    updateSessionsList();
                    
                    // If this was the current session, show welcome screen
                    if (currentSessionId === sessionId) {
                        currentSessionId = null;
                        currentTerminal = null;
                        document.getElementById('terminalWrapper').style.display = 'none';
                        document.getElementById('welcomeScreen').style.display = 'flex';
                    }
                }
            }, 30000); // Clean up after 30 seconds
        }
        
        async function reconnectSession(oldSessionId) {
            const session = sessions[oldSessionId];
            if (!session) return;
            
            // Create new connection with same parameters
            document.getElementById('hostname').value = session.hostname;
            document.getElementById('username').value = session.username;
            
            // Remove old session
            delete sessions[oldSessionId];
            updateSessionsList();
            
            // Connect
            await connect();
        }
        
        function switchToSession(sessionId) {
            currentSessionId = sessionId;
            
            // Hide all terminals
            document.getElementById('welcomeScreen').style.display = 'none';
            document.getElementById('connectingScreen').style.display = 'none';
            document.getElementById('terminalWrapper').style.display = 'block';
            
            // Show status bar
            document.getElementById('statusBar').style.display = 'flex';
            document.getElementById('statusHost').textContent = sessions[sessionId].hostname;
            
            // Focus terminal and force resize
            if (sessions[sessionId].terminal) {
                currentTerminal = sessions[sessionId].terminal;
                currentTerminal.focus();
                
                // Force fit after switching
                setTimeout(() => {
                    if (sessions[sessionId].fitAddon) {
                        try {
                            sessions[sessionId].fitAddon.fit();
                        } catch (e) {
                            console.error('Error fitting terminal on switch:', e);
                        }
                    }
                }, 100);
                
                startOutputPolling(sessionId);
            }
            
            updateSessionsList();
        }
        
        function updateSessionsList() {
            const container = document.getElementById('sessionsList');
            container.innerHTML = '';
            
            const sessionValues = Object.values(sessions);
            if (sessionValues.length === 0) {
                container.innerHTML = '<div class="empty-message">No active sessions</div>';
                return;
            }
            
            // Open the active sessions section if there are sessions
            if (sessionValues.length > 0) {
                document.getElementById('activeSessionsContent').classList.add('open');
                document.getElementById('activeSessionsChevron').classList.add('open');
            }
            
            sessionValues.forEach(session => {
                const item = document.createElement('div');
                item.className = 'session-item' + (session.id === currentSessionId ? ' active' : '');
                item.innerHTML = `
                    <div class="session-status"></div>
                    <div class="session-info">
                        <div class="session-name">${session.username}@${session.hostname}</div>
                        <div class="session-host">Session ${session.id.split('_')[1]}</div>
                    </div>
                `;
                item.onclick = () => switchToSession(session.id);
                container.appendChild(item);
            });
        }
        
        // Window resize handling
        window.addEventListener('resize', () => {
            if (currentTerminal && sessions[currentSessionId]) {
                setTimeout(() => {
                    if (sessions[currentSessionId].calculateSize) {
                        sessions[currentSessionId].calculateSize();
                    }
                }, 100);
            }
        });
    </script>
</body>
</html>
"""


def main():
    # Print debug info
    print("PrismSSH Starting...")
    print(f"Config directory: {os.path.expanduser('~/.prismssh')}")
    print(f"Encryption available: {ENCRYPTION_AVAILABLE}")
    
    # Create API instance
    api = API()
    
    # Check if connections file exists
    connections_file = os.path.join(os.path.expanduser("~/.prismssh"), "connections.json")
    if os.path.exists(connections_file):
        print(f"Found existing connections file: {connections_file}")
        try:
            with open(connections_file, 'r') as f:
                data = json.load(f)
                print(f"Loaded {len(data)} saved connections")
        except Exception as e:
            print(f"Error reading connections file: {e}")
    else:
        print("No existing connections file found")
    
    # Create window
    window = webview.create_window(
        title='PrismSSH - Modern SSH Client',
        html=HTML_CONTENT,
        js_api=api,
        width=1200,
        height=800,
        min_size=(800, 600)
    )
    
    # Start the GUI
    webview.start()


if __name__ == '__main__':
    main()
