import os
import shutil
import subprocess
import sys
from pathlib import Path

def clean_artifacts():
    """Remove previous build artifacts."""
    artifacts = ["build", "dist", "build_final", "build_output"]
    for artifact in artifacts:
        if os.path.exists(artifact):
            print(f"Removing {artifact}...")
            try:
                if os.path.isdir(artifact):
                    shutil.rmtree(artifact)
                else:
                    os.remove(artifact)
            except Exception as e:
                print(f"Error removing {artifact}: {e}")

def build():
    """Run PyInstaller build."""
    print("Starting SyncLyrics Build (PyInstaller)...")
    
    # Clean first
    clean_artifacts()
    
    # PyInstaller command
    # --clean: Clean PyInstaller cache
    # --noconfirm: Replace output directory without asking
    # --distpath build_final: Output to build_final directory
    cmd = [
        "pyinstaller",
        "sync_lyrics.spec",
        "--clean",
        "--noconfirm",
        "--distpath", "build_final"
    ]
    
    print(f"Running command: {' '.join(cmd)}")
    try:
        subprocess.run(cmd, check=True)
        
        # Manual Copy of Resources (PyInstaller often puts them in _internal)
        # We want them next to the EXE as per config.py
        print("Copying resources to output directory...")
        src_resources = Path("resources")
        dst_resources = Path("build_final/SyncLyrics/resources")
        
        if src_resources.exists():
            if dst_resources.exists():
                shutil.rmtree(dst_resources)
            shutil.copytree(src_resources, dst_resources)
            print(f"Copied resources to {dst_resources}")
        else:
            print("WARNING: Source 'resources' directory not found!")

        print("\nBuild completed successfully!")
        print("Output directory: build_final/SyncLyrics")
    except subprocess.CalledProcessError as e:
        print(f"\nBuild failed with exit code {e.returncode}")
        sys.exit(1)
    except Exception as e:
        print(f"\nError during post-build steps: {e}")
        sys.exit(1)

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "clean":
        clean_artifacts()
        print("Cleanup complete.")
    else:
        build()