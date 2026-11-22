"""Pinpeat ពិណពាទ្យ: a self-hosted Discord music bot that plays YouTube audio in a voice channel.

- Streams (never downloads), so 1 h or 5 h videos start at once and use little disk.
- Signs in to YouTube with your own cookies (Premium account): better audio, age-locked
  and members-only videos, fewer "sign in to confirm you're not a bot" blocks.
- Commands start with "!" (see !help).

Run:  DISCORD_TOKEN=... uv run bot.py      (settings: see .env.example)
"""

# /// script
# requires-python = ">=3.10"
# dependencies = ["discord.py[voice]>=2.7", "davey>=0.1,<0.2", "yt-dlp>=2026.10", "python-dotenv"]
# ///

import asyncio
import os
import re
import subprocess
import sys
import time
from collections import deque
from dataclasses import dataclass, field

import discord
import yt_dlp
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.environ.get("DISCORD_TOKEN", "")
PREFIX = os.environ.get("PREFIX", "!")
COOKIES = os.environ.get("YT_COOKIES", "")            # path to cookies.txt exported from your browser
BROWSER = os.environ.get("YT_BROWSER", "")            # or read cookies straight from a browser: chrome, firefox, safari, edge, brave
IDLE_LEAVE_S = int(os.environ.get("IDLE_LEAVE_S", "300"))
MAX_PLAYLIST = int(os.environ.get("MAX_PLAYLIST", "200"))
# View Channel, Send Messages, Add Reactions, Read Message History, Connect, Speak
INVITE_PERMS = 1024 | 2048 | 64 | 65536 | (1 << 20) | (1 << 21)
ANNOUNCE = os.environ.get("ANNOUNCE", "1") == "1"   # say "online" in each server when the bot starts
ANNOUNCE_CHANNEL = os.environ.get("ANNOUNCE_CHANNEL", "general")
# YouTube "player clients" tried in order for the stream link. tv_simply gives links FFmpeg can open from
# cloud servers (others answer 403 there); "default" = yt-dlp's own choice.
YT_CLIENTS = [c.strip() for c in os.environ.get("YT_CLIENTS", "tv_simply,default").split(",") if c.strip()]
FORMAT = "141/bestaudio[acodec=opus]/bestaudio/best"
# high = YouTube's best audio (Opus ~160 kbps) through yt-dlp at the start of a song; seeking uses the direct link.
# normal = always the direct link (on cloud servers that is ~95 kbps AAC from the tv_simply client).
QUALITY = os.environ.get("QUALITY", "high")
ALLOWED_GUILDS = {int(g) for g in os.environ.get("ALLOWED_GUILDS", "").split(",") if g.strip()}


def ydl_opts(**extra) -> dict:
    o = {
        # 141 = 256 kbps AAC, served only to Premium accounts; else the best audio there is
        "format": FORMAT,
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "default_search": "ytsearch",
        "source_address": "0.0.0.0",
    }
    if COOKIES:
        o["cookiefile"] = COOKIES
    elif BROWSER:
        o["cookiesfrombrowser"] = (BROWSER,)
    o.update(extra)
    return o


def fmt_time(s: float | None) -> str:
    if not s:
        return "live" if s == 0 else "?"
    s = int(s)
    h, m, sec = s // 3600, s % 3600 // 60, s % 60
    return f"{h}:{m:02}:{sec:02}" if h else f"{m}:{sec:02}"


def parse_time(t: str) -> int:
    """'1:23:45', '83:00', '90', '1h20m', '45s' → seconds"""
    t = t.strip().lower()
    if re.fullmatch(r"[\d:]+", t):
        sec = 0
        for part in t.split(":"):
            sec = sec * 60 + int(part or 0)
        return sec
    m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", t)
    if not m or not any(m.groups()):
        raise ValueError(t)
    h, mi, s = (int(x or 0) for x in m.groups())
    return h * 3600 + mi * 60 + s


@dataclass
class Track:
    url: str            # the YouTube page link (the stream link is fetched just before playing: those expire after ~6 h)
    title: str
    duration: float | None
    who: str
    bad: set = field(default_factory=set)  # play modes that failed for this track ("hq" pipe, "direct" link)


@dataclass
class Player:
    queue: deque = field(default_factory=deque)
    now: Track | None = None
    started: float = 0.0     # wall time when the current track (re)started
    offset: int = 0          # seconds into the track where it (re)started
    paused_at: float = 0.0
    volume: float = 1.0
    mode: str = ""           # how the current track is playing: "hq" or "direct"
    loop: bool = False
    gen: int = 0             # bumps on every play/seek so an old track's end can't skip the new one
    text: discord.abc.Messageable | None = None
    idle_task: asyncio.Task | None = None
    proc: subprocess.Popen | None = None

    def position(self) -> int:
        if not self.now:
            return 0
        end = self.paused_at or time.time()
        return int(self.offset + end - self.started)


def resolve(query: str) -> list[dict]:
    """link, playlist link or search words → list of {url, title, duration}"""
    is_list = "list=" in query and "youtube" in query
    with yt_dlp.YoutubeDL(ydl_opts(noplaylist=not is_list, extract_flat="in_playlist", playlistend=MAX_PLAYLIST)) as y:
        info = y.extract_info(query, download=False)
    entries = info.get("entries") if info else None
    items = list(entries) if entries is not None else [info]
    out = []
    for e in items:
        if not e:
            continue
        url = e.get("webpage_url") or e.get("url") or ""
        if url and not url.startswith("http"):
            url = f"https://www.youtube.com/watch?v={url}"
        out.append({"url": url, "title": e.get("title") or url, "duration": e.get("duration")})
    return out


def stream_url(page_url: str) -> tuple[str, dict]:
    last: Exception | None = None
    for c in YT_CLIENTS:
        extra = {} if c == "default" else {"extractor_args": {"youtube": {"player_client": [c]}}}
        try:
            with yt_dlp.YoutubeDL(ydl_opts(**extra)) as y:
                info = y.extract_info(page_url, download=False)
            return info["url"], info.get("http_headers") or {}
        except Exception as e:  # noqa: BLE001
            last = e
    raise last or RuntimeError("no stream")


def pipe_source(page_url: str, seek: int, volume: float):
    """best audio: yt-dlp downloads (its own chunked downloader works where FFmpeg gets 403) and pipes into FFmpeg"""
    cmd = [sys.executable, "-m", "yt_dlp", "-q", "--no-warnings", "-f", FORMAT, "-o", "-", page_url]
    if COOKIES:
        cmd[3:3] = ["--cookies", COOKIES]
    elif BROWSER:
        cmd[3:3] = ["--cookies-from-browser", BROWSER]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    before = "-nostdin" + (f" -ss {seek}" if seek else "")
    src = discord.FFmpegPCMAudio(proc.stdout, pipe=True, before_options=before, options="-vn")
    return discord.PCMVolumeTransformer(src, volume), proc


intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix=PREFIX, intents=intents, help_command=None)
players: dict[int, Player] = {}
announced = False


async def _sync_slash():
    n = await bot.tree.sync()      # makes /play, /skip ... show up with suggestions when people type "/"
    print(f"slash commands synced: {len(n)}")

bot.setup_hook = _sync_slash


def player_for(guild: discord.Guild) -> Player:
    return players.setdefault(guild.id, Player())


@bot.check
async def only_allowed(ctx: commands.Context) -> bool:
    return ctx.guild is not None and (not ALLOWED_GUILDS or ctx.guild.id in ALLOWED_GUILDS)


def kill_proc(p: Player) -> None:
    if p.proc and p.proc.poll() is None:
        p.proc.kill()
    p.proc = None


async def play_next(guild: discord.Guild, seek: int = 0, replay: bool = False) -> None:
    p = player_for(guild)
    vc = guild.voice_client
    if not vc or not vc.is_connected():
        return
    kill_proc(p)
    if seek == 0 and not replay:
        if p.loop and p.now:
            pass                      # play the same track again
        elif p.queue:
            p.now = p.queue.popleft()
        else:
            p.now = None
            start_idle(guild)
            return
    track = p.now
    order = ["hq", "direct"] if seek == 0 and QUALITY == "high" else ["direct", "hq"]
    left = [m for m in order if m not in track.bad]
    if not left:
        if p.text:
            await p.text.send(f"Can't play **{track.title}**: YouTube refused every way. Skipping.")
        p.now = None
        return await play_next(guild)
    p.mode = left[0]
    if p.mode == "hq":
        src, p.proc = await asyncio.to_thread(pipe_source, track.url, seek, p.volume)
        return _start(guild, p, track, src, seek, replay)
    try:
        url, headers = await asyncio.to_thread(stream_url, track.url)
    except Exception as e:  # noqa: BLE001
        if p.text:
            await p.text.send(f"Can't play **{track.title}**: {short_err(e)}")
        p.now = None
        return await play_next(guild)

    hdr = "".join(f"{k}: {v}\r\n" for k, v in headers.items())
    before = "-reconnect 1 -reconnect_streamed 1 -reconnect_on_network_error 1 -reconnect_delay_max 10 -nostdin"
    if hdr:
        before += f' -headers "{hdr}"'
    if seek:
        before += f" -ss {seek}"
    src = discord.PCMVolumeTransformer(discord.FFmpegPCMAudio(url, before_options=before, options="-vn"), p.volume)
    _start(guild, p, track, src, seek, replay)


def _start(guild: discord.Guild, p: Player, track: Track, src, seek: int, replay: bool) -> None:
    vc = guild.voice_client
    if not vc or not vc.is_connected():
        return
    p.gen += 1
    my_gen = p.gen

    def after(err: Exception | None) -> None:
        if my_gen != p.gen:      # stopped on purpose (seek): a newer play already took over
            return
        if err:
            print("player error:", err)
        ran = time.time() - p.started
        if ran < 5 and (track.duration or 99) > 10:
            # the stream died at once (e.g. YouTube 403 on this server): try the other way
            print(f"{p.mode} stream failed after {ran:.1f}s, trying the other way: {track.title}")
            track.bad.add(p.mode)
            asyncio.run_coroutine_threadsafe(play_next(guild, seek=p.offset, replay=True), bot.loop)
            return
        asyncio.run_coroutine_threadsafe(play_next(guild), bot.loop)

    p.offset, p.started, p.paused_at = seek, time.time(), 0.0
    cancel_idle(p)
    vc.play(src, after=after)
    if p.text and seek == 0 and not replay:
        bot.loop.create_task(p.text.send(f"▶️ **{track.title}** `{fmt_time(track.duration)}` (asked by {track.who})"))


def short_err(e: Exception) -> str:
    msg = str(e).replace("ERROR: ", "")
    if "Sign in" in msg or "confirm you" in msg:
        msg += "\n→ YouTube wants a login: set YT_COOKIES or YT_BROWSER (see README)."
    return msg[:400]


def cancel_idle(p: Player) -> None:
    if p.idle_task and not p.idle_task.done():
        p.idle_task.cancel()
    p.idle_task = None


def start_idle(guild: discord.Guild) -> None:
    p = player_for(guild)
    cancel_idle(p)

    async def leave_later():
        await asyncio.sleep(IDLE_LEAVE_S)
        vc = guild.voice_client
        if vc and not vc.is_playing() and not vc.is_paused():
            await vc.disconnect()
            if p.text:
                await p.text.send("👋 Left the voice channel (nothing playing).")

    p.idle_task = bot.loop.create_task(leave_later())


async def join(ctx: commands.Context) -> discord.VoiceClient | None:
    if not ctx.author.voice or not ctx.author.voice.channel:
        await ctx.send("Join a voice channel first.")
        return None
    ch = ctx.author.voice.channel
    vc = ctx.voice_client
    if vc is None:
        try:
            vc = await ch.connect(self_deaf=True, timeout=20)
        except asyncio.TimeoutError:
            if ctx.voice_client:
                await ctx.voice_client.disconnect(force=True)
            await ctx.send("⚠️ Can't reach Discord voice: the bot's internet blocks UDP (voice needs it). Ask the owner to move the bot.")
            return None
    elif vc.channel != ch:
        await vc.move_to(ch)
    return vc


@bot.event
async def on_ready():
    login = f"cookies file {COOKIES}" if COOKIES else f"{BROWSER} browser cookies" if BROWSER else "NOT signed in"
    await bot.change_presence(activity=discord.Activity(type=discord.ActivityType.listening, name="/play"))
    print(f"Pinpeat logged in as {bot.user} · YouTube: {login}")
    print(f"Invite link: https://discord.com/oauth2/authorize?client_id={bot.user.id}&scope=bot&permissions={INVITE_PERMS}")
    global announced
    if ANNOUNCE and not announced:          # on_ready also fires on reconnects: say it once per start
        announced = True
        for g in bot.guilds:
            if ALLOWED_GUILDS and g.id not in ALLOWED_GUILDS:
                continue
            ch = discord.utils.get(g.text_channels, name=ANNOUNCE_CHANNEL)
            if not ch or not ch.permissions_for(g.me).send_messages:
                ch = g.system_channel if g.system_channel and g.system_channel.permissions_for(g.me).send_messages else None
            if ch:
                try:
                    await ch.send("🟢 **Pinpeat is online!** Join a voice channel and type `/play` "
                                  "(Discord suggests songs as you type) or `!play <link>`. `/help` for all commands.")
                except discord.HTTPException:
                    pass


async def ack(ctx: commands.Context, emoji: str, text: str) -> None:
    """a reaction for !commands, a short reply for /commands (those must answer)"""
    if ctx.interaction:
        await ctx.send(f"{emoji} {text}")
    else:
        await ctx.message.add_reaction(emoji)


_ac_cache: dict[str, list[app_commands.Choice[str]]] = {}


def _search5(q: str) -> list[dict]:
    with yt_dlp.YoutubeDL(ydl_opts(extract_flat=True, noplaylist=True)) as y:
        info = y.extract_info(f"ytsearch5:{q}", download=False)
    return [e for e in (info.get("entries") or []) if e]


async def play_suggest(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    """while typing /play: the top 5 YouTube results (title + length); picking one plays that video"""
    q = current.strip()
    if len(q) < 3 or q.startswith("http"):
        return []
    if q in _ac_cache:
        return _ac_cache[q]
    try:
        found = await asyncio.wait_for(asyncio.to_thread(_search5, q), timeout=2.5)
    except Exception:  # noqa: BLE001  (too slow or failed: no suggestions this time)
        return []
    out = []
    for e in found:
        url = f"https://www.youtube.com/watch?v={e['id']}" if e.get("id") else e.get("url")
        if not url:
            continue
        name = f"{fmt_time(e.get('duration'))} · {e.get('title') or url}"
        out.append(app_commands.Choice(name=name[:100], value=url))
    _ac_cache[q] = out
    if len(_ac_cache) > 500:
        _ac_cache.clear()
    return out


@bot.event
async def on_command_error(ctx: commands.Context, err: Exception):
    if isinstance(err, commands.HybridCommandError):
        err = err.original
    if isinstance(err, (commands.CommandNotFound, commands.CheckFailure)):
        return
    if isinstance(err, commands.MissingRequiredArgument):
        return await ctx.send(f"Missing `{err.param.name}`. Try `{PREFIX}help`.")
    await ctx.send(f"Error: {short_err(getattr(err, 'original', err))}")


@bot.hybrid_command(aliases=["p"], description="Play a YouTube link, playlist, or search words")
@app_commands.describe(query="YouTube link or song name (pick a suggestion)")
@app_commands.autocomplete(query=play_suggest)
async def play(ctx: commands.Context, *, query: str):
    """!play <YouTube link | playlist link | search words>"""
    await ctx.defer()
    vc = await join(ctx)
    if not vc:
        return
    p = player_for(ctx.guild)
    p.text = ctx.channel
    async with ctx.typing():
        items = await asyncio.to_thread(resolve, query)
    if not items:
        return await ctx.send("Nothing found.")
    for it in items:
        p.queue.append(Track(it["url"], it["title"], it["duration"], ctx.author.display_name))
    if len(items) > 1:
        total = sum(i["duration"] or 0 for i in items)
        await ctx.send(f"➕ Added {len(items)} tracks ({fmt_time(total)}).")
    elif vc.is_playing() or vc.is_paused():
        await ctx.send(f"➕ Queued **{items[0]['title']}** `{fmt_time(items[0]['duration'])}` (#{len(p.queue)}).")
    if not vc.is_playing() and not vc.is_paused():
        if ctx.interaction and len(items) == 1:
            await ctx.send(f"🔎 Found **{items[0]['title']}**")
        await play_next(ctx.guild)


@bot.hybrid_command(description="Jump inside the song: 1:23:45, 2h10m, +10m, -30s")
@app_commands.describe(to="Time like 1:23:45, 2h10m, +10m or -30s")
async def seek(ctx: commands.Context, *, to: str):
    """!seek 1:23:45  |  !seek 2h10m  |  !seek +10m  |  !seek -30s"""
    p, vc = player_for(ctx.guild), ctx.voice_client
    if not vc or not p.now:
        return await ctx.send("Nothing playing.")
    try:
        sec = parse_time(to.lstrip("+-"))
    except ValueError:
        return await ctx.send("Use a time like `1:23:45`, `2h10m`, `+10m` or `-30s`.")
    if to.startswith("+"):
        sec = p.position() + sec
    elif to.startswith("-"):
        sec = p.position() - sec
    if p.now.duration:
        sec = min(sec, int(p.now.duration) - 1)
    sec = max(0, sec)
    await ctx.defer()
    p.gen += 1                    # the stopped track's after() is now ignored
    vc.stop()
    await play_next(ctx.guild, seek=sec, replay=True)
    await ctx.send(f"⏩ {fmt_time(sec) if sec else '0:00'} / {fmt_time(p.now.duration)}")


@bot.hybrid_command(aliases=["np"], description="What is playing now")
async def now(ctx: commands.Context):
    p = player_for(ctx.guild)
    if not p.now:
        return await ctx.send("Nothing playing.")
    pos, dur = p.position(), p.now.duration
    bar = ""
    if dur:
        n = int(20 * pos / dur)
        bar = "▬" * n + "🔘" + "▬" * (19 - n) + "\n"
    await ctx.send(f"🎵 **{p.now.title}**\n{bar}`{fmt_time(pos) if pos else '0:00'} / {fmt_time(dur)}` {p.now.url}")


@bot.hybrid_command(aliases=["q"], description="Show the queue")
async def queue(ctx: commands.Context):
    p = player_for(ctx.guild)
    if not p.queue:
        return await ctx.send("Queue is empty.")
    lines = [f"`{i}.` {t.title} `{fmt_time(t.duration)}`" for i, t in enumerate(list(p.queue)[:15], 1)]
    more = f"\n…and {len(p.queue) - 15} more" if len(p.queue) > 15 else ""
    total = sum(t.duration or 0 for t in p.queue)
    await ctx.send("\n".join(lines) + more + f"\nTotal: {fmt_time(total)}")


@bot.hybrid_command(aliases=["s", "next"], description="Skip to the next song")
async def skip(ctx: commands.Context):
    vc = ctx.voice_client
    if vc and (vc.is_playing() or vc.is_paused()):
        p = player_for(ctx.guild)
        p.loop = False
        p.gen += 1                    # so the stopped song's after() doesn't treat this as a failure
        vc.stop()
        bot.loop.create_task(play_next(ctx.guild))
        await ack(ctx, "⏭️", "Skipped")
    elif ctx.interaction:
        await ctx.send("Nothing playing.")


@bot.hybrid_command(description="Pause")
async def pause(ctx: commands.Context):
    vc, p = ctx.voice_client, player_for(ctx.guild)
    if vc and vc.is_playing():
        vc.pause()
        p.paused_at = time.time()
        await ack(ctx, "⏸️", "Paused")
    elif ctx.interaction:
        await ctx.send("Nothing to do.")


@bot.hybrid_command(aliases=["unpause"], description="Resume")
async def resume(ctx: commands.Context):
    vc, p = ctx.voice_client, player_for(ctx.guild)
    if vc and vc.is_paused():
        p.started += time.time() - p.paused_at
        p.paused_at = 0.0
        vc.resume()
        await ack(ctx, "▶️", "Resumed")
    elif ctx.interaction:
        await ctx.send("Nothing to do.")


@bot.hybrid_command(aliases=["vol"], description="Volume 0-150")
@app_commands.describe(level="0 to 150 (100 = normal)")
async def volume(ctx: commands.Context, level: int):
    """!volume 0-150"""
    p, vc = player_for(ctx.guild), ctx.voice_client
    p.volume = max(0, min(level, 150)) / 100
    if vc and isinstance(vc.source, discord.PCMVolumeTransformer):
        vc.source.volume = p.volume
    await ctx.send(f"🔊 {int(p.volume * 100)}%")


@bot.hybrid_command(description="Repeat the current song on/off")
async def loop(ctx: commands.Context):
    p = player_for(ctx.guild)
    p.loop = not p.loop
    await ctx.send(f"🔁 Loop {'on' if p.loop else 'off'}")


@bot.hybrid_command(description="Remove a song from the queue by its number")
async def remove(ctx: commands.Context, n: int):
    p = player_for(ctx.guild)
    if not 1 <= n <= len(p.queue):
        return await ctx.send("No such number in the queue.")
    t = p.queue[n - 1]
    del p.queue[n - 1]
    await ctx.send(f"🗑️ Removed **{t.title}**")


@bot.hybrid_command(description="Empty the queue")
async def clear(ctx: commands.Context):
    player_for(ctx.guild).queue.clear()
    await ctx.send("🧹 Queue cleared.")


@bot.hybrid_command(aliases=["leave", "dc"], description="Stop and leave the voice channel")
async def stop(ctx: commands.Context):
    p = player_for(ctx.guild)
    p.queue.clear()
    p.now, p.loop = None, False
    p.gen += 1
    cancel_idle(p)
    kill_proc(p)
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
    await ack(ctx, "⏹️", "Stopped")


@bot.hybrid_command(description="Check the YouTube sign-in")
async def login(ctx: commands.Context):
    """Shows whether the bot is signed in to YouTube (never shows the cookies)."""
    if not (COOKIES or BROWSER):
        return await ctx.send("❌ Not signed in to YouTube. Set `YT_COOKIES` or `YT_BROWSER` (see README).")
    src = f"cookies file `{os.path.basename(COOKIES)}`" if COOKIES else f"`{BROWSER}` browser cookies"

    def check():
        with yt_dlp.YoutubeDL(ydl_opts(extract_flat=True, playlistend=1)) as y:
            # the Liked-videos list only opens for a signed-in account
            y.extract_info("https://www.youtube.com/playlist?list=LL", download=False)

    await ctx.defer()
    try:
        await asyncio.to_thread(check)
        await ctx.send(f"✅ Signed in to YouTube with {src}.")
    except Exception as e:  # noqa: BLE001
        await ctx.send(f"⚠️ Cookies set ({src}) but YouTube did not accept them: {short_err(e)[:200]}\n"
                       "Export fresh cookies (README step 4).")


@bot.hybrid_command(name="help", description="All Pinpeat commands")
async def help_(ctx: commands.Context):
    c = PREFIX
    await ctx.send(
        f"**Pinpeat ពិណពាទ្យ** · YouTube music · type `/` to see every command with hints\n"
        f"`{c}play <link|playlist|words>` play or add to queue (alias `{c}p`)\n"
        f"`{c}seek 1:23:45` · `{c}seek +10m` · `{c}seek -30s` jump inside long videos\n"
        f"`{c}np` now playing · `{c}queue` · `{c}skip` · `{c}pause` · `{c}resume`\n"
        f"`{c}volume 0-150` · `{c}loop` · `{c}remove <n>` · `{c}clear` · `{c}stop`\n"
        f"`{c}login` check the YouTube sign-in"
    )


if __name__ == "__main__":
    if os.environ.get("DEBUG_VOICE") == "1":   # voice handshake + encryption (DAVE) details in the log
        import logging
        logging.basicConfig(level=logging.INFO)
        for name in ("discord.gateway", "discord.voice_state", "discord.player"):
            logging.getLogger(name).setLevel(logging.DEBUG)
    if not TOKEN:
        raise SystemExit("Set DISCORD_TOKEN (in .env or the shell). See README.")
    bot.run(TOKEN)
