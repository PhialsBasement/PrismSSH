"""SSH session management for PrismSSH."""

import threading
import queue
import time
import stat
import socket
import select
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
    
    def __init__(self, session_id: str, config: Config, host_key_verify_callback=None):
        self.id = session_id
        self.config = config
        self.logger = Logger.get_logger(__name__)
        
        self.client = SSHClient(config)
        if host_key_verify_callback:
            self.client.set_host_key_verify_callback(host_key_verify_callback)
        
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
        
        # Port forwarding
        self.port_forwards = {}  # {forward_id: forward_info}
        self.forward_threads = {}  # {forward_id: thread}
        
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
    
    def download_file(self, remote_path: str, local_path: str, progress_callback=None) -> bool:
        """Download a file via SFTP with optional progress tracking."""
        if not self.sftp:
            raise SFTPError("SFTP not available")
        
        try:
            if progress_callback:
                # Get file size for progress tracking
                file_stat = self.sftp.stat(remote_path)
                file_size = file_stat.st_size
                
                def sftp_progress_callback(transferred, total):
                    if total > 0:
                        progress_percent = (transferred / total) * 100
                        progress_callback(transferred, total, progress_percent)
                
                self.sftp.get(remote_path, local_path, callback=sftp_progress_callback)
            else:
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
    
    def delete_file(self, path: str) -> bool:
        """Delete a file via SFTP."""
        if not self.sftp:
            raise SFTPError("SFTP not available")
        
        try:
            self.sftp.remove(path)
            self.logger.info(f"Deleted file {path}")
            return True
        except Exception as e:
            self.logger.error(f"Error deleting file {path}: {e}")
            raise SFTPError(f"Failed to delete file: {str(e)}")
    
    def delete_directory(self, path: str) -> bool:
        """Delete a directory via SFTP."""
        if not self.sftp:
            raise SFTPError("SFTP not available")
        
        try:
            self.sftp.rmdir(path)
            self.logger.info(f"Deleted directory {path}")
            return True
        except Exception as e:
            self.logger.error(f"Error deleting directory {path}: {e}")
            raise SFTPError(f"Failed to delete directory: {str(e)}")
    
    def rename_file(self, old_path: str, new_path: str) -> bool:
        """Rename/move a file via SFTP."""
        if not self.sftp:
            raise SFTPError("SFTP not available")
        
        try:
            self.sftp.rename(old_path, new_path)
            self.logger.info(f"Renamed {old_path} to {new_path}")
            return True
        except Exception as e:
            self.logger.error(f"Error renaming {old_path} to {new_path}: {e}")
            raise SFTPError(f"Failed to rename file: {str(e)}")
    
    def upload_file_content(self, file_content: bytes, remote_path: str) -> bool:
        """Upload file content directly via SFTP."""
        if not self.sftp:
            raise SFTPError("SFTP not available")
        
        try:
            import io
            file_like = io.BytesIO(file_content)
            self.sftp.putfo(file_like, remote_path)
            self.logger.info(f"Uploaded content to {remote_path}")
            return True
        except Exception as e:
            self.logger.error(f"Error uploading content to {remote_path}: {e}")
            raise SFTPError(f"Failed to upload file content: {str(e)}")
    
    def download_file_content(self, remote_path: str, progress_callback=None) -> bytes:
        """Download file content via SFTP with MAXIMUM performance."""
        if not self.sftp:
            raise SFTPError("SFTP not available")
        
        try:
            # Check file size first
            file_stat = self.sftp.stat(remote_path)
            file_size = file_stat.st_size
            
            self.logger.info(f"Fast downloading file {remote_path} ({file_size} bytes)")
            
            # Use fast native SFTP get method with temporary file - FASTEST approach
            import tempfile
            import os
            
            # Create a temporary file to download to
            temp_fd, temp_path = tempfile.mkstemp()
            
            try:
                os.close(temp_fd)  # Close the file descriptor, we just need the path
                
                # Progress tracking with cancellation support
                cancelled = [False]
                
                def sftp_progress_callback(transferred, total):
                    if progress_callback and total > 0:
                        progress_percent = (transferred / total) * 100
                        try:
                            progress_callback(transferred, total, progress_percent)
                        except Exception as e:
                            if "cancelled" in str(e).lower():
                                cancelled[0] = True
                                raise Exception("Download cancelled by user")
                
                # Use native SFTP get - this is orders of magnitude faster
                if progress_callback:
                    try:
                        self.sftp.get(remote_path, temp_path, callback=sftp_progress_callback)
                    except Exception as e:
                        if cancelled[0] or "cancelled" in str(e).lower():
                            self.logger.info("Download cancelled by user")
                            raise SFTPError("Download cancelled by user")
                        raise
                else:
                    self.sftp.get(remote_path, temp_path)
                
                # Read the downloaded file content in one go
                with open(temp_path, 'rb') as f:
                    content = f.read()
                
                # Final progress update
                if progress_callback:
                    progress_callback(file_size, file_size, 100.0)
                
            finally:
                # Clean up temp file
                try:
                    if os.path.exists(temp_path):
                        os.unlink(temp_path)
                except Exception as cleanup_error:
                    self.logger.warning(f"Failed to cleanup temp file {temp_path}: {cleanup_error}")
            
            self.logger.info(f"Successfully fast downloaded {len(content)} bytes from {remote_path}")
            return content
            
        except SFTPError:
            # Re-raise our custom errors
            raise
        except Exception as e:
            self.logger.error(f"Error downloading content from {remote_path}: {e}")
            raise SFTPError(f"Failed to download file content: {str(e)}")
    
    def get_file_info(self, remote_path: str) -> Dict[str, Any]:
        """Get file information via SFTP."""
        if not self.sftp:
            raise SFTPError("SFTP not available")
        
        try:
            stat = self.sftp.stat(remote_path)
            import time
            return {
                'size': stat.st_size,
                'modified': time.ctime(stat.st_mtime),
                'permissions': oct(stat.st_mode)[-3:],
                'is_file': stat.st_mode & 0o100000 != 0,
                'is_dir': stat.st_mode & 0o040000 != 0
            }
        except Exception as e:
            self.logger.error(f"Error getting file info for {remote_path}: {e}")
            raise SFTPError(f"Failed to get file info: {str(e)}")
    
    def disconnect(self):
        """Disconnect the session."""
        self.logger.info(f"Disconnecting session {self.id}")
        
        # Stop all port forwards first
        self._stop_all_port_forwards()
        
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
    
    def _execute_command(self, command: str, timeout: int = 10) -> str:
        """Execute a command and return output."""
        try:
            if not self.connected or not self.client.is_connected():
                raise Exception("Session not connected")
            
            stdin, stdout, stderr = self.client.client.exec_command(command, timeout=timeout)
            output = stdout.read().decode('utf-8', errors='ignore')
            error = stderr.read().decode('utf-8', errors='ignore')
            
            if error.strip():
                self.logger.warning(f"Command '{command}' produced error: {error.strip()}")
            
            return output.strip()
        except Exception as e:
            self.logger.error(f"Error executing command '{command}': {e}")
            raise
    
    def _detect_os(self) -> str:
        """Detect the operating system of the remote host."""
        try:
            # Try Windows first
            result = self._execute_command("echo %OS%", timeout=5)
            if "Windows" in result:
                return "windows"
            
            # Try Linux/Unix
            result = self._execute_command("uname -s", timeout=5)
            if result:
                return "linux"
            
            return "unknown"
        except:
            return "unknown"
    
    def get_system_info(self) -> Dict[str, Any]:
        """Get basic system information."""
        try:
            os_type = self._detect_os()
            
            if os_type == "windows":
                return self._get_windows_system_info()
            elif os_type == "linux":
                return self._get_linux_system_info()
            else:
                return {"error": "Unknown operating system"}
                
        except Exception as e:
            self.logger.error(f"Error getting system info: {e}")
            return {"error": str(e)}
    
    def _get_windows_system_info(self) -> Dict[str, Any]:
        """Get Windows system information."""
        try:
            info = {}
            
            # Get OS info
            os_info = self._execute_command('systeminfo | findstr /B /C:"OS Name" /C:"OS Version" /C:"System Type"')
            for line in os_info.split('\n'):
                if 'OS Name' in line:
                    info['os_name'] = line.split(':', 1)[1].strip()
                elif 'OS Version' in line:
                    info['os_version'] = line.split(':', 1)[1].strip()
                elif 'System Type' in line:
                    info['architecture'] = line.split(':', 1)[1].strip()
            
            # Get hostname
            hostname = self._execute_command('hostname')
            info['hostname'] = hostname.strip()
            
            # Get uptime
            uptime = self._execute_command('systeminfo | findstr /B /C:"System Boot Time"')
            if uptime:
                info['uptime'] = uptime.split(':', 1)[1].strip()
            
            # Get CPU info
            cpu_info = self._execute_command('wmic cpu get name /value')
            for line in cpu_info.split('\n'):
                if 'Name=' in line:
                    info['cpu'] = line.split('=', 1)[1].strip()
                    break
            
            # Get memory info
            mem_info = self._execute_command('systeminfo | findstr /B /C:"Total Physical Memory"')
            if mem_info:
                info['total_memory'] = mem_info.split(':', 1)[1].strip()
            
            return info
            
        except Exception as e:
            return {"error": f"Error getting Windows system info: {e}"}
    
    def _get_linux_system_info(self) -> Dict[str, Any]:
        """Get Linux system information."""
        try:
            info = {}
            
            # Get OS info
            try:
                os_info = self._execute_command('cat /etc/os-release')
                for line in os_info.split('\n'):
                    if line.startswith('PRETTY_NAME='):
                        info['os_name'] = line.split('=', 1)[1].strip('"')
                    elif line.startswith('VERSION='):
                        info['os_version'] = line.split('=', 1)[1].strip('"')
            except:
                # Fallback
                info['os_name'] = self._execute_command('uname -s')
                info['os_version'] = self._execute_command('uname -r')
            
            # Get hostname
            info['hostname'] = self._execute_command('hostname')
            
            # Get architecture
            info['architecture'] = self._execute_command('uname -m')
            
            # Get uptime
            uptime = self._execute_command('uptime -s')
            if uptime:
                info['uptime'] = f"Since {uptime}"
            
            # Get CPU info
            cpu_info = self._execute_command('cat /proc/cpuinfo | grep "model name" | head -1')
            if cpu_info:
                info['cpu'] = cpu_info.split(':', 1)[1].strip()
            
            # Get memory info
            mem_info = self._execute_command('cat /proc/meminfo | grep MemTotal')
            if mem_info:
                info['total_memory'] = mem_info.split(':', 1)[1].strip()
            
            return info
            
        except Exception as e:
            return {"error": f"Error getting Linux system info: {e}"}
    
    def get_system_stats(self) -> Dict[str, Any]:
        """Get real-time system statistics."""
        try:
            os_type = self._detect_os()
            
            if os_type == "windows":
                return self._get_windows_stats()
            elif os_type == "linux":
                return self._get_linux_stats()
            else:
                return {"error": "Unknown operating system"}
                
        except Exception as e:
            self.logger.error(f"Error getting system stats: {e}")
            return {"error": str(e)}
    
    def _get_windows_stats(self) -> Dict[str, Any]:
        """Get Windows system statistics."""
        try:
            stats = {}
            
            # CPU usage
            cpu_usage = self._execute_command('wmic cpu get loadpercentage /value')
            for line in cpu_usage.split('\n'):
                if 'LoadPercentage=' in line:
                    stats['cpu_usage'] = f"{line.split('=')[1].strip()}%"
                    break
            
            # Memory usage
            mem_total = self._execute_command('wmic OS get TotalVisibleMemorySize /value')
            mem_free = self._execute_command('wmic OS get FreePhysicalMemory /value')
            
            total_kb = 0
            free_kb = 0
            
            for line in mem_total.split('\n'):
                if 'TotalVisibleMemorySize=' in line:
                    total_kb = int(line.split('=')[1].strip())
                    break
            
            for line in mem_free.split('\n'):
                if 'FreePhysicalMemory=' in line:
                    free_kb = int(line.split('=')[1].strip())
                    break
            
            if total_kb > 0:
                used_kb = total_kb - free_kb
                usage_percent = (used_kb / total_kb) * 100
                stats['memory_usage'] = f"{usage_percent:.1f}%"
                stats['memory_used'] = f"{used_kb // 1024} MB"
                stats['memory_total'] = f"{total_kb // 1024} MB"
            
            # Disk usage for C: drive
            disk_info = self._execute_command('wmic logicaldisk where size!=0 get size,freespace,caption')
            lines = [line.strip() for line in disk_info.split('\n') if line.strip()]
            for line in lines[1:]:  # Skip header
                parts = line.split()
                if len(parts) >= 3 and 'C:' in parts:
                    caption = parts[0]
                    free_space = int(parts[1])
                    size = int(parts[2])
                    used_space = size - free_space
                    usage_percent = (used_space / size) * 100
                    stats['disk_usage'] = f"{usage_percent:.1f}%"
                    stats['disk_used'] = f"{used_space // (1024**3):.1f} GB"
                    stats['disk_total'] = f"{size // (1024**3):.1f} GB"
                    break
            
            return stats
            
        except Exception as e:
            return {"error": f"Error getting Windows stats: {e}"}
    
    def _get_linux_stats(self) -> Dict[str, Any]:
        """Get Linux system statistics."""
        try:
            stats = {}
            
            # CPU usage from /proc/stat
            try:
                cpu_info = self._execute_command('cat /proc/stat | grep "cpu " | head -1')
                if cpu_info:
                    # Parse CPU times and calculate usage
                    fields = cpu_info.split()
                    idle = int(fields[4])
                    total = sum(int(x) for x in fields[1:8])
                    usage = ((total - idle) / total) * 100 if total > 0 else 0
                    stats['cpu_usage'] = f"{usage:.1f}%"
            except:
                # Fallback using top command
                top_output = self._execute_command('top -bn1 | grep "Cpu(s)" | head -1')
                if 'id,' in top_output:
                    idle_str = top_output.split('id,')[0].split()[-1]
                    idle = float(idle_str.replace('%', ''))
                    usage = 100 - idle
                    stats['cpu_usage'] = f"{usage:.1f}%"
            
            # Memory usage from /proc/meminfo
            mem_info = self._execute_command('cat /proc/meminfo | grep -E "MemTotal|MemAvailable"')
            mem_total = 0
            mem_available = 0
            
            for line in mem_info.split('\n'):
                if 'MemTotal:' in line:
                    mem_total = int(line.split()[1]) * 1024  # Convert to bytes
                elif 'MemAvailable:' in line:
                    mem_available = int(line.split()[1]) * 1024  # Convert to bytes
            
            if mem_total > 0:
                mem_used = mem_total - mem_available
                usage_percent = (mem_used / mem_total) * 100
                stats['memory_usage'] = f"{usage_percent:.1f}%"
                stats['memory_used'] = f"{mem_used // (1024**2)} MB"
                stats['memory_total'] = f"{mem_total // (1024**2)} MB"
            
            # Disk usage for root filesystem
            disk_info = self._execute_command('df -h / | tail -1')
            if disk_info:
                parts = disk_info.split()
                if len(parts) >= 6:
                    stats['disk_usage'] = parts[4]  # Usage percentage
                    stats['disk_used'] = parts[2]   # Used space
                    stats['disk_total'] = parts[1]  # Total space
            
            return stats
            
        except Exception as e:
            return {"error": f"Error getting Linux stats: {e}"}
    
    def get_process_list(self) -> List[Dict[str, Any]]:
        """Get list of running processes."""
        try:
            os_type = self._detect_os()
            
            if os_type == "windows":
                return self._get_windows_processes()
            elif os_type == "linux":
                return self._get_linux_processes()
            else:
                return []
                
        except Exception as e:
            self.logger.error(f"Error getting process list: {e}")
            return []
    
    def _get_windows_processes(self) -> List[Dict[str, Any]]:
        """Get Windows process list."""
        try:
            # Get top processes by CPU usage
            output = self._execute_command('wmic process get Name,ProcessId,PageFileUsage,WorkingSetSize /format:csv | sort /r /k:5')
            processes = []
            
            lines = [line.strip() for line in output.split('\n') if line.strip()]
            for line in lines[1:11]:  # Skip header, get top 10
                parts = line.split(',')
                if len(parts) >= 5:
                    try:
                        processes.append({
                            'name': parts[1] if parts[1] else 'Unknown',
                            'pid': parts[3] if parts[3] else '0',
                            'memory': f"{int(parts[2]) // 1024} KB" if parts[2] and parts[2] != 'NULL' else '0 KB'
                        })
                    except (ValueError, IndexError):
                        continue
            
            return processes[:10]  # Return top 10
            
        except Exception as e:
            return [{"error": f"Error getting Windows processes: {e}"}]
    
    def _get_linux_processes(self) -> List[Dict[str, Any]]:
        """Get Linux process list."""
        try:
            # Get top processes by CPU usage
            output = self._execute_command('ps aux --sort=-%cpu | head -11')
            processes = []
            
            lines = output.split('\n')
            for line in lines[1:]:  # Skip header
                if line.strip():
                    parts = line.split(None, 10)  # Split on whitespace, max 11 parts
                    if len(parts) >= 11:
                        processes.append({
                            'name': parts[10][:30] + '...' if len(parts[10]) > 30 else parts[10],
                            'pid': parts[1],
                            'cpu': f"{parts[2]}%",
                            'memory': f"{parts[3]}%"
                        })
            
            return processes[:10]  # Return top 10
            
        except Exception as e:
            return [{"error": f"Error getting Linux processes: {e}"}]
    
    def get_disk_usage(self) -> List[Dict[str, Any]]:
        """Get disk usage information."""
        try:
            os_type = self._detect_os()
            
            if os_type == "windows":
                return self._get_windows_disk_usage()
            elif os_type == "linux":
                return self._get_linux_disk_usage()
            else:
                return []
                
        except Exception as e:
            self.logger.error(f"Error getting disk usage: {e}")
            return []
    
    def _get_windows_disk_usage(self) -> List[Dict[str, Any]]:
        """Get Windows disk usage."""
        try:
            output = self._execute_command('wmic logicaldisk where size!=0 get size,freespace,caption')
            disks = []
            
            lines = [line.strip() for line in output.split('\n') if line.strip()]
            for line in lines[1:]:  # Skip header
                parts = line.split()
                if len(parts) >= 3:
                    caption = parts[0]
                    free_space = int(parts[1])
                    size = int(parts[2])
                    used_space = size - free_space
                    usage_percent = (used_space / size) * 100
                    
                    disks.append({
                        'device': caption,
                        'total': f"{size // (1024**3):.1f} GB",
                        'used': f"{used_space // (1024**3):.1f} GB",
                        'free': f"{free_space // (1024**3):.1f} GB",
                        'usage': f"{usage_percent:.1f}%"
                    })
            
            return disks
            
        except Exception as e:
            return [{"error": f"Error getting Windows disk usage: {e}"}]
    
    def _get_linux_disk_usage(self) -> List[Dict[str, Any]]:
        """Get Linux disk usage."""
        try:
            output = self._execute_command('df -h | grep -E "^/dev/"')
            disks = []
            
            for line in output.split('\n'):
                if line.strip():
                    parts = line.split()
                    if len(parts) >= 6:
                        disks.append({
                            'device': parts[0],
                            'total': parts[1],
                            'used': parts[2],
                            'free': parts[3],
                            'usage': parts[4],
                            'mount': parts[5]
                        })
            
            return disks
            
        except Exception as e:
            return [{"error": f"Error getting Linux disk usage: {e}"}]
    
    def get_network_info(self) -> List[Dict[str, Any]]:
        """Get network interface information."""
        try:
            os_type = self._detect_os()
            
            if os_type == "windows":
                return self._get_windows_network_info()
            elif os_type == "linux":
                return self._get_linux_network_info()
            else:
                return []
                
        except Exception as e:
            self.logger.error(f"Error getting network info: {e}")
            return []
    
    def _get_windows_network_info(self) -> List[Dict[str, Any]]:
        """Get Windows network interface information."""
        try:
            output = self._execute_command('ipconfig')
            interfaces = []
            current_interface = None
            
            for line in output.split('\n'):
                line = line.strip()
                if 'adapter' in line.lower() and ':' in line:
                    if current_interface:
                        interfaces.append(current_interface)
                    current_interface = {'name': line.split(':')[0].strip()}
                elif current_interface and 'IPv4 Address' in line:
                    current_interface['ip'] = line.split(':')[1].strip()
                elif current_interface and 'Subnet Mask' in line:
                    current_interface['netmask'] = line.split(':')[1].strip()
            
            if current_interface:
                interfaces.append(current_interface)
            
            return interfaces
            
        except Exception as e:
            return [{"error": f"Error getting Windows network info: {e}"}]
    
    def _get_linux_network_info(self) -> List[Dict[str, Any]]:
        """Get Linux network interface information."""
        try:
            # Try ip command first
            try:
                output = self._execute_command('ip addr show')
                interfaces = []
                current_interface = None
                
                for line in output.split('\n'):
                    line = line.strip()
                    if line and line[0].isdigit() and ':' in line:
                        if current_interface:
                            interfaces.append(current_interface)
                        parts = line.split(':')
                        if len(parts) >= 2:
                            current_interface = {'name': parts[1].strip().split()[0]}
                    elif current_interface and 'inet ' in line and 'scope global' in line:
                        parts = line.split()
                        if len(parts) >= 2:
                            ip_cidr = parts[1]
                            current_interface['ip'] = ip_cidr.split('/')[0]
                            current_interface['cidr'] = ip_cidr
                
                if current_interface:
                    interfaces.append(current_interface)
                
                return [iface for iface in interfaces if iface.get('ip')]
                
            except:
                # Fallback to ifconfig
                output = self._execute_command('ifconfig')
                interfaces = []
                current_interface = None
                
                for line in output.split('\n'):
                    if line and not line.startswith(' ') and not line.startswith('\t'):
                        if current_interface:
                            interfaces.append(current_interface)
                        current_interface = {'name': line.split(':')[0].strip()}
                    elif current_interface and 'inet ' in line:
                        parts = line.split()
                        for i, part in enumerate(parts):
                            if part == 'inet' and i + 1 < len(parts):
                                current_interface['ip'] = parts[i + 1]
                                break
                
                if current_interface:
                    interfaces.append(current_interface)
                
                return [iface for iface in interfaces if iface.get('ip')]
            
        except Exception as e:
            return [{"error": f"Error getting Linux network info: {e}"}]
    
    # Port Forwarding Methods
    def create_local_port_forward(self, local_port: int, remote_host: str, remote_port: int) -> str:
        """Create a local port forward (SSH -L option)."""
        if not self.connected:
            raise SessionError("Session not connected")
        
        forward_id = f"L_{local_port}_{remote_host}_{remote_port}"
        
        if forward_id in self.port_forwards:
            raise SessionError(f"Local forward already exists: {local_port} -> {remote_host}:{remote_port}")
        
        try:
            # Check if local port is available
            test_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            test_socket.bind(('127.0.0.1', local_port))
            test_socket.close()
            
            # Create the forward
            forward_info = {
                'type': 'local',
                'local_port': local_port,
                'remote_host': remote_host,
                'remote_port': remote_port,
                'active': False,
                'connections': 0
            }
            
            # Start forwarding thread
            thread = threading.Thread(
                target=self._local_forward_handler,
                args=(local_port, remote_host, remote_port, forward_id),
                daemon=True
            )
            thread.start()
            
            self.port_forwards[forward_id] = forward_info
            self.forward_threads[forward_id] = thread
            
            self.logger.info(f"Created local port forward: {local_port} -> {remote_host}:{remote_port}")
            return forward_id
            
        except socket.error as e:
            raise SessionError(f"Port {local_port} is already in use or unavailable: {e}")
        except Exception as e:
            raise SessionError(f"Failed to create local port forward: {e}")
    
    def create_remote_port_forward(self, remote_port: int, local_host: str, local_port: int) -> str:
        """Create a remote port forward (SSH -R option)."""
        if not self.connected:
            raise SessionError("Session not connected")
        
        forward_id = f"R_{remote_port}_{local_host}_{local_port}"
        
        if forward_id in self.port_forwards:
            raise SessionError(f"Remote forward already exists: {remote_port} -> {local_host}:{local_port}")
        
        try:
            # Create the remote forward using Paramiko
            transport = self.client.client.get_transport()
            transport.request_port_forward('', remote_port)
            
            forward_info = {
                'type': 'remote',
                'remote_port': remote_port,
                'local_host': local_host,
                'local_port': local_port,
                'active': True,
                'connections': 0
            }
            
            # Start handler for incoming connections
            thread = threading.Thread(
                target=self._remote_forward_handler,
                args=(remote_port, local_host, local_port, forward_id),
                daemon=True
            )
            thread.start()
            
            self.port_forwards[forward_id] = forward_info
            self.forward_threads[forward_id] = thread
            
            self.logger.info(f"Created remote port forward: {remote_port} -> {local_host}:{local_port}")
            return forward_id
            
        except Exception as e:
            raise SessionError(f"Failed to create remote port forward: {e}")
    
    def create_dynamic_port_forward(self, local_port: int) -> str:
        """Create a dynamic port forward / SOCKS proxy (SSH -D option)."""
        if not self.connected:
            raise SessionError("Session not connected")
        
        forward_id = f"D_{local_port}"
        
        if forward_id in self.port_forwards:
            raise SessionError(f"Dynamic forward already exists on port {local_port}")
        
        try:
            # Check if local port is available
            test_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            test_socket.bind(('127.0.0.1', local_port))
            test_socket.close()
            
            forward_info = {
                'type': 'dynamic',
                'local_port': local_port,
                'active': False,
                'connections': 0
            }
            
            # Start SOCKS proxy thread
            thread = threading.Thread(
                target=self._dynamic_forward_handler,
                args=(local_port, forward_id),
                daemon=True
            )
            thread.start()
            
            self.port_forwards[forward_id] = forward_info
            self.forward_threads[forward_id] = thread
            
            self.logger.info(f"Created dynamic port forward (SOCKS proxy) on port {local_port}")
            return forward_id
            
        except socket.error as e:
            raise SessionError(f"Port {local_port} is already in use or unavailable: {e}")
        except Exception as e:
            raise SessionError(f"Failed to create dynamic port forward: {e}")
    
    def stop_port_forward(self, forward_id: str) -> bool:
        """Stop a specific port forward."""
        if forward_id not in self.port_forwards:
            return False
        
        try:
            forward_info = self.port_forwards[forward_id]
            
            # Mark as inactive
            forward_info['active'] = False
            
            # For remote forwards, cancel the port forward
            if forward_info['type'] == 'remote':
                try:
                    transport = self.client.client.get_transport()
                    transport.cancel_port_forward('', forward_info['remote_port'])
                except:
                    pass
            
            # Clean up
            if forward_id in self.forward_threads:
                # Thread will stop when it detects active=False
                del self.forward_threads[forward_id]
            
            del self.port_forwards[forward_id]
            
            self.logger.info(f"Stopped port forward: {forward_id}")
            return True
            
        except Exception as e:
            self.logger.error(f"Error stopping port forward {forward_id}: {e}")
            return False
    
    def list_port_forwards(self) -> List[Dict[str, Any]]:
        """List all active port forwards."""
        forwards = []
        for forward_id, info in self.port_forwards.items():
            forward_data = {
                'id': forward_id,
                'type': info['type'],
                'active': info['active'],
                'connections': info['connections']
            }
            
            if info['type'] == 'local':
                forward_data.update({
                    'local_port': info['local_port'],
                    'remote_host': info['remote_host'],
                    'remote_port': info['remote_port'],
                    'description': f"Local {info['local_port']} -> {info['remote_host']}:{info['remote_port']}"
                })
            elif info['type'] == 'remote':
                forward_data.update({
                    'remote_port': info['remote_port'],
                    'local_host': info['local_host'],
                    'local_port': info['local_port'],
                    'description': f"Remote {info['remote_port']} -> {info['local_host']}:{info['local_port']}"
                })
            elif info['type'] == 'dynamic':
                forward_data.update({
                    'local_port': info['local_port'],
                    'description': f"SOCKS proxy on port {info['local_port']}"
                })
            
            forwards.append(forward_data)
        
        return forwards
    
    def _stop_all_port_forwards(self):
        """Stop all port forwards when disconnecting."""
        for forward_id in list(self.port_forwards.keys()):
            self.stop_port_forward(forward_id)
    
    def _local_forward_handler(self, local_port: int, remote_host: str, remote_port: int, forward_id: str):
        """Handle local port forwarding connections."""
        try:
            # Create listening socket
            server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server_socket.bind(('127.0.0.1', local_port))
            server_socket.listen(5)
            
            self.port_forwards[forward_id]['active'] = True
            self.logger.info(f"Local forward listening on port {local_port}")
            
            while self.port_forwards.get(forward_id, {}).get('active', False):
                try:
                    server_socket.settimeout(1.0)  # Check for shutdown every second
                    client_socket, addr = server_socket.accept()
                    
                    # Handle connection in separate thread
                    thread = threading.Thread(
                        target=self._handle_local_forward_connection,
                        args=(client_socket, remote_host, remote_port, forward_id),
                        daemon=True
                    )
                    thread.start()
                    
                except socket.timeout:
                    continue
                except Exception as e:
                    if self.port_forwards.get(forward_id, {}).get('active', False):
                        self.logger.error(f"Error in local forward handler: {e}")
                    break
            
        except Exception as e:
            self.logger.error(f"Failed to start local forward handler: {e}")
        finally:
            try:
                server_socket.close()
            except:
                pass
            if forward_id in self.port_forwards:
                self.port_forwards[forward_id]['active'] = False
    
    def _handle_local_forward_connection(self, client_socket: socket.socket, remote_host: str, remote_port: int, forward_id: str):
        """Handle individual local forward connection."""
        ssh_channel = None
        try:
            # Increment connection count
            if forward_id in self.port_forwards:
                self.port_forwards[forward_id]['connections'] += 1
            
            # Create SSH channel
            ssh_channel = self.client.client.get_transport().open_channel(
                'direct-tcpip',
                (remote_host, remote_port),
                client_socket.getpeername()
            )
            
            # Relay data between client and SSH channel
            self._relay_data(client_socket, ssh_channel, forward_id)
            
        except Exception as e:
            self.logger.error(f"Error in local forward connection: {e}")
        finally:
            try:
                client_socket.close()
            except:
                pass
            try:
                if ssh_channel:
                    ssh_channel.close()
            except:
                pass
            # Decrement connection count
            if forward_id in self.port_forwards:
                self.port_forwards[forward_id]['connections'] = max(0, self.port_forwards[forward_id]['connections'] - 1)
    
    def _remote_forward_handler(self, remote_port: int, local_host: str, local_port: int, forward_id: str):
        """Handle remote port forwarding connections."""
        try:
            transport = self.client.client.get_transport()
            
            while self.port_forwards.get(forward_id, {}).get('active', False):
                try:
                    # Accept incoming channel from remote server
                    channel = transport.accept(timeout=1.0)
                    if channel is None:
                        continue
                    
                    # Handle connection in separate thread
                    thread = threading.Thread(
                        target=self._handle_remote_forward_connection,
                        args=(channel, local_host, local_port, forward_id),
                        daemon=True
                    )
                    thread.start()
                    
                except Exception as e:
                    if self.port_forwards.get(forward_id, {}).get('active', False):
                        self.logger.error(f"Error in remote forward handler: {e}")
                    break
            
        except Exception as e:
            self.logger.error(f"Failed to start remote forward handler: {e}")
        finally:
            if forward_id in self.port_forwards:
                self.port_forwards[forward_id]['active'] = False
    
    def _handle_remote_forward_connection(self, ssh_channel, local_host: str, local_port: int, forward_id: str):
        """Handle individual remote forward connection."""
        local_socket = None
        try:
            # Increment connection count
            if forward_id in self.port_forwards:
                self.port_forwards[forward_id]['connections'] += 1
            
            # Connect to local service
            local_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            local_socket.connect((local_host, local_port))
            
            # Relay data between SSH channel and local socket
            self._relay_data(local_socket, ssh_channel, forward_id)
            
        except Exception as e:
            self.logger.error(f"Error in remote forward connection: {e}")
        finally:
            try:
                if local_socket:
                    local_socket.close()
            except:
                pass
            try:
                ssh_channel.close()
            except:
                pass
            # Decrement connection count
            if forward_id in self.port_forwards:
                self.port_forwards[forward_id]['connections'] = max(0, self.port_forwards[forward_id]['connections'] - 1)
    
    def _dynamic_forward_handler(self, local_port: int, forward_id: str):
        """Handle dynamic port forwarding (SOCKS proxy)."""
        try:
            # Create listening socket
            server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            server_socket.bind(('127.0.0.1', local_port))
            server_socket.listen(5)
            
            self.port_forwards[forward_id]['active'] = True
            self.logger.info(f"SOCKS proxy listening on port {local_port}")
            
            while self.port_forwards.get(forward_id, {}).get('active', False):
                try:
                    server_socket.settimeout(1.0)
                    client_socket, addr = server_socket.accept()
                    
                    # Handle SOCKS connection in separate thread
                    thread = threading.Thread(
                        target=self._handle_socks_connection,
                        args=(client_socket, forward_id),
                        daemon=True
                    )
                    thread.start()
                    
                except socket.timeout:
                    continue
                except Exception as e:
                    if self.port_forwards.get(forward_id, {}).get('active', False):
                        self.logger.error(f"Error in SOCKS handler: {e}")
                    break
            
        except Exception as e:
            self.logger.error(f"Failed to start SOCKS handler: {e}")
        finally:
            try:
                server_socket.close()
            except:
                pass
            if forward_id in self.port_forwards:
                self.port_forwards[forward_id]['active'] = False
    
    def _handle_socks_connection(self, client_socket: socket.socket, forward_id: str):
        """Handle individual SOCKS proxy connection."""
        ssh_channel = None
        try:
            # Increment connection count
            if forward_id in self.port_forwards:
                self.port_forwards[forward_id]['connections'] += 1
            
            # Simple SOCKS4/5 implementation
            # Read SOCKS request
            data = client_socket.recv(1024)
            if len(data) < 2:
                return
            
            # SOCKS5
            if data[0] == 5:
                # Send auth method (no auth)
                client_socket.send(b'\x05\x00')
                
                # Read connect request
                data = client_socket.recv(1024)
                if len(data) < 10 or data[0] != 5 or data[1] != 1:
                    return
                
                # Parse destination
                addr_type = data[3]
                if addr_type == 1:  # IPv4
                    dest_addr = '.'.join(str(b) for b in data[4:8])
                    dest_port = int.from_bytes(data[8:10], 'big')
                elif addr_type == 3:  # Domain name
                    addr_len = data[4]
                    dest_addr = data[5:5+addr_len].decode('utf-8')
                    dest_port = int.from_bytes(data[5+addr_len:7+addr_len], 'big')
                else:
                    # Send error response
                    client_socket.send(b'\x05\x08\x00\x01\x00\x00\x00\x00\x00\x00')
                    return
                
                # Create SSH channel
                ssh_channel = self.client.client.get_transport().open_channel(
                    'direct-tcpip',
                    (dest_addr, dest_port),
                    client_socket.getpeername()
                )
                
                # Send success response
                client_socket.send(b'\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00')
                
                # Relay data
                self._relay_data(client_socket, ssh_channel, forward_id)
            
            # SOCKS4
            elif data[0] == 4:
                if len(data) < 8 or data[1] != 1:
                    return
                
                dest_port = int.from_bytes(data[2:4], 'big')
                dest_addr = '.'.join(str(b) for b in data[4:8])
                
                # Create SSH channel
                ssh_channel = self.client.client.get_transport().open_channel(
                    'direct-tcpip',
                    (dest_addr, dest_port),
                    client_socket.getpeername()
                )
                
                # Send success response
                client_socket.send(b'\x00\x5a\x00\x00\x00\x00\x00\x00')
                
                # Relay data
                self._relay_data(client_socket, ssh_channel, forward_id)
                
        except Exception as e:
            self.logger.error(f"Error in SOCKS connection: {e}")
        finally:
            try:
                client_socket.close()
            except:
                pass
            try:
                if ssh_channel:
                    ssh_channel.close()
            except:
                pass
            # Decrement connection count
            if forward_id in self.port_forwards:
                self.port_forwards[forward_id]['connections'] = max(0, self.port_forwards[forward_id]['connections'] - 1)
    
    def _relay_data(self, socket1, socket2, forward_id: str):
        """Relay data between two sockets/channels."""
        try:
            while self.port_forwards.get(forward_id, {}).get('active', False):
                ready, _, _ = select.select([socket1, socket2], [], [], 1.0)
                
                if not ready:
                    continue
                
                for sock in ready:
                    try:
                        if sock == socket1:
                            data = socket1.recv(4096)
                            if not data:
                                break
                            socket2.send(data)
                        else:
                            data = socket2.recv(4096)
                            if not data:
                                break
                            socket1.send(data)
                    except Exception:
                        break
                else:
                    continue
                break
                
        except Exception as e:
            self.logger.debug(f"Data relay ended: {e}")
        finally:
            try:
                socket1.close()
            except:
                pass
            try:
                socket2.close()
            except:
                pass