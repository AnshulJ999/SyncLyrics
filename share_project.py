import os
import subprocess
import sys

# python share_project.py

# ==========================================
# --- ⚙️ USER CONFIGURATION START HERE ---
# ==========================================

OUTPUT_FILE = "full_project_code.txt"

# 1. Folders to completely ignore (exact folder name matching)
# Any file found inside these folders (or their subfolders) will be skipped.
SKIP_FOLDERS = {
    'tests',
    'Unused',
    'screenshots',
    '__pycache__',
    'build',
    'dist',
    'venv',
    'env',
    '.git',
    '.idea',
    '.vscode'
}

# 2. Specific files to ignore (exact filename matching)
SKIP_FILES = {
    'share_project.py',       # Don't include this script
    'full_project_code.txt',  # Don't include the output
    '.env',                   # Security: Never share secrets
    'package-lock.json',      # Too much noise
    'pnpm-lock.yaml',
    'poetry.lock',
    'sync_lyrics.spec'        # PyInstaller spec (optional, remove if you want to share it)
}

# 3. Binary/Junk extensions to skip (automatically skipped)
BINARY_EXTENSIONS = {
    '.pyc', '.exe', '.dll', '.bin', '.png', '.jpg', '.jpeg', '.ico', '.gif', 
    '.zip', '.tar', '.gz', '.7z', '.pdf', '.woff', '.ttf', '.eot', '.db', 
    '.sqlite', '.mp3', '.wav', '.so', '.pyd'
}

# ==========================================
# --- ⚙️ CONFIGURATION END ---
# ==========================================

def is_binary_extension(filename):
    _, ext = os.path.splitext(filename)
    return ext.lower() in BINARY_EXTENSIONS

def get_git_files():
    """Retrives file list from git, respecting .gitignore"""
    try:
        # Check if git repo
        subprocess.check_output(['git', 'rev-parse', '--is-inside-work-tree'], stderr=subprocess.STDOUT)
        
        # Get list (cached=committed, others=new/untracked, exclude-standard=apply gitignore)
        result = subprocess.check_output(
            ['git', 'ls-files', '--cached', '--others', '--exclude-standard'],
            encoding='utf-8'
        )
        return [f for f in result.split('\n') if f.strip()]
    except Exception:
        print("⚠️  Error: Not a git repository or git not found.")
        return []

def should_skip(filepath):
    """Decides if a file should be skipped based on User Configuration"""
    # Normalize path for cross-platform consistency
    norm_path = os.path.normpath(filepath)
    parts = norm_path.split(os.sep)
    filename = os.path.basename(norm_path)

    # 1. Check specific file exclusion
    if filename in SKIP_FILES:
        return True

    # 2. Check directory exclusion
    # This checks if any parent folder of the file is in SKIP_FOLDERS
    # e.g. 'tests/subfolder/file.py' -> 'tests' is in parts, so it skips.
    if not set(parts).isdisjoint(SKIP_FOLDERS):
        return True

    # 3. Check binary extension
    if is_binary_extension(filename):
        return True

    return False

def merge_files():
    files = get_git_files()
    
    if not files:
        print("No files found. Is this a git repo?")
        return

    print(f"🔍 Scanning {len(files)} files from Git...")
    
    count = 0
    skipped_count = 0
    
    try:
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as outfile:
            outfile.write("--- START OF PROJECT DUMP ---\n")
            
            for filepath in files:
                if should_skip(filepath):
                    skipped_count += 1
                    continue
                
                # Double check file existence (git ls-files might list deleted files if index isn't updated)
                if not os.path.exists(filepath):
                    continue

                try:
                    with open(filepath, 'r', encoding='utf-8') as infile:
                        content = infile.read()
                        
                        outfile.write(f"\n\n{'='*60}\n")
                        outfile.write(f"FILE: {filepath}\n")
                        outfile.write(f"{'='*60}\n")
                        outfile.write(content)
                        
                        print(f"  + Added: {filepath}")
                        count += 1
                        
                except UnicodeDecodeError:
                    print(f"  ⚠️  Skipping Non-UTF8: {filepath}")
                except Exception as e:
                    print(f"  ❌ Error reading {filepath}: {e}")

            outfile.write("\n\n--- END OF PROJECT DUMP ---\n")
            
        print(f"\n✅ Done! {count} files merged into '{OUTPUT_FILE}'")
        print(f"🙈 Skipped {skipped_count} files based on configuration.")
        
    except Exception as e:
        print(f"\n❌ Critical Error: {e}")

if __name__ == "__main__":
    merge_files()