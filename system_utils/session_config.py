"""
Session-level configuration overrides for Audio Recognition.

These overrides are NOT persisted to settings.json.
They reset when the application restarts.

This module provides session-scoped audio recognition configuration
that allows the UI to enable/configure audio recognition without
modifying the user's persistent settings.
"""

from typing import Any, Dict, Optional
from logging_config import get_logger

logger = get_logger(__name__)


# =============================================================================
# Session Override State
# =============================================================================

# Session overrides (not persisted to settings.json)
# Each key maps to a config value; None means "use settings.json value"
_audio_session_override: Dict[str, Optional[Any]] = {
    "enabled": None,              # True/False/None
    "device_id": None,            # int or None
    "device_name": None,          # str or None
    "mode": None,                 # "backend" | "frontend" | None
    "recognition_interval": None, # float or None
    "capture_duration": None,     # float or None
    "latency_offset": None,       # float or None
    "reaper_auto_detect": None,   # True/False/None
}


# =============================================================================
# Session Override Functions
# =============================================================================

def set_session_override(key: str, value: Any) -> bool:
    """
    Set a session-level override.
    
    Args:
        key: The config key to override
        value: The override value (None to clear)
        
    Returns:
        True if the key was valid and set, False otherwise
    """
    if key in _audio_session_override:
        _audio_session_override[key] = value
        logger.debug(f"Session override set: {key} = {value}")
        return True
    else:
        logger.warning(f"Unknown session override key: {key}")
        return False


def get_session_override(key: str) -> Optional[Any]:
    """
    Get a session-level override value.
    
    Args:
        key: The config key to get
        
    Returns:
        The override value, or None if not set
    """
    return _audio_session_override.get(key)


def clear_session_overrides() -> None:
    """Reset all session overrides to None (use settings.json values)."""
    for key in _audio_session_override:
        _audio_session_override[key] = None
    logger.debug("All session overrides cleared")


def has_session_overrides() -> bool:
    """Check if any session overrides are currently active."""
    return any(v is not None for v in _audio_session_override.values())


def get_active_overrides() -> Dict[str, Any]:
    """Get a dict of only the active (non-None) overrides."""
    return {k: v for k, v in _audio_session_override.items() if v is not None}


# =============================================================================
# Config Merging
# =============================================================================

def get_audio_config_with_overrides() -> Dict[str, Any]:
    """
    Get audio recognition config with session overrides applied.
    
    Priority: session override > settings.json > default
    
    Returns:
        Complete config dict with all values resolved
    """
    # Lazy import to avoid circular dependency
    from config import AUDIO_RECOGNITION
    
    # Start with defaults, then layer settings.json values
    config = {
        "enabled": AUDIO_RECOGNITION.get("enabled", False),
        "device_id": AUDIO_RECOGNITION.get("device_id"),
        "device_name": AUDIO_RECOGNITION.get("device_name", ""),
        "mode": AUDIO_RECOGNITION.get("mode", "backend"),
        "recognition_interval": AUDIO_RECOGNITION.get("recognition_interval", 5.0),
        "capture_duration": AUDIO_RECOGNITION.get("capture_duration", 4.0),
        "latency_offset": AUDIO_RECOGNITION.get("latency_offset", 0.0),
        "reaper_auto_detect": AUDIO_RECOGNITION.get("reaper_auto_detect", False),
    }
    
    # Apply session overrides (only non-None values)
    for key, value in _audio_session_override.items():
        if value is not None:
            config[key] = value
    
    return config


def get_effective_value(key: str, default: Any = None) -> Any:
    """
    Get a single config value with session override applied.
    
    Args:
        key: The config key to get
        default: Default value if not found anywhere
        
    Returns:
        The effective value (session override > settings > default)
    """
    # Check session override first
    override = _audio_session_override.get(key)
    if override is not None:
        return override
    
    # Fall back to settings.json
    from config import AUDIO_RECOGNITION
    return AUDIO_RECOGNITION.get(key, default)
