# Custom Fonts

Place your `.ttf` or `.woff2` font files in this folder.

## How It Works

1. Add font file (e.g., `MyFont-Regular.woff2`)
2. Restart SyncLyrics
3. Your font appears in Settings → Lyrics Font / UI Font dropdowns

## Supported Formats

- `.woff2` (recommended - smallest size)
- `.woff`
- `.ttf`
- `.otf`

## Naming Convention

The font name shown in the dropdown is derived from the filename:
- `MyFont-Regular.woff2` → "MyFont"
- `Open_Sans.ttf` → "Open Sans"
- `Roboto-Bold.woff2` → "Roboto"

## Notes

- Fonts are scanned once at startup
- Invalid font files are skipped with a warning in the log
- For variable fonts, use the standard weight (Regular/400)
