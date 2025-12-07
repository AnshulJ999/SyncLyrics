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
    'AI Docs',
    '/AI Docs',
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
    'bootstrap.min.css',
    'bootstrap.min.js',
    'bootstrap.bundle.min.js',
    'bootstrap.bundle.min.js.map',
    'bootstrap.min.js.map',
    'bootstrap.min.css.map',
    'bootstrap.min.css.map',
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

def get_git_files():
    """
    Retrieves a list of files that git tracks or are untracked-but-not-ignored.
    Returns None if git is not available or fails.
    """
    try:
        # Check if we are in a git repo
        subprocess.run(['git', 'rev-parse', '--is-inside-work-tree'], 
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        
        # Run git ls-files
        # -c: cached (tracked)
        # -o: others (untracked)
        # --exclude-standard: respect .gitignore
        # -z: use NUL termination for safety with spaces
        result = subprocess.run(
            ['git', 'ls-files', '-c', '-o', '--exclude-standard', '-z'],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace'
        )
        if result.returncode == 0:
            files = result.stdout.split('\0')
            return [os.path.normpath(f) for f in files if f.strip()]
    except Exception:
        pass
    return None

def load_gitignore_patterns():
    """
    Reads .gitignore patterns if the file exists.
    """
    patterns = []
    if os.path.exists('.gitignore'):
        try:
            with open('.gitignore', 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
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
    Robust fallback matcher for when git is not available.
    Supports basic gitignore syntax including:
    - Negations (!)
    - Directory matches (ends with /)
    - Anchored matches (starts with /)
    - Wildcards (*, ?)
    """
    if not patterns:
        return False
        
    # Prepare path for matching (relative, forward slashes)
    rel_path = os.path.relpath(filepath).replace(os.sep, '/')
    filename = os.path.basename(filepath)
    
    # Gitignore rules apply in order, with later rules overriding earlier ones.
    # We scan all patterns to determine final state.
    should_ignore = False
    
    for pattern in patterns:
        is_negation = pattern.startswith('!')
        if is_negation:
            p = pattern[1:]
        else:
            p = pattern
            
        matches_this = False
        
        # 1. Directory Match (e.g. "build/")
        if p.endswith('/'):
            dir_name = p.rstrip('/')
            
            if p.startswith('/'):
                # Anchored directory: "/build/" matches "build/file" but not "src/build/file"
                # Check if path starts with this dir
                if rel_path == dir_name or rel_path.startswith(dir_name + '/'):
                    matches_this = True
            else:
                # Floating directory: "build/" matches "src/build/file"
                # Check if dir_name is any component of the path
                path_parts = rel_path.split('/')
                if dir_name in path_parts:
                    matches_this = True

        # 2. Path/File Match
        elif '/' in p:
            # Pattern contains slash: match against full relative path
            # e.g. "src/main.py" or "/src/*.py"
            p_clean = p.lstrip('/') # fnmatch doesn't like leading slash usually
            if fnmatch.fnmatch(rel_path, p_clean):
                matches_this = True
                
        # 3. Filename Match (no slash)
        else:
            # Pattern is just filename: "*.log" matches "logs/error.log"
            if fnmatch.fnmatch(filename, p):
                matches_this = True
                
        # Apply result
        if matches_this:
            should_ignore = not is_negation
            
    return should_ignore

def should_skip(filepath, gitignore_patterns, use_gitignore_check=True):
    """Decides if a file should be skipped."""
    norm_path = os.path.normpath(filepath)
    parts = norm_path.split(os.sep)
    filename = os.path.basename(norm_path)

    # 1. Check specific file exclusion
    if filename in SKIP_FILES:
        return "Skip List"

    # 2. Check directory exclusion
    if not set(parts).isdisjoint(SKIP_FOLDERS):
        return "Skip Folder"

    # 3. Check binary extension
    if is_binary_extension(filename):
        return "Binary"
        
    # 4. Check gitignore patterns (Fallback logic)
    if use_gitignore_check and matches_gitignore(filepath, gitignore_patterns):
        return ".gitignore"

    return False

def merge_files():
    """Main function to scan and merge files."""
    print("🚀 Starting Project Share Script...")
    
    files_to_process = []
    using_git = False
    gitignore_patterns = []

    # Strategy 1: Try Git (Most Reliable)
    git_files = get_git_files()
    
    if git_files is not None:
        print(f"✅ Git detected. Using git to identify valid project files.")
        files_to_process = git_files
        using_git = True
    else:
        # Strategy 2: Fallback scan
        print("⚠️  Git not available or not a repo. Falling back to directory scan.")
        gitignore_patterns = load_gitignore_patterns()
        if gitignore_patterns:
            print(f"📋 Loaded {len(gitignore_patterns)} patterns from .gitignore")
            
        print("🔍 Scanning directory tree...")
        for root, dirs, files in os.walk('.'):
            # Optimization: Skip ignored folders during walk
            dirs[:] = [d for d in dirs if d not in SKIP_FOLDERS and not d.startswith('.')]
            
            for file in files:
                filepath = os.path.join(root, file)
                files_to_process.append(filepath)

    print(f"📦 Found {len(files_to_process)} candidates. filtering...")
    
    count = 0
    skipped_count = 0
    skipped_details = []
    
    try:
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as outfile:
            outfile.write("--- START OF PROJECT DUMP ---\n")
            
            for filepath in files_to_process:
                # Clean up path display
                display_path = filepath
                if display_path.startswith('.' + os.sep):
                    display_path = display_path[2:]
                
                # Check skip rules
                # If using git, we don't need to re-check gitignore (git already filtered it)
                skip_reason = should_skip(filepath, gitignore_patterns, use_gitignore_check=not using_git)
                
                if skip_reason:
                    skipped_count += 1
                    continue
                
                # Read/Write
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
        
        if skipped_details:
            print("\n--- Skipped Files Review ---")
            for item in sorted(skipped_details):
                print(f"  - {item}")
        
    except Exception as e:
        print(f"\n❌ Critical Error: {e}")
        
    print("\n" + "="*50)
    print("Review the list above to see what was included.")
    input("Press Enter to exit...")

if __name__ == "__main__":
    merge_files()