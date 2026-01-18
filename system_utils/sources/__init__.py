"""
Plugin source auto-discovery and registry.

This module handles:
1. Discovering plugin source files in this directory
2. Validating they implement the required interface
3. Providing a list of enabled plugin sources sorted by priority
4. Merging plugin sources with legacy sources for unified dispatch

Usage in metadata.py:
    from system_utils.sources import get_all_sources_sorted
    
    for source_info in get_all_sources_sorted():
        if source_info["type"] == "legacy":
            # Use existing dispatch logic
        else:
            # Call source_info["instance"].get_metadata()
"""
import importlib
import pkgutil
import platform
from typing import Dict, Type, List, Optional, Any
from .base import BaseMetadataSource, SourceConfig, SourceCapability
from logging_config import get_logger

# =============================================================================
# EXPLICIT IMPORTS FOR PYINSTALLER
# =============================================================================
# PyInstaller's pkgutil.iter_modules() cannot discover modules at runtime in
# frozen apps. These imports ensure the modules are bundled AND discoverable.
# When adding a new source, add an import here.
# =============================================================================
from . import linux      # noqa: F401
from . import macos      # noqa: F401
from . import music_assistant  # noqa: F401

logger = get_logger(__name__)

# Registry of discovered source classes
_registry: Dict[str, Type[BaseMetadataSource]] = {}

# Singleton instances (created on first access)
_instances: Dict[str, BaseMetadataSource] = {}

# Flag to track if discovery has run
_discovered = False


def _discover_sources():
    """
    Auto-discover source modules in this package.
    
    Called lazily on first access to avoid startup overhead
    and circular import issues.
    
    Validation checks:
    - Source must have get_config() method
    - Source must have capabilities() method
    - get_config() must return valid SourceConfig
    - Current platform must be in config.platforms
    """
    global _registry, _discovered
    
    if _discovered:
        return
    
    _discovered = True
    current_platform = platform.system()  # "Windows", "Linux", "Darwin"
    
    for _, name, _ in pkgutil.iter_modules(__path__):
        # Skip non-source files
        if name in ('base', 'enrichment', '__init__'):
            continue
        
        try:
            module = importlib.import_module(f'.{name}', __package__)
            
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                
                # Skip non-classes and the base class itself
                if not isinstance(attr, type):
                    continue
                if not issubclass(attr, BaseMetadataSource):
                    continue
                if attr is BaseMetadataSource:
                    continue
                
                # === VALIDATION: Check required methods exist ===
                if not hasattr(attr, 'get_config') or not callable(getattr(attr, 'get_config')):
                    logger.warning(f"Plugin source {attr_name} missing get_config(), skipping")
                    continue
                
                if not hasattr(attr, 'capabilities') or not callable(getattr(attr, 'capabilities')):
                    logger.warning(f"Plugin source {attr_name} missing capabilities(), skipping")
                    continue
                
                # === VALIDATION: Check config is valid ===
                try:
                    config = attr.get_config()
                    if not isinstance(config, SourceConfig):
                        logger.warning(f"Plugin source {attr_name}: get_config() must return SourceConfig, skipping")
                        continue
                except Exception as e:
                    logger.warning(f"Plugin source {attr_name} has invalid config: {e}, skipping")
                    continue
                
                # === PLATFORM CHECK ===
                # Sources with skip_platform_check=True bypass this (e.g., Linux uses playerctl as gate)
                if not config.skip_platform_check and current_platform not in config.platforms:
                    logger.debug(f"Plugin source {config.name}: not supported on {current_platform}, skipping")
                    continue
                
                # Register the source
                _registry[config.name] = attr
                logger.info(f"Registered plugin source: {config.display_name} ({config.name})")
                
        except Exception as e:
            logger.warning(f"Failed to load plugin source module '{name}': {e}")


def get_source(name: str) -> Optional[BaseMetadataSource]:
    """
    Get a source instance by name.
    
    Creates singleton instance on first access.
    
    Args:
        name: Source name (e.g., "linux", "music_assistant")
        
    Returns:
        Source instance, or None if not found
    """
    _discover_sources()
    
    if name not in _registry:
        return None
    
    if name not in _instances:
        try:
            _instances[name] = _registry[name]()
        except Exception as e:
            logger.error(f"Failed to instantiate plugin source '{name}': {e}")
            return None
    
    return _instances[name]


def get_all_source_classes() -> Dict[str, Type[BaseMetadataSource]]:
    """
    Get all registered source classes.
    
    Useful for introspection and testing.
    
    Returns:
        Dict mapping source name to source class
    """
    _discover_sources()
    return _registry.copy()

# Track sources that have already logged "not available" to prevent spam
_logged_unavailable: set = set()


def get_enabled_plugin_sources() -> List[BaseMetadataSource]:
    """
    Get enabled plugin sources sorted by priority.
    
    Reads enabled/priority from settings.json for each source.
    Checks is_available() to skip sources with missing dependencies.
    
    Returns:
        List of plugin source instances, sorted by priority (lower = first)
    """
    _discover_sources()
    
    enabled = []
    for name, cls in _registry.items():
        instance = get_source(name)
        if instance is None:
            continue
        
        # Check if enabled
        if not instance.enabled:
            continue
        
        # Check if available (platform, dependencies)
        if not instance.is_available():
            # Only log once per source per session to prevent spam
            if name not in _logged_unavailable:
                logger.debug(f"Plugin source {name} not available (not configured or dependencies missing)")
                _logged_unavailable.add(name)
            continue
        
        enabled.append((instance.priority, instance))
    
    # Sort by priority (lower = higher priority)
    enabled.sort(key=lambda x: x[0])
    return [src for _, src in enabled]


def get_all_sources_sorted() -> List[Dict[str, Any]]:
    """
    Get ALL sources (legacy + plugin) merged and sorted by priority.
    
    This is the main function used by metadata.py to build the unified
    dispatch list for full priority mixing.
    
    Legacy sources come from config.MEDIA_SOURCE.
    Plugin sources come from the registry + settings.
    
    Returns:
        List of dicts with keys:
        - name: str (source name)
        - priority: int (lower = checked first)
        - type: "legacy" or "plugin"
        - instance: BaseMetadataSource (only for plugins)
    """
    import config  # Import here to avoid circular imports
    
    _discover_sources()
    
    all_sources = []
    
    # Add legacy sources from config.MEDIA_SOURCE
    for source in config.MEDIA_SOURCE.get("sources", []):
        if not source.get("enabled", False):
            continue
        all_sources.append({
            "name": source["name"],
            "priority": int(source.get("priority", 999)),
            "type": "legacy",
        })
    
    # Add enabled plugin sources
    for plugin in get_enabled_plugin_sources():
        all_sources.append({
            "name": plugin.name,
            "priority": plugin.priority,
            "type": "plugin",
            "instance": plugin,
        })
    
    # Sort by priority (lower = higher priority)
    all_sources.sort(key=lambda x: x["priority"])
    
    return all_sources


# Export key classes for convenience
__all__ = [
    'BaseMetadataSource',
    'SourceConfig', 
    'SourceCapability',
    'get_source',
    'get_all_source_classes',
    'get_enabled_plugin_sources',
    'get_all_sources_sorted',
]
