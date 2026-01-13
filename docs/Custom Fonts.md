# Custom Fonts

Place your `.ttf` or `.woff2` custom font files in:

`resources/fonts/custom`

## How It Works

1. Add font file (e.g., `MyFont-Regular.woff2`)
2. Restart SyncLyrics
3. Your font appears in Settings → Lyrics Font / UI Font dropdowns

## Supported Formats

- `.woff2` (recommended - smallest size)
- `.woff`
- `.ttf`
- `.otf`

## Where to Get Fonts

- [Google Fonts](https://fonts.google.com) - download TTF files
- [Font Squirrel](https://www.fontsquirrel.com) - free commercial fonts

## Font Names

The font name shown in the dropdown is extracted from the font file's 
internal metadata (the "name table"). This means:

- The filename doesn't matter for display purposes
- Variable fonts like `Rubik-VariableFont_wght.ttf` show as "Rubik"
- If metadata can't be read, the filename is used as fallback

## Notes

- Fonts are scanned once at startup
- Invalid font files are skipped with a warning in the log
- Variable fonts work too; they'll just use default weight.
