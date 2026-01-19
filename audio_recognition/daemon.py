"""
SFP-CLI Daemon Manager

Manages the sfp-cli daemon process for fast local fingerprint queries.
The daemon keeps the fingerprint database loaded in memory, providing
sub-100ms query times instead of 7-15 seconds per request.

Lifecycle:
- Daemon starts lazily on first query (not when engine starts)
- Daemon stops when recognition engine stops
- Auto-restarts on crash (max 5 retries, then falls back to subprocess)
"""

import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional, Any

from logging_config import get_logger

logger = get_logger(__name__)


class DaemonManager:
    """
    Manages the sfp-cli daemon process lifecycle.
    
    Features:
    - Lazy initialization (starts on first query)
    - Thread-safe command sending
    - Auto-restart on crash (max 5 times)
    - Fallback to subprocess mode if daemon fails
    - Graceful shutdown
    """
    
    MAX_RESTART_ATTEMPTS = 5
    STARTUP_TIMEOUT = 60  # seconds to wait for daemon ready
    COMMAND_TIMEOUT = 30  # seconds to wait for command response
    
    def __init__(self, exe_path: Path, db_path: Path):
        """
        Initialize daemon manager.
        
        Args:
            exe_path: Path to sfp-cli executable
            db_path: Path to fingerprint database directory
        """
        self._exe_path = exe_path
        self._db_path = db_path
        self._process: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._restart_count = 0
        self._ready = False
        self._last_ready_info: dict = {}
        self._fallback_mode = False  # If True, use subprocess instead of daemon
    
    @property
    def is_running(self) -> bool:
        """Check if daemon process is running."""
        return self._process is not None and self._process.poll() is None
    
    @property
    def is_ready(self) -> bool:
        """Check if daemon is running and ready to accept commands."""
        return self.is_running and self._ready
    
    @property
    def in_fallback_mode(self) -> bool:
        """Check if we've fallen back to subprocess mode."""
        return self._fallback_mode
    
    def start(self) -> bool:
        """
        Start the daemon process.
        
        Returns:
            True if daemon started successfully, False otherwise
        """
        with self._lock:
            if self.is_running:
                logger.debug("Daemon already running")
                return True
            
            if self._fallback_mode:
                logger.debug("In fallback mode, not starting daemon")
                return False
            
            try:
                logger.info(f"Starting sfp-cli daemon (attempt {self._restart_count + 1})...")
                
                # Start the daemon process
                creationflags = 0
                if sys.platform == "win32":
                    creationflags = subprocess.CREATE_NO_WINDOW
                
                self._process = subprocess.Popen(
                    [
                        str(self._exe_path),
                        "--db-path", str(self._db_path.absolute()),
                        "serve"
                    ],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,  # Line buffered
                    creationflags=creationflags
                )
                
                # Wait for ready signal
                self._ready = False
                start_time = time.time()
                
                while time.time() - start_time < self.STARTUP_TIMEOUT:
                    if self._process.poll() is not None:
                        # Process exited
                        stderr = self._process.stderr.read() if self._process.stderr else ""
                        logger.error(f"Daemon exited during startup: {stderr}")
                        self._process = None
                        return False
                    
                    # Check for ready message
                    if self._process.stdout:
                        line = self._process.stdout.readline()
                        if line:
                            try:
                                data = json.loads(line.strip())
                                if data.get("status") == "ready":
                                    self._ready = True
                                    self._last_ready_info = data
                                    self._restart_count = 0  # Reset on successful start
                                    logger.info(
                                        f"sfp-cli daemon ready: {data.get('songs', 0)} songs, "
                                        f"{data.get('fingerprints', 0)} fingerprints"
                                    )
                                    return True
                            except json.JSONDecodeError:
                                logger.debug(f"Non-JSON from daemon: {line.strip()}")
                    
                    time.sleep(0.1)
                
                # Timeout reached
                logger.error("Daemon startup timeout - killing process")
                self._kill_process()
                return False
                
            except Exception as e:
                logger.error(f"Failed to start daemon: {e}")
                self._kill_process()
                return False
    
    def stop(self) -> None:
        """Stop the daemon process gracefully."""
        with self._lock:
            if not self.is_running:
                return
            
            logger.info("Stopping sfp-cli daemon...")
            
            try:
                # Send shutdown command
                self._send_raw('{"cmd": "shutdown"}')
                
                # Wait for graceful shutdown
                try:
                    self._process.wait(timeout=5)
                    logger.info("Daemon stopped gracefully")
                except subprocess.TimeoutExpired:
                    logger.warning("Daemon didn't stop gracefully, killing")
                    self._kill_process()
                    
            except Exception as e:
                logger.warning(f"Error stopping daemon: {e}")
                self._kill_process()
            
            self._process = None
            self._ready = False
    
    def send_command(self, command: dict) -> Optional[dict]:
        """
        Send a command to the daemon and get response.
        
        Args:
            command: Command dict (e.g., {"cmd": "query", "path": "..."})
            
        Returns:
            Response dict or None on error
        """
        with self._lock:
            # Ensure daemon is running
            if not self.is_ready:
                if not self._ensure_daemon():
                    return None
            
            try:
                # Send command
                cmd_json = json.dumps(command)
                self._send_raw(cmd_json)
                
                # Read response
                if self._process and self._process.stdout:
                    line = self._process.stdout.readline()
                    if line:
                        return json.loads(line.strip())
                    else:
                        # EOF - daemon died
                        logger.warning("Daemon returned EOF")
                        self._handle_crash()
                        return None
                        
            except json.JSONDecodeError as e:
                logger.error(f"Invalid JSON response from daemon: {e}")
                return None
            except Exception as e:
                logger.error(f"Error communicating with daemon: {e}")
                self._handle_crash()
                return None
        
        return None
    
    def _ensure_daemon(self) -> bool:
        """Ensure daemon is running, starting or restarting if needed."""
        if self.is_ready:
            return True
        
        # Check if we should try to restart
        if self._restart_count >= self.MAX_RESTART_ATTEMPTS:
            if not self._fallback_mode:
                logger.warning(
                    f"Daemon failed {self.MAX_RESTART_ATTEMPTS} times, "
                    "falling back to subprocess mode"
                )
                self._fallback_mode = True
            return False
        
        self._restart_count += 1
        return self.start()
    
    def _handle_crash(self) -> None:
        """Handle daemon crash - cleanup and prepare for restart."""
        logger.warning("Daemon crashed, will attempt restart on next command")
        self._kill_process()
        self._process = None
        self._ready = False
    
    def _send_raw(self, line: str) -> None:
        """Send raw line to daemon stdin."""
        if self._process and self._process.stdin:
            self._process.stdin.write(line + "\n")
            self._process.stdin.flush()
    
    def _kill_process(self) -> None:
        """Force kill the daemon process."""
        if self._process:
            try:
                self._process.kill()
                self._process.wait(timeout=2)
            except Exception:
                pass
            self._process = None
            self._ready = False
    
    def get_stats(self) -> Optional[dict]:
        """Get daemon stats."""
        return self.send_command({"cmd": "stats"})
    
    def reload_database(self) -> Optional[dict]:
        """Reload database from disk."""
        return self.send_command({"cmd": "reload"})
