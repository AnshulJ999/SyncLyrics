import socket
import logging
from typing import Optional
from zeroconf import Zeroconf, ServiceInfo
from logging_config import get_logger

logger = get_logger(__name__)

class MDNSService:
    """
    Handles mDNS (Bonjour/Zeroconf) registration for the application.
    Allows accessing the app via http://synclyrics.local
    """
    def __init__(self, port: int):
        self.port = port
        self.zeroconf: Optional[Zeroconf] = None
        self.info: Optional[ServiceInfo] = None

    def _get_local_ip(self) -> str:
        """Best effort to get the actual LAN IP address."""
        try:
            # Connect to a public DNS server to determine the outgoing interface
            # We don't actually send data, just establish the route
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(0)
            try:
                s.connect(('8.8.8.8', 1))
                ip = s.getsockname()[0]
            except Exception:
                ip = '127.0.0.1'
            finally:
                s.close()
            return ip
        except Exception:
            return '127.0.0.1'

    def register(self):
        """Register the service on the local network."""
        try:
            local_ip = self._get_local_ip()
            if local_ip == '127.0.0.1':
                logger.warning("Could not determine LAN IP, skipping mDNS registration")
                return

            logger.info(f"Registering mDNS service: http://synclyrics.local:{self.port}")
            
            self.zeroconf = Zeroconf()
            
            # Service type: _http._tcp.local.
            # Service name: SyncLyrics._http._tcp.local.
            service_type = "_http._tcp.local."
            service_name = "SyncLyrics._http._tcp.local."
            
            self.info = ServiceInfo(
                service_type,
                service_name,
                addresses=[socket.inet_aton(local_ip)],
                port=self.port,
                properties={'version': '1.0.0', 'path': '/'},
                server="synclyrics.local."
            )
            
            self.zeroconf.register_service(self.info)
            logger.info("mDNS registration successful")
            
        except Exception as e:
            logger.error(f"Failed to register mDNS service: {e}")
            self.unregister()

    def unregister(self):
        """Unregister the service."""
        if self.zeroconf:
            try:
                logger.info("Unregistering mDNS service...")
                if self.info:
                    self.zeroconf.unregister_service(self.info)
                self.zeroconf.close()
            except Exception as e:
                logger.error(f"Error unregistering mDNS: {e}")
            finally:
                self.zeroconf = None
                self.info = None
