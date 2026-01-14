#!/bin/bash
# SyncLyrics macOS Launcher
# This script handles Gatekeeper quarantine removal and launches the app

# Get the directory where this script is located
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo "🎵 SyncLyrics Launcher"
echo "======================"

# Check if this is first run (quarantine attribute exists)
if xattr -l SyncLyrics 2>/dev/null | grep -q "com.apple.quarantine"; then
    echo "📋 First run detected - removing macOS quarantine..."
    
    # Remove quarantine from all files
    xattr -cr . 2>/dev/null
    
    if [ $? -eq 0 ]; then
        echo "✅ Quarantine removed successfully"
    else
        echo "⚠️  Could not remove quarantine automatically."
        echo ""
        echo "Please run this command in Terminal:"
        echo "  xattr -cr \"$SCRIPT_DIR\""
        echo ""
        echo "Or go to System Preferences → Privacy & Security → Open Anyway"
        read -p "Press Enter to continue anyway..."
    fi
else
    echo "✅ App is already trusted"
fi

# Make executable if needed
if [ ! -x SyncLyrics ]; then
    chmod +x SyncLyrics
    echo "✅ Made SyncLyrics executable"
fi

# Launch the app
echo ""
echo "🚀 Starting SyncLyrics..."
echo "   Web UI: http://localhost:9012"
echo ""
echo "Press Ctrl+C to stop"
echo ""

./SyncLyrics
