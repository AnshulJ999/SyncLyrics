import os
import re
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

def build(debug_mode=False):
    """Run PyInstaller build.
    
    Args:
        debug_mode: If True, build with console window enabled for debugging.
    """
    mode_str = "DEBUG (with console)" if debug_mode else "RELEASE (no console)"
    print(f"Starting SyncLyrics Build (PyInstaller) - {mode_str}...")
    
    # Clean first
    clean_artifacts()
    
    spec_file = "sync_lyrics.spec"
    temp_spec_file = None
    
    # For debug builds, create a temporary spec file with console=True
    if debug_mode:
        print("Creating debug spec file with console enabled...")
        temp_spec_file = "sync_lyrics_debug_temp.spec"
        
        with open(spec_file, "r", encoding="utf-8") as f:
            spec_content = f.read()
        
        # Replace console=False with console=True
        spec_content = re.sub(
            r'console\s*=\s*False',
            'console=True,  # DEBUG BUILD - console enabled',
            spec_content
        )
        
        with open(temp_spec_file, "w", encoding="utf-8") as f:
            f.write(spec_content)
        
        spec_file = temp_spec_file
    
    # PyInstaller command
    # --clean: Clean PyInstaller cache
    # --noconfirm: Replace output directory without asking
    # --distpath build_final: Output to build_final directory
    cmd = [
        "pyinstaller",
        spec_file,
        "--clean",
        "--noconfirm",
        "--distpath", "build_final"
    ]
    
    print(f"Running command: {' '.join(cmd)}")
    try:
        subprocess.run(cmd, check=True)
        
        # Clean up temporary spec file
        if temp_spec_file and os.path.exists(temp_spec_file):
            os.remove(temp_spec_file)
            print(f"Cleaned up temporary spec file: {temp_spec_file}")
        
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
            print("OPTIONAL: For Spotify integration, rename .env.example to .env and add credentials")
        else:
            print("WARNING: .env.example not found!")

        # Copy VBS launchers to build output
        print("Copying VBS launchers to output directory...")
        vbs_launchers = ["Run SyncLyrics Hidden.vbs"]
        for launcher in vbs_launchers:
            src_vbs = Path(launcher)
            dst_vbs = Path(f"build_final/{launcher}")
            
            if src_vbs.exists():
                shutil.copy2(src_vbs, dst_vbs)
                print(f"Copied {launcher} to build_final/")
            else:
                print(f"WARNING: {launcher} not found!")

        print("\n" + "="*60)
        print(f"Build completed successfully! ({mode_str})")
        print("="*60)
        print(f"Output directory: build_final/SyncLyrics")
        print(f"\nHow to run:")
        print(f"  - Double-click: build_final/SyncLyrics/SyncLyrics.exe")
        if debug_mode:
            print(f"  - Console window will appear with logs")
        print(f"\nOptional: Spotify Integration")
        print(f"  - App works without .env (Windows Media + LRCLib/NetEase/QQ lyrics)")
        print(f"  - For Spotify: Copy .env.example to .env and add credentials")
        print("="*60)
    except subprocess.CalledProcessError as e:
        # Clean up temporary spec file on error
        if temp_spec_file and os.path.exists(temp_spec_file):
            os.remove(temp_spec_file)
        print(f"\nBuild failed with exit code {e.returncode}")
        sys.exit(1)
    except Exception as e:
        # Clean up temporary spec file on error
        if temp_spec_file and os.path.exists(temp_spec_file):
            os.remove(temp_spec_file)
        print(f"\nError during post-build steps: {e}")
        sys.exit(1)

def print_usage():
    """Print usage information."""
    print("SyncLyrics Build Script")
    print("="*40)
    print("Usage:")
    print("  python build.py           Build release version (no console)")
    print("  python build.py --debug   Build debug version (with console)")
    print("  python build.py clean     Remove build artifacts only")
    print("  python build.py --help    Show this help message")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        arg = sys.argv[1].lower()
        if arg == "clean":
            clean_artifacts()
            print("Cleanup complete.")
        elif arg == "--debug" or arg == "-d":
            build(debug_mode=True)
        elif arg == "--help" or arg == "-h":
            print_usage()
        else:
            print(f"Unknown argument: {sys.argv[1]}")
            print_usage()
            sys.exit(1)
    else:
        build(debug_mode=False)