"""
SFP-CLI Daemon Manager

Manages the sfp-cli daemon process for fast local fingerprint queries.
The daemon keeps the fingerprint database loaded in memory, providing
sub-100ms query times instead of 7-15 seconds per request.

Lifecycle:
- Daemon starts lazily on first query (not when engine starts)
- Daemon stops when recognition engine stops
- Auto-restarts on crash (max 5 retries, then falls back to subprocess)

CRITICAL: All I/O with the daemon subprocess uses asyncio.to_thread()
to avoid blocking the event loop. This is essential since the daemon
is called from async recognition code.
"""

import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional, Any, Dict

from logging_config import get_logger

logger = get_logger(__name__)


class DaemonManager:
    """
    Manages the sfp-cli daemon process lifecycle.
    
    Features:
    - Lazy initialization (starts on first query)
    - Async-safe command sending (uses asyncio.to_thread)
    - Auto-restart on crash (max 5 times)
    - Fallback to subprocess mode if daemon fails
    - Graceful shutdown
    
    IMPORTANT: All public methods that interact with the daemon are async
    to ensure they don't block the event loop.
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
        self._restart_count = 0
        self._ready = False
        self._last_ready_info: dict = {}
        self._fallback_mode = False  # If True, use subprocess instead of daemon
        # Locks for thread-safe async operations
        self._start_lock = asyncio.Lock()  # Serializes startup attempts
        self._io_lock = asyncio.Lock()  # Serializes send/receive transactions
    
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
    
    def _start_process_sync(self) -> bool:
        """
        Start the daemon process (synchronous - run in thread).
        
        Returns:
            True if daemon started and ready, False otherwise
        """
        if self.is_running:
            return True
        
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
                stderr=subprocess.DEVNULL,  # Avoid deadlock from unbuffered stderr
                text=True,
                bufsize=1,  # Line buffered
                creationflags=creationflags
            )
            
            # Wait for ready signal (blocking - that's why we're in a thread)
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
    
    async def start(self) -> bool:
        """
        Start the daemon process (async-safe).
        
        Uses asyncio.Lock to ensure only one startup attempt runs at a time.
        Other callers wait for the first attempt to complete.
        
        Returns:
            True if daemon started successfully, False otherwise
        """
        if self.is_running:
            logger.debug("Daemon already running")
            return True
        
        if self._fallback_mode:
            logger.debug("In fallback mode, not starting daemon")
            return False
        
        # Use lock so concurrent callers wait instead of returning False
        async with self._start_lock:
            # Double-check after acquiring lock (another caller may have started it)
            if self.is_running:
                return True
            
            try:
                # Run blocking startup in thread to avoid blocking event loop
                result = await asyncio.wait_for(
                    asyncio.to_thread(self._start_process_sync),
                    timeout=self.STARTUP_TIMEOUT + 5  # Extra buffer
                )
                return result
            except asyncio.TimeoutError:
                logger.error("Daemon startup timed out in async wrapper")
                self._kill_process()
                return False
            except Exception as e:
                logger.error(f"Daemon startup failed: {e}")
                self._kill_process()
                return False
    
    def _stop_sync(self) -> None:
        """Stop the daemon process (synchronous - for cleanup)."""
        if not self.is_running:
            return
        
        logger.info("Stopping sfp-cli daemon...")
        
        try:
            # Send shutdown command
            if self._process and self._process.stdin:
                self._process.stdin.write('{"cmd": "shutdown"}\n')
                self._process.stdin.flush()
            
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
    
    def stop(self) -> None:
        """
        Stop the daemon process gracefully.
        
        This is synchronous because it's typically called during cleanup
        when the event loop may be shutting down.
        """
        self._stop_sync()
    
    def _send_command_sync(self, command: dict) -> Optional[dict]:
        """
        Send command to daemon and get response (synchronous - run in thread).
        
        Args:
            command: Command dict (e.g., {"cmd": "query", "path": "..."})
            
        Returns:
            Response dict or None on error
        """
        if not self.is_ready:
            return None
        
        try:
            # Send command
            cmd_json = json.dumps(command)
            if self._process and self._process.stdin:
                self._process.stdin.write(cmd_json + "\n")
                self._process.stdin.flush()
            else:
                return None
            
            # Read response (blocking - that's why we're in a thread)
            if self._process and self._process.stdout:
                line = self._process.stdout.readline()
                if line:
                    return json.loads(line.strip())
                else:
                    # EOF - daemon died
                    logger.warning("Daemon returned EOF")
                    return None
                    
        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON response from daemon: {e}")
            return None
        except Exception as e:
            logger.error(f"Error communicating with daemon: {e}")
            return None
        
        return None
    
    async def send_command(self, command: dict) -> Optional[dict]:
        """
        Send a command to the daemon and get response (async-safe).
        
        Uses asyncio.Lock to serialize send/receive transactions,
        ensuring responses are correctly paired with requests.
        
        Args:
            command: Command dict (e.g., {"cmd": "query", "path": "..."})
            
        Returns:
            Response dict or None on error
        """
        # Ensure daemon is running
        if not self.is_ready:
            if not await self._ensure_daemon():
                return None
        
        # Lock ensures only one command/response transaction at a time
        async with self._io_lock:
            try:
                # Run blocking I/O in thread with timeout
                result = await asyncio.wait_for(
                    asyncio.to_thread(self._send_command_sync, command),
                    timeout=self.COMMAND_TIMEOUT
                )
                
                if result is None:
                    # Command failed, daemon may have crashed
                    await self._handle_crash()
                
                return result
                
            except asyncio.TimeoutError:
                logger.error(f"Daemon command timed out: {command.get('cmd')}")
                await self._handle_crash()
                return None
            except Exception as e:
                logger.error(f"Error sending command to daemon: {e}")
                await self._handle_crash()
                return None
    
    async def _ensure_daemon(self) -> bool:
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
        return await self.start()
    
    async def _handle_crash(self) -> None:
        """Handle daemon crash - cleanup and prepare for restart."""
        logger.warning("Daemon crashed, will attempt restart on next command")
        self._kill_process()
        self._process = None
        self._ready = False
    
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
    
    async def get_stats(self) -> Optional[dict]:
        """Get daemon stats (async-safe)."""
        return await self.send_command({"cmd": "stats"})
    
    async def reload_database(self) -> Optional[dict]:
        """Reload database from disk (async-safe)."""
        return await self.send_command({"cmd": "reload"})
