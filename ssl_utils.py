"""
SSL Certificate Utilities

Generates self-signed certificates for HTTPS support.
Required for browser microphone access on non-localhost.

Usage:
    from ssl_utils import ensure_ssl_certs
    cert_path, key_path = ensure_ssl_certs(cert_dir)
    if cert_path:
        # Configure Hypercorn with SSL
        config.certfile = str(cert_path)
        config.keyfile = str(key_path)
"""

import os
import socket
from pathlib import Path
from datetime import datetime, timedelta
from typing import Tuple, List, Optional

from logging_config import get_logger

logger = get_logger(__name__)

# Try to import cryptography, fall back to subprocess openssl
try:
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.backends import default_backend
    import ipaddress
    CRYPTOGRAPHY_AVAILABLE = True
except ImportError:
    CRYPTOGRAPHY_AVAILABLE = False
    logger.warning("cryptography library not available - HTTPS requires 'pip install cryptography'")


def generate_self_signed_cert(
    cert_path: Path,
    key_path: Path,
    hostnames: Optional[List[str]] = None,
    ip_addresses: Optional[List[str]] = None,
    days_valid: int = 365
) -> bool:
    """
    Generate a self-signed certificate.
    
    Args:
        cert_path: Path to save certificate (.crt/.pem)
        key_path: Path to save private key (.key/.pem)
        hostnames: List of hostnames (e.g., ["localhost", "synclyrics.local"])
        ip_addresses: List of IP addresses (e.g., ["192.168.1.3", "127.0.0.1"])
        days_valid: Certificate validity in days
        
    Returns:
        True if successful
    """
    hostnames = hostnames or ["localhost"]
    ip_addresses = ip_addresses or ["127.0.0.1"]
    
    # Ensure directories exist
    cert_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.parent.mkdir(parents=True, exist_ok=True)
    
    if CRYPTOGRAPHY_AVAILABLE:
        return _generate_with_cryptography(
            cert_path, key_path, hostnames, ip_addresses, days_valid
        )
    else:
        logger.error(
            "Cannot generate SSL certificate: 'cryptography' library not installed.\n"
            "Install it with: pip install cryptography"
        )
        return False


def _generate_with_cryptography(
    cert_path: Path,
    key_path: Path,
    hostnames: List[str],
    ip_addresses: List[str],
    days_valid: int
) -> bool:
    """Generate certificate using cryptography library."""
    try:
        # Generate private key
        key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
            backend=default_backend()
        )
        
        # Build subject
        subject = issuer = x509.Name([
            x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
            x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "Local"),
            x509.NameAttribute(NameOID.LOCALITY_NAME, "Local"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "SyncLyrics"),
            x509.NameAttribute(NameOID.COMMON_NAME, hostnames[0]),
        ])
        
        # Build SAN (Subject Alternative Names)
        san_entries = []
        for hostname in hostnames:
            san_entries.append(x509.DNSName(hostname))
        for ip in ip_addresses:
            try:
                san_entries.append(x509.IPAddress(ipaddress.ip_address(ip)))
            except ValueError:
                logger.warning(f"Invalid IP address skipped: {ip}")
        
        # Build certificate
        cert = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(datetime.utcnow())
            .not_valid_after(datetime.utcnow() + timedelta(days=days_valid))
            .add_extension(
                x509.SubjectAlternativeName(san_entries),
                critical=False,
            )
            .sign(key, hashes.SHA256(), default_backend())
        )
        
        # Write private key
        with open(key_path, "wb") as f:
            f.write(key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption()
            ))
        
        # Write certificate
        with open(cert_path, "wb") as f:
            f.write(cert.public_bytes(serialization.Encoding.PEM))
        
        logger.info(f"Generated self-signed certificate: {cert_path}")
        logger.info(f"Certificate valid for: {', '.join(hostnames + ip_addresses)}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to generate certificate: {e}")
        return False


def get_local_ip() -> Optional[str]:
    """Get the local IP address of this machine."""
    try:
        # Connect to a remote address to determine local IP
        # (doesn't actually send data)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        return local_ip
    except Exception:
        return None


def ensure_ssl_certs(
    cert_dir: Path,
    hostnames: Optional[List[str]] = None,
    ip_addresses: Optional[List[str]] = None
) -> Tuple[Optional[Path], Optional[Path]]:
    """
    Ensure SSL certificates exist, generating if needed.
    
    Args:
        cert_dir: Directory to store certificates
        hostnames: Optional list of hostnames (defaults to ["localhost"])
        ip_addresses: Optional list of IPs (auto-detects local IP if None)
        
    Returns:
        Tuple of (cert_path, key_path), or (None, None) if failed
    """
    cert_path = cert_dir / "server.crt"
    key_path = cert_dir / "server.key"
    
    # Check if certs already exist
    if cert_path.exists() and key_path.exists():
        logger.info(f"SSL certificates found at {cert_dir}")
        return (cert_path, key_path)
    
    # Check if cryptography is available
    if not CRYPTOGRAPHY_AVAILABLE:
        logger.error(
            "HTTPS requires the 'cryptography' library.\n"
            "Install it with: pip install cryptography"
        )
        return (None, None)
    
    # Generate new certs
    logger.info("Generating new SSL certificates...")
    
    # Auto-detect local IPs if not provided
    if ip_addresses is None:
        ip_addresses = ["127.0.0.1"]
        local_ip = get_local_ip()
        if local_ip and local_ip not in ip_addresses:
            ip_addresses.append(local_ip)
            logger.info(f"Auto-detected local IP: {local_ip}")
    
    # Default hostnames
    if hostnames is None:
        hostnames = ["localhost"]
        try:
            hostname = socket.gethostname()
            if hostname and hostname not in hostnames:
                hostnames.append(hostname)
        except Exception:
            pass
    
    if generate_self_signed_cert(cert_path, key_path, hostnames, ip_addresses):
        return (cert_path, key_path)
    
    return (None, None)


def check_cert_expiry(cert_path: Path) -> Optional[datetime]:
    """
    Check when a certificate expires.
    
    Args:
        cert_path: Path to certificate file
        
    Returns:
        Expiry datetime, or None if unable to read
    """
    if not CRYPTOGRAPHY_AVAILABLE:
        return None
        
    try:
        with open(cert_path, "rb") as f:
            cert_data = f.read()
        cert = x509.load_pem_x509_certificate(cert_data, default_backend())
        return cert.not_valid_after
    except Exception as e:
        logger.error(f"Failed to check certificate expiry: {e}")
        return None
