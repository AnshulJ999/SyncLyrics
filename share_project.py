import os
import subprocess
import sys
import fnmatch

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
    'build_final',        # Added: Build artifact folder
    'dist',
    'venv',
    'env',
    '.git',
    '.idea',
    '.vscode',
    'logs',               # Added: Log files
    'album_art_database', # Added: Large database folder
    'lyrics_database',    # Added: Large database folder
    'cache',              # Added: Cache folder
    'terminals'           # Added: Terminals folder (just in case)
}

# 2. Specific files to ignore (exact filename matching)
SKIP_FILES = {
    'share_project.py',       # Don't include this script
    'full_project_code.txt',  # Don't include the output
    '.env',                   # Security: Never share secrets
    'package-lock.json',  
    'Run SyncLyrics Hidden.vbs',    # Too much noise
    'pnpm-lock.yaml',
    'poetry.lock',
    'poetry.lock'
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

def load_gitignore_patterns():
    """
    Reads .gitignore patterns if the file exists.
    Returns a list of patterns to ignore.
    """
    patterns = []
    if os.path.exists('.gitignore'):
        try:
            with open('.gitignore', 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    # Skip comments and empty lines
                    if line and not line.startswith('#'):
                        patterns.append(line)
        except Exception as e:
            print(f"⚠️  Could not read .gitignore: {e}")
    return patterns

def is_binary_extension(filename):
    """Checks if the filename has a binary extension."""
    _, ext = os.path.splitext(filename)
    return ext.lower() in BINARY_EXTENSIONS

def matches_gitignore(filepath, patterns):
    """
    Checks if a filepath matches any gitignore pattern.
    Uses fnmatch for simple wildcard matching.
    """
    if not patterns:
        return False
        
    # Standardize path separators to / for matching (git style)
    # Get path relative to current working directory
    rel_path = os.path.relpath(filepath).replace(os.sep, '/')
    filename = os.path.basename(filepath)
    
    for pattern in patterns:
        # 1. Match specific filenames (e.g. "*.log")
        if fnmatch.fnmatch(filename, pattern):
            return True
            
        # 2. Match relative paths (e.g. "dir/file.txt")
        if fnmatch.fnmatch(rel_path, pattern):
            return True
            
        # 3. Handle directory wildcards roughly (e.g. "build/" matching "build/file.txt")
        # If the pattern looks like a directory, check if it's in the path parts
        if pattern.endswith('/') and pattern[:-1] in rel_path.split('/'):
            return True

    return False

def should_skip(filepath, gitignore_patterns):
    """Decides if a file should be skipped based on User Configuration and .gitignore"""
    # Normalize path for cross-platform consistency
    norm_path = os.path.normpath(filepath)
    parts = norm_path.split(os.sep)
    filename = os.path.basename(norm_path)

    # 1. Check specific file exclusion
    if filename in SKIP_FILES:
        return "Skip List"

    # 2. Check directory exclusion
    # This checks if any parent folder of the file is in SKIP_FOLDERS
    if not set(parts).isdisjoint(SKIP_FOLDERS):
        return "Skip Folder"

    # 3. Check binary extension
    if is_binary_extension(filename):
        return "Binary"
        
    # 4. Check gitignore patterns
    if matches_gitignore(filepath, gitignore_patterns):
        return ".gitignore"

    return False

def merge_files():
    """Main function to scan and merge files."""
    print("🚀 Starting Project Share Script...")
    
    # Load gitignore patterns
    gitignore_patterns = load_gitignore_patterns()
    if gitignore_patterns:
        print(f"📋 Loaded {len(gitignore_patterns)} patterns from .gitignore")
    
    print("🔍 Scanning files in current directory...")
    
    count = 0
    skipped_count = 0
    skipped_details = []  # List to store skipped files for review
    
    try:
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as outfile:
            outfile.write("--- START OF PROJECT DUMP ---\n")
            
            # Walk through the directory tree
            for root, dirs, files in os.walk('.'):
                # Modify 'dirs' in-place to skip traversing ignored folders
                # This makes the script much faster and prevents checking massive ignored folders
                dirs[:] = [d for d in dirs if d not in SKIP_FOLDERS and not d.startswith('.')]
                
                for file in files:
                    filepath = os.path.join(root, file)
                    
                    # Clean up path display (remove ./ prefix)
                    display_path = filepath
                    if display_path.startswith('.' + os.sep):
                        display_path = display_path[2:]
                    
                    # Check if we should skip this file
                    # In Python, non-empty strings are Truthy, so this works for "reason" strings too
                    skip_reason = should_skip(filepath, gitignore_patterns)
                    if skip_reason:
                        skipped_count += 1
                        skipped_details.append(f"{display_path} [{skip_reason}]")
                        continue
                    
                    # Attempt to read and write the file
                    try:
                        with open(filepath, 'r', encoding='utf-8') as infile:
                            content = infile.read()
                            
                            outfile.write(f"\n\n{'='*60}\n")
                            outfile.write(f"FILE: {display_path}\n")
                            outfile.write(f"{'='*60}\n")
                            outfile.write(content)
                            
                            print(f"  + Added: {display_path}")
                            count += 1
                            
                    except UnicodeDecodeError:
                        print(f"  ⚠️  Skipping Non-UTF8: {display_path}")
                        skipped_count += 1
                        skipped_details.append(f"{display_path} [Non-UTF8]")
                    except Exception as e:
                        print(f"  ❌ Error reading {display_path}: {e}")
                        skipped_count += 1
                        skipped_details.append(f"{display_path} [Error]")

            outfile.write("\n\n--- END OF PROJECT DUMP ---\n")
            
        print(f"\n✅ Done! {count} files merged into '{OUTPUT_FILE}'")
        print(f"🙈 Skipped {skipped_count} files based on configuration.")
        
        # Print the list of excluded files
        if skipped_details:
            print("\n--- Skipped Files Review ---")
            for item in sorted(skipped_details):
                print(f"  - {item}")
            print("-" * 30)
        
    except Exception as e:
        print(f"\n❌ Critical Error: {e}")
        
    # Pause so user can review output
    print("\n" + "="*50)
    print("Review the list above to see what was included.")
    input("Press Enter to exit...")

if __name__ == "__main__":
    merge_files()