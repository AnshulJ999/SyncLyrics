import os

# --- CONFIGURATION ---

# python share_project.py

# Folders to skip completely
IGNORE_DIRS = {
    '.git', '__pycache__', 'venv', 'env', 'build', 'dist', 
    'logs', 'cache', 'sync_lyrics.build', 'sync_lyrics.dist', 
    'sync_lyrics.onefile-build', 'icons', 'images', 'node_modules',
    'build_final', 'build_output', '_internal'
}

# File extensions to skip (Binaries, Images, etc.)
IGNORE_EXTS = {
    '.pyc', '.exe', '.bin', '.png', '.ico', '.jpg', '.zip', 
    '.mp3', '.wav', '.dll', '.pyd', '.pdf', '.spec'
}

# Specific files to skip
IGNORE_FILES = {
    'share_project.py',     # Don't include this script
    'full_project_code.txt',# Don't include the output file
    '.env',                 # SECURITY: Never share your API keys
    'state.json',           # Skip local state
    'sync_lyrics.log',      # Skip logs
    'app.log'
}

OUTPUT_FILE = "full_project_code.txt"

def merge_files():
    print(f"🚀 Starting project merge...")
    count = 0
    
    try:
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as outfile:
            outfile.write("--- START OF PROJECT DUMP ---\n")
            
            for root, dirs, files in os.walk("."):
                # 1. Filter Directories
                dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
                
                for file in files:
                    # 2. Filter Files
                    if file in IGNORE_FILES:
                        continue
                        
                    # 3. Filter Extensions
                    _, ext = os.path.splitext(file)
                    if any(file.endswith(x) for x in IGNORE_EXTS):
                        continue
                        
                    path = os.path.join(root, file)
                    
                    # 4. Read and Write
                    try:
                        with open(path, 'r', encoding='utf-8') as infile:
                            content = infile.read()
                            
                            # Add a header for each file so the AI knows where it starts
                            outfile.write(f"\n\n{'='*60}\n")
                            outfile.write(f"FILE: {path}\n")
                            outfile.write(f"{'='*60}\n")
                            outfile.write(content)
                            print(f"  + Added: {path}")
                            count += 1
                    except UnicodeDecodeError:
                        print(f"  ! Skipping binary/non-utf8 file: {path}")
                    except Exception as e:
                        print(f"  ! Error reading {path}: {e}")

            outfile.write("\n\n--- END OF PROJECT DUMP ---\n")
            
        print(f"\n✅ Success! {count} files merged into '{OUTPUT_FILE}'")
        
    except Exception as e:
        print(f"\n❌ Critical Error: {e}")

if __name__ == "__main__":
    merge_files()
    input("\nPress Enter to exit...")