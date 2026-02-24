import logging
import socket
import time
from models.ssh_profile import SSHProfile

# Try to import paramiko, handle missing dependency gracefully
try:
    import paramiko
    PARAMIKO_AVAILABLE = True
except ImportError:
    PARAMIKO_AVAILABLE = False

class SSHService:
    def __init__(self):
        self.logger = logging.getLogger(__name__)

    def test_connection(self, host, username, password=None, key_path=None, port=22):
        """
        Test SSH connection to a host.
        Returns (success, message)
        """
        if not PARAMIKO_AVAILABLE:
            return False, "Paramiko library not installed. Cannot perform SSH."

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        try:
            # Prepare kwargs
            connect_kwargs = {
                'hostname': host,
                'port': port,
                'username': username,
                'timeout': 10,
                'banner_timeout': 10
            }
            if password:
                connect_kwargs['password'] = password
            if key_path:
                connect_kwargs['key_filename'] = key_path
                
            client.connect(**connect_kwargs)
            client.close()
            return True, "Connection successful"
            
        except paramiko.AuthenticationException:
            return False, "Authentication failed"
        except paramiko.SSHException as e:
            return False, f"SSH error: {str(e)}"
        except socket.timeout:
            return False, "Connection timed out"
        except Exception as e:
            return False, f"Error: {str(e)}"
        finally:
            client.close()

    def execute_command(self, host, profile_id, command, timeout=10):
        """
        Execute a single command on a device using a stored profile.
        Returns (output, error) or raises Exception.
        """
        if not PARAMIKO_AVAILABLE:
            raise ImportError("Paramiko not installed")

        profile = SSHProfile.query.get(profile_id)
        if not profile:
            raise ValueError(f"SSH Profile {profile_id} not found")
            
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        
        try:
            client.connect(
                hostname=host,
                username=profile.username,
                password=profile.password,
                key_filename=profile.key_path,
                timeout=timeout
            )
            
            stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
            out_str = stdout.read().decode('utf-8')
            err_str = stderr.read().decode('utf-8')
            
            return out_str, err_str
            
        except Exception as e:
            self.logger.error(f"SSH Execute fail on {host}: {e}")
            raise
        finally:
            client.close()

    def get_lldp_neighbors(self, device):
        """
        Connect to device, run 'show lldp neighbors detail' (or equivalent),
        parse output, and return list of neighbor dicts.
        
        Mock implementation for now if no SSH profile.
        """
        if not getattr(device, 'ssh_profile_id', None):
            return self._simulate_lldp_neighbors(device)

        # Real implementation would be here...
        # For now, fallback to simulation to avoid blocking dev
        return self._simulate_lldp_neighbors(device)

        return neighbors

    def get_server_health(self, host, profile_id, timeout=10):
        """
        Connect to a Linux server and retrieve health metrics (CPU, RAM, Disk).
        Returns a dict with:
        - cpu_usage: percentage (0-100)
        - memory_usage: percentage (0-100)
        - disk_usage: percentage (0-100)
        - uptime: string
        """
        
        # Commands to run
        # Multi-command string to run in one session
    def get_server_health(self, host, profile_id, timeout=10):
        """
        Connect to a server (Linux or Windows via OpenSSH) and retrieve health metrics.
        Returns a dict with:
        - cpu_usage: percentage (0-100)
        - memory_usage: percentage (0-100)
        - disk_usage: percentage (0-100)
        - uptime: string
        """
        try:
            # 1. Detect OS
            # Try 'uname' first. Windows cmd/powershell usually won't have it unless WSL/GitBash is default shell.
            # Windows 'ver' is standard.
            
            # Simple check: Try to run 'uname'
            is_linux = False
            try:
                out_uname, _ = self.execute_command(host, profile_id, "uname", timeout=5)
                if "Linux" in out_uname:
                    is_linux = True
            except:
                pass # Might be Windows or connection failed

            if is_linux:
                return self._get_linux_health(host, profile_id, timeout)
            else:
                return self._get_windows_health(host, profile_id, timeout)

        except Exception as e:
            self.logger.error(f"Failed to get server health for {host}: {e}")
            return None

    def _get_linux_health(self, host, profile_id, timeout):
        commands = {
            'ram': "free -m | grep Mem | awk '{print $2,$3}'",
            'disk': "df -h / | tail -1 | awk '{print $5}'",
            'cpu': "top -bn1 | grep 'Cpu(s)' | awk '{print $2}'",
            'uptime': "uptime -p"
        }
        
        out_ram, _ = self.execute_command(host, profile_id, commands['ram'], timeout)
        total_mem, used_mem = map(int, out_ram.strip().split())
        mem_percent = round((used_mem / total_mem) * 100, 1)
        
        out_disk, _ = self.execute_command(host, profile_id, commands['disk'], timeout)
        disk_percent = float(out_disk.strip().replace('%', ''))
        
        out_cpu, _ = self.execute_command(host, profile_id, "grep 'cpu ' /proc/stat | awk '{print ($2+$4)*100/($2+$4+$5)}'", timeout)
        cpu_percent = round(float(out_cpu.strip()), 1)
        
        out_uptime, _ = self.execute_command(host, profile_id, commands['uptime'], timeout)

        return {
            'cpu_usage': cpu_percent,
            'memory_usage': mem_percent,
            'disk_usage': disk_percent,
            'uptime': out_uptime.strip(),
            'status': 'online'
        }

    def _get_windows_health(self, host, profile_id, timeout):
        """
        Requires OpenSSH Server on Windows and access to PowerShell.
        """
        # RAM: Get-CimInstance Win32_OperatingSystem | Select-Object TotalVisibleMemorySize,FreePhysicalMemory
        # CPU: (Get-WmiObject Win32_Processor).LoadPercentage
        # Disk: Get-PSDrive C | Select-Object Used,Free
        
        cmd_ram = 'powershell "Get-CimInstance Win32_OperatingSystem | Select-Object TotalVisibleMemorySize,FreePhysicalMemory | ConvertTo-Json"'
        cmd_cpu = 'powershell "(Get-WmiObject Win32_Processor).LoadPercentage"'
        cmd_disk = 'powershell "Get-PSDrive C | Select-Object Used,Free | ConvertTo-Json"'
        
        import json
        
        # RAM
        out_ram, _ = self.execute_command(host, profile_id, cmd_ram, timeout)
        data_ram = json.loads(out_ram)
        total = data_ram['TotalVisibleMemorySize']
        free = data_ram['FreePhysicalMemory']
        mem_percent = round(((total - free) / total) * 100, 1)

        # CPU
        out_cpu, _ = self.execute_command(host, profile_id, cmd_cpu, timeout)
        cpu_percent = float(out_cpu.strip())

        # Disk
        out_disk, _ = self.execute_command(host, profile_id, cmd_disk, timeout)
        data_disk = json.loads(out_disk)
        used = data_disk['Used']
        free_disk = data_disk['Free']
        disk_percent = round((used / (used + free_disk)) * 100, 1)
        
        return {
            'cpu_usage': cpu_percent,
            'memory_usage': mem_percent,
            'disk_usage': disk_percent,
            'uptime': "Windows Server", # Getting uptime is verbose in PS, skip for speed
            'status': 'online'
        }

    def get_active_connections(self, host, profile_id, timeout=10):
        """
        Connects to a server to retrieve established TCP connections.
        Returns a list of dicts: [{'remote_ip': str, 'remote_port': int, 'local_port': int, 'state': str}]
        """
        try:
            is_linux = False
            try:
                out_uname, _ = self.execute_command(host, profile_id, "uname", timeout=5)
                if "Linux" in out_uname:
                    is_linux = True
            except:
                pass

            if is_linux:
                return self._get_linux_connections(host, profile_id, timeout)
            else:
                return self._get_windows_connections(host, profile_id, timeout)
        except Exception as e:
            self.logger.error(f"Failed to get connections for {host}: {e}")
            return []

    def _get_linux_connections(self, host, profile_id, timeout):
        # Format of ss: ESTAB  0      0            10.10.1.88:22       10.10.1.100:60000 
        cmd = "ss -tn state established | awk '{print $4, $5}' | tail -n +2"
        out_conn, _ = self.execute_command(host, profile_id, cmd, timeout)
        
        connections = []
        if not out_conn:
            return connections
            
        for line in out_conn.strip().split('\\n'):
            parts = line.strip().split()
            if len(parts) >= 2:
                local_str = parts[0]
                remote_str = parts[1]
                
                if ':' in remote_str and not remote_str.startswith('['):
                    try:
                        r_ip, r_port = remote_str.rsplit(':', 1)
                        l_ip, l_port = local_str.rsplit(':', 1)
                        if not r_ip.startswith('127.') and r_ip != '0.0.0.0':
                            connections.append({
                                'remote_ip': r_ip,
                                'remote_port': int(r_port) if r_port.isdigit() else 0,
                                'local_port': int(l_port) if l_port.isdigit() else 0,
                                'state': 'ESTABLISHED'
                            })
                    except Exception as e:
                        continue
        return connections

    def _get_windows_connections(self, host, profile_id, timeout):
        cmd = 'powershell "Get-NetTCPConnection -State Established | Select-Object LocalPort,RemoteAddress,RemotePort | ConvertTo-Json"'
        out_conn, _ = self.execute_command(host, profile_id, cmd, timeout)
        
        import json
        connections = []
        if not out_conn:
            return connections
            
        try:
            data = json.loads(out_conn)
            if isinstance(data, dict):
                data = [data]
            for conn in data:
                r_ip = conn.get('RemoteAddress', '')
                if r_ip and not r_ip.startswith('127.') and r_ip != '::1':
                    connections.append({
                        'remote_ip': r_ip,
                        'remote_port': int(conn.get('RemotePort', 0)),
                        'local_port': int(conn.get('LocalPort', 0)),
                        'state': 'ESTABLISHED'
                    })
        except Exception as e:
            self.logger.error(f"Failed to parse windows connections: {e}")
            
        return connections
