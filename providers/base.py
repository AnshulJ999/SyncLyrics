"""
Base Provider Class
All lyrics providers must inherit from this base class.
"""

from abc import ABC, abstractmethod
from typing import Optional, List, Tuple
import requests
import logging
from logging_config import get_logger, setup_logging  # Import setup_logging

# Set up logging
# logging.basicConfig(level=logging.INFO)

setup_logging()

logger = get_logger(__name__)

class LyricsProvider(ABC):
    """
    Base class for all lyrics providers.
    
    Each provider must implement:
    - get_lyrics(artist: str, title: str) method
    """
    
    def __init__(self, name: str, priority: int = 100):
        """
        Initialize the provider
        
        Args:
            name (str): Name of the provider
            priority (int): Priority level (lower number = higher priority)
        """
        self.name = name
        self.priority = priority
        self.enabled = True
        self.session = requests.Session()
        logger.info(f"Initialized {self.name} provider")
    
    @abstractmethod
    def get_lyrics(self, artist: str, title: str) -> Optional[List[Tuple[float, str]]]:
        """
        Get synchronized lyrics for a song.
        
        Args:
            artist (str): Artist name
            title (str): Song title
            
        Returns:
            Optional[List[Tuple[float, str]]]: List of (timestamp, lyric) pairs
                                             or None if lyrics not found
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
