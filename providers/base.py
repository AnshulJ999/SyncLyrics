"""
Base Provider Class
All lyrics providers must inherit from this base class.
"""

import sys
from pathlib import Path

# Add project root to path
sys.path.append(str(Path(__file__).parent.parent)) 

from abc import ABC, abstractmethod
from typing import Optional, List, Tuple, Dict, Any
import requests
import logging
from logging_config import get_logger  # Removed setup_logging import - logging is configured in sync_lyrics.py
from config import get_provider_config  # Add this import

# Set up logging
# logging.basicConfig(level=logging.INFO)

# setup_logging()  # REMOVED: Early call overrides main config from sync_lyrics.py
# Logging is configured once in sync_lyrics.py with proper environment variable support
logger = get_logger(__name__)

class LyricsProvider(ABC):
    """Base class for all lyrics providers."""
    
    def __init__(self, provider_name: str):
        """
        Initialize the provider using configuration from config.py
        
        Args:
            provider_name (str): Name of the provider (must match config key)
        """
        # Get provider config
        config = get_provider_config(provider_name.lower())
        
        # Set provider attributes from config
        self.name = provider_name
        self.priority = config.get('priority', 100)
        self.enabled = config.get('enabled', True)
        self.timeout = config.get('timeout', 10)
        self.retries = config.get('retries', 3)
        
        # Initialize session
        self.session = requests.Session()
        self.session.timeout = self.timeout
    
        if self.enabled:
            logger.info(f"Initialized {self.name} provider (priority: {self.priority})")
        else:
            logger.info(f"{self.name} provider is disabled")
    
    @abstractmethod
    def get_lyrics(self, artist: str, title: str, 
                   album: str = None, duration: int = None) -> Optional[Dict[str, Any]]:
        """
        Get synchronized lyrics for a song.
        
        Args:
            artist (str): Artist name
            title (str): Song title
            album (str, optional): Album name for better matching
            duration (int, optional): Track duration in seconds for scoring/verification
            
        Returns:
            Optional[Dict[str, Any]]: Result dictionary containing:
                {
                    "lyrics": List[Tuple[float, str]],  # synced lines
                    "is_instrumental": bool,            # optional metadata
                    "plain_lyrics": str,                # optional fallback
                    ...
                }
        """
        pass
    
    def _format_search_term(self, artist: str, title: str) -> str:
        """
        Format artist and title for searching
        
        Args:
            artist (str): Artist name
            title (str): Song title
            
        Returns:
            str: Formatted search term
        """
        return f"{artist} {title}".strip()
    
    def __str__(self) -> str:
        """String representation of the provider"""
        status = "enabled" if self.enabled else "disabled"
        return f"{self.name} Provider (Priority: {self.priority}, Status: {status})"
    
    def __repr__(self) -> str:
        """Detailed representation of the provider"""
        return f"<{self.__class__.__name__} name='{self.name}' priority={self.priority} enabled={self.enabled}>" 
