"""
State management system for SyncLyrics
Combines simplicity with reliability while supporting new config system
"""

import json
import logging
from os import path
from typing import Any, Dict, Optional
from benedict import benedict

from config import ROOT_DIR, LYRICS, UI, STORAGE, DEBUG

# Configure logging
logging.basicConfig(
    level=getattr(logging, DEBUG['log_level']),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    filename=DEBUG['log_file'] if DEBUG['enabled'] else None
)
logger = logging.getLogger(__name__)

# Default state configuration
DEFAULT_STATE = {
    "theme": "dark",
    "currentWallpaper": None,
    "representationMethods": {
        "notifications": True,
        "wallpaper": False,
        "terminal": False
    },
    "wallpaperSettings": {
        "fontSize": LYRICS['wallpaper']['font_size_percent'],
        "fontColor": LYRICS['wallpaper']['font_color'],
        "pickColorFromWallpaper": LYRICS['wallpaper']['pick_color_from_wallpaper'],
        "fontFamily": LYRICS['wallpaper']['font_family'],
        "fontStroke": LYRICS['wallpaper']['font_stroke_percent'],
        "xOffset": LYRICS['wallpaper']['x_offset_percent'],
        "yOffset": LYRICS['wallpaper']['y_offset_percent'],
        "width": LYRICS['wallpaper']['width_percent'],
        "height": LYRICS['wallpaper']['height_percent'],
        "quality": LYRICS['wallpaper']['quality'],
        "scaling": LYRICS['wallpaper']['scaling']
    },
    "uiSettings": {
        "themePreset": "default",
        "customColors": UI['themes']['default'],
        "backgroundStyle": "gradient",
        "albumArt": {
            "enabled": False,
            "opacity": 50,
            "blur": 10,
            "extractColors": True
        },
        "animationStyle": "wave"
    }
}

# Memory cache for state
_state_cache = None
_STATE_FILE = ROOT_DIR / "state.json"

def reset_state() -> None:
    """Reset the state to default values"""
    set_state(DEFAULT_STATE)
    logger.info("State reset to defaults")

def set_state(new_state: dict) -> None:
    """
    Set the application state
    
    Args:
        new_state (dict): The new state to set
    """
    global _state_cache
    try:
        # Create backup of current state if it exists
        if _STATE_FILE.exists():
            backup_path = _STATE_FILE.with_suffix('.backup.json')
            with open(_STATE_FILE, 'r') as f:
                current_state = json.load(f)
            with open(backup_path, 'w') as f:
                json.dump(current_state, f, indent=4)
        
        # Write new state
        with open(_STATE_FILE, 'w') as f:
            json.dump(new_state, f, indent=4)
        
        # Update cache
        _state_cache = new_state
        logger.debug("State updated successfully")
        
    except Exception as e:
        logger.error(f"Error setting state: {e}")
        if DEBUG['enabled']:
            raise

def get_state() -> dict:
    """
    Get the current application state
    
    Returns:
        dict: The current state
    """
    global _state_cache
    
    # Return cached state if available
    if _state_cache is not None:
        return _state_cache
    
    try:
        # Create default state if file doesn't exist
        if not _STATE_FILE.exists():
            reset_state()
            return DEFAULT_STATE
        
        # Read state from file
        with open(_STATE_FILE, 'r') as f:
            state = json.load(f)
            
        # Validate and update with any missing default values
        updated = False
        for key, value in DEFAULT_STATE.items():
            if key not in state:
                state[key] = value
                updated = True
                
        # Save if we added any missing values
        if updated:
            set_state(state)
            
        _state_cache = state
        return state
        
    except Exception as e:
        logger.error(f"Error reading state: {e}")
        return DEFAULT_STATE

def set_attribute(state: dict, attribute: str, value: Any) -> dict:
    """
    Set a specific attribute in the state using dot notation
    
    Args:
        state (dict): Current state
        attribute (str): Attribute to set (using dot notation)
        value (Any): Value to set
        
    Returns:
        dict: Updated state
    """
    try:
        state_dict = benedict(state, keypath_separator=".")
        state_dict[attribute] = value
        return state_dict.dict()
    except Exception as e:
        logger.error(f"Error setting attribute {attribute}: {e}")
        return state

def get_attribute(state: dict, attribute: str) -> Any:
    """
    Get a specific attribute from the state using dot notation
    
    Args:
        state (dict): Current state
        attribute (str): Attribute to get (using dot notation)
        
    Returns:
        Any: Value of the attribute
    """
    try:
        state_dict = benedict(state, keypath_separator=".")
        return state_dict[attribute]
    except Exception as e:
        logger.error(f"Error getting attribute {attribute}: {e}")
        return None

def clear_cache() -> None:
    """Clear the state cache to force reload from disk"""
    global _state_cache
    _state_cache = None
    logger.debug("State cache cleared")

# Memory cache for simple values
_value_cache: Dict[str, Any] = {}

def cache_value(key: str, value: Any) -> None:
    """Cache a simple value in memory"""
    _value_cache[key] = value

def get_cached_value(key: str) -> Optional[Any]:
    """Get a cached value from memory"""
    return _value_cache.get(key)

def clear_value_cache() -> None:
    """Clear the simple value cache"""
    _value_cache.clear()