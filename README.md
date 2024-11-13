# SyncLyrics

## Description

A cross-platform application (focused on Windows) that let's you sing along to your favorite songs regardless the player by displaying the lyrics of the currently playing song on your desktop wallpaper, notification pane, terminal or in a locally hosted webpage.

Notes: Forked by me for personal use. It focuses on Windows 10 and Spotify desktop client. I can't guarantee it will work on other OS or other music players.

![Main UI](<screenshots/SyncLyrics Main UI.png>)

_Main UI_

![Minimal Mode](<screenshots/Minimal Mode.png>) 

_Minimal Mode can be accessed by adding ?minimal=true to the URL_

## Features
- Real-time lyrics synchronization
- Multiple display methods
- Customizable appearance
- Minimal mode for embedded view
- Dark/Light theme support
- Cross-platform support (Windows/Linux)

More are being worked on slowly. 

For more information check the SyncLyrics' [website](https://konstantinospetrakis.github.io/SyncLyrics/)! (Konstantinos' original project)

**For linux playerctl is required**

## Run from source
```python
python -m venv venv
source ./venv/bin/activate
pip install -r requirements.txt
python sync_lyrics.py
```

If you don't want to create a virtual environment, you can install requirements and use run.bat to run the script in a hidden window. 

## Build
```python
python build.py 
```

## Known Issues from Original Project
* Tray is partially broken (only default button works) on Linux in the compiled versions. (Works fine when running from source)
* Notification library has an issue on windows, I believe it will be fixed soon, see open issue: 
https://github.com/samschott/desktop-notifier/issues/95

## Special Notes:

- I'm not a dev, just an enthusiast. I had a special niche need for this, so I used AI (Claude) to help me understand the code and modify it to my needs.
- All of the code edits are made by AI, so it's prone to bugs right now. 

The license is the same as the original project.

## Credits: 

* [Konstantinos Petrakis](https://github.com/konstantinospetrakis) for amazing work the original project
