"""
SyncLyrics Test Runner
Runs all tests and generates a report
"""

import unittest
import sys
from pathlib import Path
from datetime import datetime
from logging_config import setup_logging, get_logger

# Set up test logging
setup_logging(
    level="DEBUG",
    console=True,
    detailed=True
)

logger = get_logger(__name__)

def run_tests():
    """Run all tests and generate report"""
    # Start timing
    start_time = datetime.now()
    logger.info("Starting SyncLyrics test suite")
    
    # Discover and run tests
    loader = unittest.TestLoader()
    start_dir = Path(__file__).parent
    suite = loader.discover(start_dir, pattern="test_*.py")
    
    # Run tests with verbosity
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    # Log results
    duration = (datetime.now() - start_time).total_seconds()
    logger.info(f"\nTest Suite Complete:")
    logger.info(f"Ran {result.testsRun} tests in {duration:.2f}s")
    logger.info(f"Successes: {result.testsRun - len(result.failures) - len(result.errors)}")
    logger.info(f"Failures: {len(result.failures)}")
    logger.info(f"Errors: {len(result.errors)}")
    
    return len(result.failures) + len(result.errors)

if __name__ == "__main__":
    sys.exit(run_tests()) 