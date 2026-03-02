"""
Audio Recognition Module for SyncLyrics

Provides audio fingerprinting capabilities using ShazamIO for song identification.
Supports Reaper DAW integration and manual audio recognition modes.

LAZY IMPORTS: shazam.py and engine.py are NOT imported at package load time
to avoid loading shazamio when audio recognition is disabled.
Only capture.py (which has no shazamio dependency) is loaded eagerly.
"""

# Eager imports - these have no shazamio/pydub dependencies
from .capture import AudioCaptureManager, AudioChunk

# Lazy imports - only loaded when actually accessed
# This prevents shazamio from loading when just listing audio devices
_lazy_imports = {
    'ShazamRecognizer': '.shazam',
    'RecognitionResult': '.shazam',
    'RecognitionEngine': '.engine',
    'EngineState': '.engine',
}

def __getattr__(name):
    """Lazy import handler for shazam and engine modules."""
    if name in _lazy_imports:
        # Use explicit standard import statements instead of importlib.import_module.
        # PyInstaller's frozen importer is specifically designed to handle `import X.Y`
        # statements. importlib.import_module can fail to set __package__ correctly on
        # loaded modules in frozen EXEs, breaking relative imports inside engine.py
        # (e.g. `from .shazam import ...`). Standard import statements avoid this.
        #
        # Results are cached in globals() so __getattr__ is only called once per name;
        # subsequent accesses find the attribute directly in the module __dict__.
        if name in ('RecognitionEngine', 'EngineState'):
            import audio_recognition.engine as _mod
            globals()['RecognitionEngine'] = _mod.RecognitionEngine
            globals()['EngineState'] = _mod.EngineState
        elif name in ('ShazamRecognizer', 'RecognitionResult'):
            import audio_recognition.shazam as _mod
            globals()['ShazamRecognizer'] = _mod.ShazamRecognizer
            globals()['RecognitionResult'] = _mod.RecognitionResult
        return globals()[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    'AudioCaptureManager',
    'AudioChunk',
    'ShazamRecognizer',
    'RecognitionResult',
    'RecognitionEngine',
    'EngineState',
]
