# Setting up SyncLyrics (guide for AI assistants)

This file is for an AI assistant **installing and configuring** SyncLyrics for a user. For working on the code, see `CLAUDE.md` and [docs/Development Reference.md](docs/Development%20Reference.md).

SyncLyrics is a self-hosted server that detects what's playing, fetches synced lyrics and serves a web page to any browser (tablet, phone, TV, OBS). It runs in the background and does nothing until music plays. Default ports: **9012** (HTTP) and **9013** (HTTPS, self-signed).

## 1. Ask the user first

1. **Where does the music play?** Spotify (which device?), a Windows or Mac app, Music Assistant / Sonos, a phone, YouTube Music, TIDAL, or "anything" (speakers).
2. **Where should SyncLyrics run?** The same PC as the music, Docker on a server, or the Home Assistant add-on.
3. **Where will they view the lyrics?** Same machine, or a tablet/phone on the network (then they need the server's LAN IP).
4. **Do they have a Spotify developer app?** Optional, but needed for the Spotify API source and Spotify playback controls.
5. **Will SyncLyrics be reachable from the internet?** If yes, it needs a reverse proxy with authentication: SyncLyrics has no login.

## 2. Pick the install method

The key rule: **a source can only see music on the machine SyncLyrics runs on**, except network sources (Spotify API, Music Assistant, Now Playing Input, and the pull sources pointed at another computer).

| Situation | Install | Sources to use |
|---|---|---|
| Music plays on the same Windows PC | Windows exe | Windows Media (SMTC), plus Spotify API / Spicetify for Spotify |
| Music plays on the same Linux or Mac | AppImage / macOS zip | Linux (needs `playerctl`) / macOS (install `nowplaying-cli`) |
| Home Assistant user, music via Music Assistant or Sonos | Home Assistant add-on | Music Assistant |
| Server / NAS, music via Spotify on any device | Docker | Spotify API |
| Music on a phone or a device SyncLyrics can't see | Any (usually Docker / HA) | Now Playing Input (push) |
| YouTube Music desktop / TIDAL desktop | Same PC, or any install pointed at that PC | Pear Desktop / tidal-hifi |
| Vinyl, radio, anything audible | Any machine with a mic or loopback device | Audio Recognition |

Install steps for each method are in [README.md](README.md#-installation). Docker details: [docs/Docker Reference.md](docs/Docker%20Reference.md).

## 3. Configure

- **Precedence:** environment variable > `settings.json` > built-in default. The web UI at `/settings` writes `settings.json`.
- **Any setting as an environment variable:** uppercase the key and replace dots with underscores. `media_source.tidal_hifi.enabled` becomes `MEDIA_SOURCE_TIDAL_HIFI_ENABLED`.
- **Secrets** (Spotify secret, Music Assistant token) go in environment variables or `.env`, not `settings.json`.
- **Where config lives:** exe / source: `.env` next to the app. Docker: `environment:` in compose. HA add-on: the add-on's Configuration tab.

Common settings:

| What | Settings / variables |
|---|---|
| Spotify API | `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`, `SPOTIFY_REDIRECT_URI` |
| Music Assistant | `SYSTEM_MUSIC_ASSISTANT_SERVER_URL`, `SYSTEM_MUSIC_ASSISTANT_TOKEN`, optional `SYSTEM_MUSIC_ASSISTANT_PLAYER_ID` |
| Push from other devices | `MEDIA_SOURCE_NOW_PLAYING_INPUT_ENABLED=true`, optional `MEDIA_SOURCE_NOW_PLAYING_INPUT_TOKEN` |
| Source order | `MEDIA_SOURCE_<NAME>_PRIORITY` (lower = checked first) |
| Update check / stats | `UPDATES_CHECK_ENABLED`, `UPDATES_USAGE_STATS` (both on by default, see [Usage Stats](docs/Usage%20Stats.md)) |

Full list: [docs/Configuration Reference.md](docs/Configuration%20Reference.md). Guides: [Music Assistant](docs/Music%20Assistant.md), [Now Playing Input](docs/Now%20Playing%20Input.md), [Spicetify](docs/Spicetify%20Integration.md), [Audio Recognition](docs/Audio%20Recognition.md).

## 4. Check it works

1. Open `http://<host>:9012`. A fresh install shows a Welcome panel listing what works and what doesn't.
2. `GET http://<host>:9012/health` answers when the server is up.
3. Play a song, then `GET http://<host>:9012/current-track`. `source` shows which source picked it up.
4. The settings Overview page (`/settings`) has the same status rows.
5. Logs: the `logs/` folder in the data directory (next to the app for exe/source, `~/.local/share/synclyrics/logs` for the AppImage, `/data/logs` in Docker; `SYNCLYRICS_LOGS_DIR` overrides it), `docker logs synclyrics`, or the add-on's Log tab.

## 5. Known failure modes

- **Spotify login fails from another device:** Spotify only allows plain `http` redirect URIs for `127.0.0.1`/`localhost`. For any other address use `https://<IP>:9013/callback`, and register exactly the same URI in the Spotify developer dashboard.
- **Docker loses everything on restart:** the `/data` volume isn't mounted. Lyrics, art, settings and state all live there.
- **Linux detects nothing:** `playerctl` isn't installed.
- **macOS only sees Spotify and Apple Music:** install `nowplaying-cli` (`brew install nowplaying-cli`).
- **A local integration takes ~4 s per attempt on Windows:** use `127.0.0.1` instead of `localhost` in source URLs.
- **Lyrics drift with browser players:** browsers barely report position to Windows Media. Prefer a desktop app, Pear Desktop, or Spicetify; or block browsers in `system.windows.app_blocklist`.
- **Spicetify can't connect over the LAN:** Spotify Desktop blocks it; see [Spicetify Integration](docs/Spicetify%20Integration.md).
- **Browser microphone doesn't work:** it needs HTTPS (`https://<IP>:9013`, accept the self-signed certificate).
- **Wrong source wins:** adjust priorities; the first enabled source that's playing wins.
- **Pushed songs don't show:** Now Playing Input is off by default, has the lowest priority, and a device that stops sending for 30 seconds counts as paused.
- **Don't symlink `settings.json`:** saving replaces the file. Use `SYNCLYRICS_SETTINGS_FILE` to move it.

## 6. More

- Everything the web UI can do is available over HTTP: [docs/API Reference.md](docs/API%20Reference.md).
- Common problems: [docs/Troubleshooting.md](docs/Troubleshooting.md) and [docs/FAQ.md](docs/FAQ.md).
