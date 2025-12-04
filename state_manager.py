from os import path
from typing import Any
import json 
import time
import threading
import os
import uuid

from benedict import benedict

# Allow overriding state file location via environment variable for HAOS persistence
# This ensures state.json is written to /config/state.json instead of /app/state.json
STATE_FILE = os.getenv("SYNCLYRICS_STATE_FILE", "state.json")

DEFAULT_STATE = {
    "theme": "dark",
    "representationMethods": {
        "terminal": False
    },
}

# In-memory cache with TTL to avoid reading from disk constantly
state = None # memory cache for state to avoid reading from disk
state_cache_time = 0
STATE_CACHE_TTL = 2.0  # Cache for 2 seconds to reduce disk I/O

# Thread lock to prevent concurrent writes (cross-platform)
_state_lock = threading.Lock()


def reset_state(): 
    """
    This function resets the state to the default state.
    """

    set_state(DEFAULT_STATE)


def set_state(new_state: dict):
    """
    This function sets the state to the given state.
    Uses file locking and atomic writes to prevent race conditions.

    Args:
        new_state (dict): The new state.
    """

    global state, state_cache_time
    
    # Use lock to prevent concurrent writes
    with _state_lock:
        # FIX: Use unique temp filename to prevent concurrent writes from overwriting each other
        # This provides extra safety even though we have a lock (defense in depth)
        # Temp file must be in the same directory as STATE_FILE for atomic replace to work
        state_dir = os.path.dirname(STATE_FILE) if os.path.dirname(STATE_FILE) else "."
        temp_filename = f"state_{uuid.uuid4().hex}.json.tmp"
        temp_path = os.path.join(state_dir, temp_filename) if state_dir != "." else temp_filename
        try:
            with open(temp_path, "w") as f:
                json.dump(new_state, f, indent=4)
            
            # Atomic replace (works on both Windows and Unix)
            # Ensure target directory exists if using absolute path
            if os.path.dirname(STATE_FILE):
                os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
            
            if path.exists(STATE_FILE):
                os.remove(STATE_FILE)
            os.replace(temp_path, STATE_FILE)
            
            # Update cache immediately
            state = new_state
            state_cache_time = time.time()
        except Exception as e:
            # If write fails, try to clean up temp file
            try:
                if path.exists(temp_path):
                    os.remove(temp_path)
            except:
                pass
            # Re-raise the exception so caller knows it failed
            raise


def get_state() -> dict:
    """
    This function returns the current state.
    Uses caching with TTL to avoid reading from disk constantly.

    Returns:
        dict: The current state.
    """

    global state, state_cache_time
    
    # Check cache first (with TTL)
    current_time = time.time()
    if state is not None and (current_time - state_cache_time) < STATE_CACHE_TTL:
        return state  # Return cached version (still valid)
    
    # Cache expired or doesn't exist, read from disk
    # Use lock to prevent concurrent reads during write
    with _state_lock:
        if not path.exists(STATE_FILE):
            reset_state()
            return state
        
        # Read from disk
        try:
            with open(STATE_FILE, "r") as f:
                state = json.load(f)
                state_cache_time = current_time
                return state
        except Exception as e:
            # If read fails (corrupted file), reset to default
            reset_state()
            return state


def set_attribute_js_notation(state: dict, attribute: str, value: Any) -> dict:
    """
    This function sets the given attribute to the given value in the given state.

    Args:
        state (dict): The state to set the attribute in.
        attribute (str): The attribute to set in js notation.
        value (Any): The value to set the attribute to.

    Returns:
        dict: The state with the attribute set to the value.
    """

    state = benedict(state, keypath_separator=".")
    state[attribute] = value
    return state.dict()


def get_attribute_js_notation(state: dict, attribute: str) -> Any:
    """
    This function returns the value of the given attribute in the given state.

    Args:
        state (dict): The state to get the attribute from.
        attribute (str): The attribute to get in js notation.

    Returns:
        Any: The value of the attribute.
    """

    state = benedict(state, keypath_separator=".")
    return state[attribute]
