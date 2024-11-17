"""
SyncLyrics Test Runner
"""
import pytest
import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.append(str(Path(__file__).parent.parent))

import pytest
import asyncio
from datetime import datetime
from typing import Dict
import logging

# Configure logging first
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

logger = logging.getLogger(__name__)

class TestRunner:
    def __init__(self):
        self.results: Dict[str, Dict] = {}
        
    async def run_all_tests(self):
        """Run all tests"""
        logger.info("Starting test suite...")
        start_time = datetime.now()
        
        try:
            # Run pytest with correct arguments
            test_path = str(Path(__file__).parent)
            result = pytest.main([
                '-v',                    # Verbose output
                '--capture=no',          # Show print statements
                '--log-cli-level=INFO',  # Show logs
                test_path               # Test directory
            ])
            
            status = 'passed' if result == pytest.ExitCode.OK else 'failed'
            self.results['pytest'] = {'status': status}
            
        except Exception as e:
            logger.error(f"Test suite failed: {e}")
            self.results['pytest'] = {'status': 'failed', 'error': str(e)}
            
        finally:
            duration = (datetime.now() - start_time).total_seconds()
            logger.info(f"Test suite completed in {duration:.2f}s")
            
            # Print summary
            logger.info("Test Results Summary:")
            for test, result in self.results.items():
                logger.info(f"{test}: {result['status']}")

def main():
    """Run the test suite"""
    runner = TestRunner()
    asyncio.run(runner.run_all_tests())

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Test suite interrupted by user")
    except Exception as e:
        logger.error(f"Test suite failed: {e}")
        sys.exit(1) 