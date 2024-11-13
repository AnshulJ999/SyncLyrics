"""
Graphics handling system for SyncLyrics
Manages wallpaper generation and text rendering
"""

import textwrap
import logging
from PIL import Image, ImageDraw, ImageFont, ImageColor
from PIL.ImageFont import FreeTypeFont
import colorsys

from system_utils import get_current_wallpaper, set_wallpaper, get_font_path
from state_manager import get_state
from config import LYRICS, SYSTEM, DEBUG

# Configure logging
logging.basicConfig(level=getattr(logging, DEBUG['log_level']))
logger = logging.getLogger(__name__)

def _get_dominant_color(image: Image.Image, palette_size: int = 16) -> tuple:
    """
    Get dominant color from image using k-means clustering
    
    Args:
        image (Image.Image): Image to analyze
        palette_size (int): Number of colors to extract
        
    Returns:
        tuple: RGB color values
    """
    try:
        # Resize image for faster processing
        img = image.copy()
        img.thumbnail((100, 100))

        # Reduce colors using k-means internally
        paletted = img.convert('P', palette=Image.ADAPTIVE, colors=palette_size)

        # Find most common color
        palette = paletted.getpalette()
        color_counts = sorted(paletted.getcolors(), reverse=True)
        palette_index = color_counts[0][1]
        dominant_color = tuple(palette[palette_index*3:palette_index*3+3])

        # Adjust color brightness if needed
        h, l, s = colorsys.rgb_to_hls(*[x/255.0 for x in dominant_color])
        if l > 0.8:  # Too bright
            l = 0.8
        elif l < 0.2:  # Too dark
            l = 0.2
        adjusted_color = tuple(int(x * 255) for x in colorsys.hls_to_rgb(h, l, s))

        return adjusted_color
        
    except Exception as e:
        logger.error(f"Error getting dominant color: {e}")
        return (255, 255, 255)  # Default to white

def _find_words_per_line(font: FreeTypeFont, text: str, max_width: int) -> list[str]:
    """
    Split text into lines that fit within width
    
    Args:
        font (FreeTypeFont): Font to use for text
        text (str): Text to split
        max_width (int): Maximum width in pixels
        
    Returns:
        list[str]: Lines of text that fit
    """
    if not text:
        return []
        
    max_chars = len(text)
    while font.getlength(text[:max_chars]) > max_width and max_chars > 0:
        max_chars -= 1
        
    return textwrap.wrap(text, max_chars)

def render_text(text: str, font_size: int = 50, 
                font_family: str = None, color: tuple = (0, 0, 0),
                stroke_width: int = 0, stroke_color: tuple = (0, 0, 0),
                width: float = 1.0, height: float = 1.0) -> Image.Image:
    """
    Render text on transparent background
    
    Args:
        text (str): Text to render
        font_size (int): Font size in pixels
        font_family (str): Font family path
        color (tuple): Text color RGB
        stroke_width (int): Text outline width
        stroke_color (tuple): Outline color RGB
        width (float): Width as fraction of background
        height (float): Height as fraction of background
        
    Returns:
        Image.Image: Rendered text image
    """
    try:
        # Get font
        font_path = font_family or get_font_path("Arial")
        font = ImageFont.truetype(font_path, font_size, encoding="unic")

        # Create image
        im_width = int(background.width * width)
        im_height = int(background.height * height)
        im = Image.new('RGBA', (im_width, im_height))
        draw = ImageDraw.Draw(im)

        # Calculate text positioning
        text_height = font.getsize(text)[1]
        y = 0

        # Draw text lines
        for line in _find_words_per_line(font, text, im_width):
            line_width = font.getlength(line)
            x = int((im_width - line_width) * 0.5)  # Center text
            draw.text((x, y), line, font=font, fill=color, 
                     stroke_width=stroke_width, stroke_fill=stroke_color)
            y += text_height

        return im
        
    except Exception as e:
        logger.error(f"Error rendering text: {e}")
        return Image.new('RGBA', (1, 1))  # Return empty image on error

def render_text_with_background(text: str):
    """
    Render text on wallpaper and set as background
    
    Args:
        text (str): Text to render
    """
    try:
        # Get clean copy of background
        clean_background = background.copy()

        # Get font color
        font_color = FONT_COLOR
        if PICK_COLOR:
            font_color = _get_dominant_color(background)
            
        # Render text
        front = render_text(
            text=text,
            font_size=FONT_SIZE,
            font_family=FONT_FAMILY,
            color=font_color,
            stroke_width=FONT_STROKE,
            stroke_color=STROKE_COLOR,
            width=WIDTH,
            height=HEIGHT
        )

        # Calculate position
        x = int((clean_background.width - front.width) * X_OFFSET)
        y = int(clean_background.height * Y_OFFSET)

        # Paste text on background
        clean_background.paste(front, (x, y), front)

        # Save and set wallpaper
        save_path = SYSTEM['windows']['wallpaper']['save_path']
        clean_background.save(save_path, "JPEG", 
                            quality=QUALITY, optimize=True)
        set_wallpaper(save_path)
        
    except Exception as e:
        logger.error(f"Error rendering wallpaper: {e}")

def restore_wallpaper():
    """Restore original wallpaper"""
    try:
        original_wallpaper = get_state().get('currentWallpaper')
        if original_wallpaper:
            set_wallpaper(original_wallpaper)
    except Exception as e:
        logger.error(f"Error restoring wallpaper: {e}")

# Load settings and initialize
SETTINGS = get_state()["wallpaperSettings"]

# Font settings
FONT_PERCENT = SETTINGS["fontSize"]
FONT_COLOR = SETTINGS["fontColor"]
PICK_COLOR = SETTINGS["pickColorFromWallpaper"]
FONT_FAMILY = get_font_path(SETTINGS["fontFamily"])
FONT_STROKE_PERCENT = SETTINGS["fontStroke"]

# Position settings
X_OFFSET = SETTINGS["xOffset"] / 100
Y_OFFSET = SETTINGS["yOffset"] / 100
WIDTH = SETTINGS["width"] / 100
HEIGHT = SETTINGS["height"] / 100

# Image settings
QUALITY = SETTINGS["quality"]
SCALING = SETTINGS["scaling"]

# Get background and calculate derived settings
background = get_current_wallpaper()
if SCALING != 100:
    new_size = (
        int(background.width * SCALING / 100),
        int(background.height * SCALING / 100)
    )
    background = background.resize(new_size, Image.LANCZOS)

# Convert colors
if FONT_COLOR.startswith("#"):
    FONT_COLOR = ImageColor.getrgb(FONT_COLOR)
if PICK_COLOR:
    FONT_COLOR = _get_dominant_color(background)

# Calculate font size and stroke
FONT_SIZE = int(background.width * FONT_PERCENT / 100)
FONT_STROKE = int(FONT_SIZE * FONT_STROKE_PERCENT / 100)

# Choose stroke color based on font color brightness
color_brightness = (0.299 * FONT_COLOR[0] + 
                   0.587 * FONT_COLOR[1] + 
                   0.114 * FONT_COLOR[2])
STROKE_COLOR = (0, 0, 0) if color_brightness > 128 else (255, 255, 255)