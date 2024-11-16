"""
SyncLyrics Test Suite
Comprehensive test runner for all components
"""

import unittest
import sys
import os
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List

# Add parent directory to path
sys.path.append(str(Path(__file__).parent.parent))

from logging_config import setup_logging, get_logger

# Set up test-specific logging
setup_logging(
    level="DEBUG",
    console=True,
    detailed=True
)

logger = get_logger(__name__)

class TestBase(unittest.TestCase):
    """Base test class with common utilities"""
    
    @classmethod
    def setUpClass(cls):
        """Set up test class"""
        cls.logger = get_logger(cls.__name__)
        cls.start_time = datetime.now()
        cls.logger.info(f"Starting {cls.__name__} tests")
    
    @classmethod
    def tearDownClass(cls):
        """Clean up after tests"""
        duration = (datetime.now() - cls.start_time).total_seconds()
        cls.logger.info(f"Completed {cls.__name__} tests in {duration:.2f}s")

    def setUp(self):
        """Set up each test"""
        self.test_start = datetime.now()
        self.logger.info(f"\nRunning: {self._testMethodName}")
    
    def tearDown(self):
        """Clean up after each test"""
        duration = (datetime.now() - self.test_start).total_seconds()
        self.logger.info(f"Test completed in {duration:.2f}s") 