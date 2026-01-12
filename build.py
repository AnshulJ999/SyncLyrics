import os
import platform
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


def get_spec_file():
    """Get the appropriate spec file for the current platform."""
    system = platform.system()
    if system == "Windows":
        return "sync_lyrics.spec"
    elif system == "Linux":
        return "sync_lyrics_linux.spec"
    elif system == "Darwin":  # macOS
        return "sync_lyrics_macos.spec"
    else:
        print(f"Unsupported platform: {system}")
        sys.exit(1)


def get_executable_name():
    """Get the executable name for the current platform."""
    if platform.system() == "Windows":
        return "SyncLyrics.exe"
    else:
        return "SyncLyrics"


def build(debug_mode=False):
    """Run PyInstaller build.
    
    Args:
        debug_mode: If True, build with console window enabled for debugging.
    """
    system = platform.system()
    mode_str = "DEBUG (with console)" if debug_mode else "RELEASE (no console)"
    print(f"Starting SyncLyrics Build (PyInstaller) - {mode_str}...")
    print(f"Platform: {system}")
    
    # Clean first
    clean_artifacts()
    
    spec_file = get_spec_file()
    temp_spec_file = None
    
    # Check spec file exists
    if not os.path.exists(spec_file):
        print(f"ERROR: Spec file not found: {spec_file}")
        sys.exit(1)
    
    # For debug builds, create a temporary spec file with console=True
    if debug_mode:
        print("Creating debug spec file with console enabled...")
        temp_spec_file = spec_file.replace(".spec", "_debug_temp.spec")
        
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
        
        # Determine output directory based on platform
        # NOTE: macOS now uses folder output (same as Linux) - no .app bundle
        output_dir = Path("build_final/SyncLyrics")
        resources_dst = output_dir / "resources"
        
        # Copy resources (all platforms)
        print("Copying resources to output directory...")
        src_resources = Path("resources")
        
        if src_resources.exists():
            if resources_dst.exists():
                shutil.rmtree(resources_dst)
            shutil.copytree(src_resources, resources_dst)
            print(f"Copied resources to {resources_dst}")
        else:
            print("WARNING: Source 'resources' directory not found!")

        # Copy .env.example to build output
        print("Copying .env.example to output directory...")
        src_env = Path(".env.example")
        dst_env = output_dir / ".env.example"
        
        if src_env.exists():
            shutil.copy2(src_env, dst_env)
            print(f"Copied .env.example to {dst_env}")
        else:
            print("WARNING: .env.example not found!")

        # Copy docs folder to build output
        print("Copying documentation to output directory...")
        src_docs = Path("docs")
        dst_docs = output_dir / "docs"
        
        if src_docs.exists():
            if dst_docs.exists():
                shutil.rmtree(dst_docs)
            shutil.copytree(src_docs, dst_docs)
            print(f"Copied docs to {dst_docs}")
        else:
            print("WARNING: docs directory not found!")

        # Copy README.md to build output
        src_readme = Path("README.md")
        dst_readme = output_dir / "README.md"
        
        if src_readme.exists():
            shutil.copy2(src_readme, dst_readme)
            print(f"Copied README.md to {dst_readme}")
        else:
            print("WARNING: README.md not found!")

        # Print success message
        print("\n" + "="*60)
        print(f"Build completed successfully! ({mode_str})")
        print("="*60)
        
        exe_name = get_executable_name()
        print(f"Output: build_final/SyncLyrics/")
        print(f"\nHow to run:")
        print(f"  - Terminal: cd build_final/SyncLyrics && ./{exe_name}")
            
        if debug_mode:
            print(f"  - Console window will appear with logs")
            
        print(f"\nOptional: Spotify Integration")
        print(f"  - App works without .env (uses available media sources)")
        print(f"  - For Spotify: Copy .env.example to .env and add credentials")
        print("="*60)
        
    except subprocess.CalledProcessError as e:
        if temp_spec_file and os.path.exists(temp_spec_file):
            os.remove(temp_spec_file)
        print(f"\nBuild failed with exit code {e.returncode}")
        sys.exit(1)
    except Exception as e:
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
    print("")
    print(f"Current platform: {platform.system()}")
    print(f"Spec file: {get_spec_file()}")


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