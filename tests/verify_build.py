import os
from pathlib import Path
import sys
import subprocess
import time

def verify_build():
    print("Verifying SyncLyrics Build...")
    
    # Define paths
    ROOT_DIR = Path(__file__).parent
    BUILD_DIR = ROOT_DIR / "build_final" / "SyncLyrics"
    EXE_PATH = BUILD_DIR / "SyncLyrics.exe"
    RESOURCES_DIR = BUILD_DIR / "resources"
    INTERNAL_DIR = BUILD_DIR / "_internal"
    
    # Checks
    checks = [
        ("Executable exists", EXE_PATH.exists()),
        ("Resources directory exists", RESOURCES_DIR.exists()),
        ("Internal directory exists", INTERNAL_DIR.exists()),
        ("Icon exists in resources", (RESOURCES_DIR / "images" / "icon.ico").exists()),
    ]
    
    all_passed = True
    for name, passed in checks:
        status = "PASS" if passed else "FAIL"
        print(f"[{status}] {name}")
        if not passed:
            all_passed = False
            
    if not all_passed:
        print("\nBuild verification FAILED: Missing files.")
        return False
        
    print("\nFiles check passed. Attempting to run executable (timeout 5s)...")
    
    try:
        # Run the exe and wait for 5 seconds
        # We expect it to keep running, so a TimeoutExpired is actually good (means it didn't crash immediately)
        subprocess.run([str(EXE_PATH)], timeout=5, check=True)
    except subprocess.TimeoutExpired:
        print("[PASS] Executable ran for 5 seconds without crashing.")
    except subprocess.CalledProcessError as e:
        print(f"[FAIL] Executable crashed with exit code {e.returncode}.")
        return False
    except Exception as e:
        print(f"[FAIL] Error running executable: {e}")
        return False
        
    print("\nBuild verification PASSED!")
    return True

if __name__ == "__main__":
    success = verify_build()
    sys.exit(0 if success else 1)
