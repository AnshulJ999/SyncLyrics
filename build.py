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

        # Copy .env.example to build output
        print("Copying .env.example to output directory...")
        src_env = Path(".env.example")
        dst_env = Path("build_final/SyncLyrics/.env.example")
        
        if src_env.exists():
            shutil.copy2(src_env, dst_env)
            print(f"Copied .env.example to {dst_env}")
            print("NOTE: Users should rename .env.example to .env and configure it!")
        else:
            print("WARNING: .env.example not found!")

        # Copy VBS launchers to build output
        print("Copying VBS launchers to output directory...")
        vbs_launchers = ["Run SyncLyrics (EXE).vbs"]
        for launcher in vbs_launchers:
            src_vbs = Path(launcher)
            dst_vbs = Path(f"build_final/{launcher}")
            
            if src_vbs.exists():
                shutil.copy2(src_vbs, dst_vbs)
                print(f"Copied {launcher} to build_final/")
            else:
                print(f"WARNING: {launcher} not found!")

        print("\n" + "="*60)
        print("Build completed successfully!")
        print("="*60)
        print(f"Output directory: build_final/SyncLyrics")
        print(f"\nHow to run:")
        print(f"  - Double-click: build_final/SyncLyrics/SyncLyrics.exe")
        print(f"  - Or use VBS:   build_final/Run SyncLyrics (EXE).vbs")
        print(f"\nIMPORTANT: Configure .env file before first run!")
        print(f"  1. Copy .env.example to .env in build_final/SyncLyrics/")
        print(f"  2. Edit .env with your Spotify API credentials")
        print("="*60)
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