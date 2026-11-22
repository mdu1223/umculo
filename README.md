# Umculo

**Your whole music collection in one place, served from your own computer.**

*Umculo* (isiZulu for "music") is a self-hosted music server and web player. It plays and organises the music you own, works with the Subsonic apps you already use on your phone, and brings in the music you've bought or have the right to download from the sources you choose, in the quality you want.

![Umculo on a phone: search, library and the player](docs/screenshots.png)

> **Only download music you own or have the right to.** Umculo is a tool to bring *your* collection together into one all-in-one place. It doesn't come with any music, and it isn't for getting music you haven't paid for. You're responsible for what you download and how you use any source you connect.

## What it does

- **A music server for your apps.** Built-in Subsonic / OpenSubsonic API, so Subsonic apps like Arpeggi and Amperfy work with it. (A Jellyfin-style API is there too, still experimental.)
- **A web player** made for phones and for computers (on a big screen: a sidebar, a docked now-playing panel with the mix video and its songs, and a full player bar with seek and volume): search, library (artists, playlists and DJ mixes), synced lyrics, shuffle and repeat, an editable queue that survives a reload, a sleep timer, keyboard shortcuts, lock-screen controls, a yearly "your listening" recap, light and dark mode.
- **Cook.** Start a mix from any song and it stays in that song's style. *Cook locally* uses your own music; *Just cook* finds songs you don't know yet. With *keep cooking* on, the music never just stops at the end of the queue, and it keeps to the style of what you were playing (a playlist called "Kwaito Classics" keeps cooking kwaito).
- **Likes that matter.** A heart on every song: liked songs become favourites in your music apps, shape For You and come up more in Cook. *Not for me* keeps a song out of every mix and suggestion.
- **Made for you.** Smart mixes built from your own library: Liked songs, On repeat, Fresh in, Rediscover, Never played, and one per style you own.
- **Long DJ mixes and episodes** pick up where you left off, and can play faster.
- **Quality you can see.** Every song shows its format (FLAC, MP3 320…), and a quality profile decides what's good enough. Umculo can replace low-quality copies with better ones.
- **Playlists that stay in sync.** Paste a Spotify or Apple Music playlist link and Umculo builds it from your library and your sources, then keeps it up to date.
- **For You.** Weekly mix, "because you play…", rediscover old favourites, follow artists and get their new releases.
- **DJ mixes and podcasts.** Subscribe to shows, and Umculo can recognise the songs in a mix.
- **What's playing in the mix.** The song finder lists every song in a DJ mix with its time; the player shows the song playing now and jumps to any of them. Works on mixes already in your library too.
- **Radio.** Type an artist, a style, a mood or an era ("Kwaito from the 90s", "chill sunday", "Kabza De Small") and it plays forever: your songs first, new ones mixed in.
- **Gapless and crossfade.** The next song is ready before this one ends; crossfade is a setting (an album or mix always flows straight on).
- **Karaoke and visualiser.** Full-screen synced lyrics that fill as they're sung, or a visualiser that moves with the music.
- **Listen together.** Start a room and share the code: everyone hears the same song at the same moment on their own phone, and anyone can add songs.
- **On this phone.** Save a playlist, album or smart mix to your phone and it plays with no internet (the web app works offline).
- **Play on speakers and TVs.** AirPlay and Chromecast from the player, and any speaker Home Assistant knows (Sonos, Google, Alexa, smart TVs): Umculo plays the queue there song after song. Set `HA_URL`/`HA_TOKEN`, or connect it in the app.
- **Messages on WhatsApp or ntfy.** New mixes from shows you follow, a Sunday note about your week, downloads ready: each person chooses (WhatsApp through [WAHA](https://waha.devlike.pro)).
- **Gigs near you.** Upcoming shows by artists you follow or play a lot, near your city or within a distance, in For You and as a message (from Bandsintown).
- **Alarms and automations.** Wake up to a playlist, smart mix, show or radio on a Home Assistant speaker, getting louder. A hook address lets Home Assistant automations start music ("when I get home").
- **Songs from your mixes.** Every song in your mixes' tracklists that you don't have, in one list: get them all from your sources into a playlist (optional buy links: Settings → Where to look).
- **The inbox.** Drop music you bought (files or zips) into `Music/_inbox` and Umculo files it with tags and covers. Works well with [bandcampsync](https://github.com/meeb/bandcampsync) pointed at the inbox.
- **Mixes for you.** DJ mixes picked from the shows, DJs and styles you download and play (YouTube and Hearthis.at), and a mix search, so you never need YouTube to find sets. Tap to play straight away; Download asks once about subscribing, finding the songs and the video, then keeps playing while it downloads and lists the songs as they're heard. On a computer it looks like a streaming app: a big featured mix, rows you can page through, a page for every DJ or show (their sets, "fans also like", subscribe), Save for later, a right-click menu, and "Pick up where you left off". The seek bar marks every song in the mix; hover to see it, click to jump. Pick the video quality (480p to best) from the player.
- **Watch the mix.** Turn on *Video* while a DJ mix plays and the set's video plays along, in step with the audio. Tick *Also save the video* when you download a mix or subscribe to a show to keep the video too (up to 720p, next to the mix in a hidden `.video` folder).
- **Tidy-up tools.** One folder per artist, duplicates removed, cover art and lyrics added.
- **Family accounts.** Everyone gets their own library, playlists and history. You choose what each person can do, and they can ask you for songs.
- **Safe to put online.** 2-step sign-in, app passwords for music apps, "only devices I approve", sign-in lockout, security headers. See [SECURITY.md](SECURITY.md).
- **Moving in is easy.** Bring stars, plays and playlists over from Navidrome, Plex or Jellyfin; your music files stay where they are.
- **Nightly backups** and **phone notifications** through ntfy.

## Quick start

You need Docker. Then:

```bash
mkdir umculo && cd umculo
curl -O https://raw.githubusercontent.com/mdu1223/umculo/main/docker-compose.yml
# edit docker-compose.yml: point /path/to/your/media at the folder that holds your Music folder
docker compose up -d
```

Open `http://<your-computer>:8095`, create your admin account, and Umculo scans your music. In your phone app, add a Subsonic server with the same address, your username, and an app password (Your account → Devices).

To update: `docker compose pull && docker compose up -d`.

### Building it yourself

```bash
git clone https://github.com/mdu1223/umculo.git && cd umculo
docker build -t umculo .
```

## Download sources (optional)

Umculo plays and organises your own music with no extra setup. To bring in music you have the right to, connect any of these in `docker-compose.yml`, then choose the order under **Settings → Where to look**:

| Source | What you need |
|---|---|
| Soulseek | a running [slskd](https://github.com/slskd/slskd): `SLSKD_URL`, `SLSKD_KEY`, `SLSKD_DOWNLOADS` |
| Usenet | Prowlarr + SABnzbd: `PROWLARR_URL`, `PROWLARR_KEY`, `SABNZBD_URL`, `SABNZBD_KEY` |
| Hearthis.at | nothing; built in. Only songs the uploader offers as a free download (many South African house producers and DJs) |
| YouTube / SoundCloud | nothing; built in through yt-dlp (SoundCloud gives the uploader's original file when they allow downloads) |

## Settings

Most settings live in the app (**Settings**). These are set in `docker-compose.yml`:

| Variable | Default | What it's for |
|---|---|---|
| `PORT` | `8095` | Port inside the container |
| `MUSIC_DIR` | `/data/Music` | Your music |
| `LIBRARY_DIR` | `/data/Music/Umculo` | Where new music goes |
| `PLAYLISTS_DIR` | `/data/Music/_playlists` | Playlist files (`.m3u8`) |
| `FAMILY_DIR` | `/data/Family` | Family members' own folders |
| `MIXES_DIR` | `/data/Music/meta_podcasts` | DJ mixes and podcast episodes |
| `CONFIG_DIR` | `/config` | Accounts, settings, database |
| `BACKUP_DIR` | `/backup` | Nightly backups (`BACKUP_HOUR`, `BACKUP_KEEP`) |
| `PUBLIC_URL` | | Your public address, for artist pictures in apps |
| `QUALITY_PROFILE` | `best` | `best`, `320`, `256` or `128` |
| `STREAM_QUALITY` | `320` | Quality when streaming songs you don't have yet |
| `INTERNET_KBPS` | `320` | Highest bitrate sent to devices outside your home (`0` = no limit) |
| `SOURCES` | `soulseek,usenet,hearthis,soundcloud,youtube` | Where to look when you search |
| `BATCH_SOURCES` | same | Where to look for playlists and upgrades |
| `NTFY_URL` | | Phone notifications |
| `LISTENBRAINZ_USER` | | Better recommendations from your ListenBrainz history |
| `HOST_PATHS` | `/mnt/Storage/Media=/data` | Host-to-container folder mapping, for reading old playlists that use host paths |
| `INBOX_DIR` | `/data/Music/_inbox` | Drop bought songs or zips here to have them filed |
| `VIDEO_QUALITY` | `1080` | Mix videos, for watching and saving: `480`, `720`, `1080`, `1440` or `best` (also in Settings) |
| `COOK_MIN_SECONDS` | `45` | Shortest song a Cook will include (skips intros and skits) |
| `HA_URL`, `HA_TOKEN`, `HA_MEDIA_BASE` | | Home Assistant, for playing on speakers (or set it up in the app); `HA_MEDIA_BASE` is the address speakers use to reach Umculo, e.g. `http://192.168.0.2:8095` |
| `WAHA_URL`, `WAHA_KEY`, `WAHA_SESSION` | | WhatsApp messages through WAHA (or set it up in the app) |
| `FINDER_LISTENERS` | `4` | At most this many song-finder lookups at once (Shazam limits each server to a burst of about 20, then a few a minute; Umculo paces itself) |
| `AUTO_GET_SONGS` | `1` | Get each song the song finder hears (and you don't have) from your sources straight away |
| `MIX_MAX_AGE_DAYS` | `540` | Mixes for you: older sets only show when there aren't enough newer ones |
| `SLSK_OFF_AFTER` | `120` | Seconds slskd may be off Soulseek before Umculo asks it to reconnect |
| `GIGS_APP_ID` | | Your Bandsintown app ID, for gigs near you (Bandsintown no longer answers without one) |
| `TZ` | `Africa/Johannesburg` | Time zone for the weekly note (alarms keep the time zone of the phone that set them) |
| `SUPPORT_GITHUB`, `SUPPORT_KOFI` | project links | Donation links in Settings (blank to hide) |

## Putting it on the internet

Use a VPN (Tailscale/WireGuard) or a tunnel (for example Cloudflare Tunnel). Never forward port 8095 on your router. Then switch on 2-step sign-in, app passwords and "only devices you approve". Details in [SECURITY.md](SECURITY.md).

## Running the tests

The tests use made-up sample music and pretend versions of every outside service:

```bash
pip install mutagen segno shazamio py-opensonic && sudo apt install ffmpeg
bash test/run_api_tests.sh
```

## Support Umculo

Umculo is free and made in South Africa. If it's become your music app, you can support it on [Ko-fi](https://ko-fi.com/mdz10111). Thank you!

## Licence

[GPL-3.0](LICENSE). You can use, change and share Umculo freely; if you share a changed version, share its code under the same licence.
