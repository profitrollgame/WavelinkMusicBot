import random
import re

import requests
import discord
from pycord.wavelink.ext import spotify
from pycord import wavelink
import json

from enum import Enum


class Sites(Enum):
    Spotify = "Spotify"
    Spotify_Playlist = "Spotify Playlist"
    Spotify_Album = "Spotify Album"
    YouTube = "YouTube"
    YouTube_Playlist = "YouTube Playlist"
    Twitter = "Twitter"
    SoundCloud = "SoundCloud"
    Bandcamp = "Bandcamp"
    Custom = "Custom"
    Unknown = "Unknown"


def identify_url(url):
    if url is None:
        return Sites.Unknown

    if "youtube" in url or "youtu.be" in url:
        if "list=" in url or "playlist" in url:
            return Sites.YouTube_Playlist
        return Sites.YouTube

    if "open.spotify.com/track" in url:
        return Sites.Spotify

    if ('open.spotify.com/playlist/' in url) or ('open.spotify.com/user/' in url and '/playlist' in url):
        return Sites.Spotify_Playlist

    if "https://open.spotify.com/album/" in url:
        return Sites.Spotify_Album

    # If no match
    return Sites.Unknown


class Music(discord.Cog):
    def __init__(self, bot: discord.Bot, config: dict):
        self.bot = bot
        self.config = config
        bot.loop.create_task(self.connect_nodes())

    async def connect_nodes(self):
        await self.bot.wait_until_ready()
        await wavelink.NodePool.create_node(
            bot=self.bot,
            host=self.config["lavalink_host"],
            port=self.config["lavalink_port"],
            password=self.config["lavalink_passwd"],
            spotify_client=spotify.SpotifyClient(client_id=self.config["spotify_client_id"],
                                                 client_secret=self.config["spotify_client_secret"])
        )

    @discord.Cog.listener()
    async def on_wavelink_track_end(self, player: wavelink.Player, track, reason):
        # await self.find_related(track)
        if reason == 'FINISHED':
            try:
                await player.play(player.queue.pop())
            except wavelink.QueueEmpty:
                if player.autoplay:
                    await self.find_related(track, player)
                else:
                    await self._stop(player)
        if reason == "STOPPED":
            await player.stop()

    @discord.Cog.listener()
    async def on_wavelink_track_start(self, player: wavelink.Player, track: wavelink.Track):
        if not hasattr(player, "autoplay"):
            player.autoplay = False

    @discord.Cog.listener()
    async def on_wavelink_node_ready(self, node: wavelink.Node):
        print(f"Node: <{node.identifier}> is ready!")

    @discord.slash_command()
    async def play(self, ctx: discord.ApplicationContext, query: str):
        url_type = identify_url(query)

        if not ctx.response.is_done():
            await ctx.respond("OK")

        if not ctx.voice_client:
            vc: wavelink.Player = await ctx.author.voice.channel.connect(cls=wavelink.Player)
        else:
            vc: wavelink.Player = ctx.voice_client

        node = wavelink.NodePool.get_node()
        player = node.get_player(ctx.guild)

        if url_type == Sites.Spotify_Playlist:
            if "/user/" in query:
                query = query.split("/user/")
                query[1] = "/".join((query[1].split("/"))[1:])
                query = "/".join(query)
            async for partial in spotify.SpotifyTrack.iterator(query=query, partial_tracks=True):
                vc.queue.put_at_front(partial)

        elif url_type == Sites.Spotify:
            vc.queue.put_at_front(await spotify.SpotifyTrack.search(query=query, return_first=True))

        elif url_type == Sites.Spotify_Album:
            tracks = await spotify.SpotifyTrack.search(query=query)
            tracks.reverse()
            vc.queue.extend(tracks)

        elif url_type == Sites.YouTube_Playlist:
            pl = await node.get_playlist(cls=wavelink.YouTubePlaylist, identifier=query)
            vc.queue.extend(pl.tracks)

        elif url_type == Sites.YouTube:
            vc.queue.put_at_front(await wavelink.YouTubeTrack.search(query=query, return_first=True))

        elif url_type == Sites.Unknown:
            vc.queue.put_at_front(await wavelink.YouTubeMusicTrack.search(query=query, return_first=True))

        track = player.queue.pop()

        if not player.is_playing():
            await vc.play(track)
        else:
            vc.queue.put_at_front(track)

    @discord.message_command(name="Play in voice")
    async def _play(self, ctx: discord.ApplicationContext, message: discord.Message):
        url = await self.ensure_url(message.clean_content)
        for i in url:
            await self.play(ctx, i)

    @discord.slash_command()
    async def queue(self, ctx: discord.ApplicationContext):
        player = wavelink.NodePool.get_node().get_player(guild=ctx.guild)
        output = f"**Now playing:**\n[{player.source.title}](<{player.source.info['uri']}>)\n**Next:**\n"
        que = list(player.queue)
        que.reverse()
        for i in range(10 if len(que) > 10 else len(que)):
            track = que[i]
            output += f"[{i + 1}] {track.title}\n"
        await ctx.respond(output)

    @discord.slash_command()
    async def skip(self, ctx: discord.ApplicationContext):
        player = wavelink.NodePool.get_node().get_player(guild=ctx.guild)
        await player.seek(int(player.source.duration * 1000))
        await ctx.respond("Skipped")

    @discord.slash_command()
    async def stop(self, ctx):
        player = wavelink.NodePool.get_node().get_player(guild=ctx.guild)
        await self._stop(player)
        await ctx.respond("Stopped")

    @discord.slash_command()
    async def shuffle(self, ctx: discord.ApplicationContext):
        player = wavelink.NodePool.get_node().get_player(guild=ctx.guild)
        tmp = list(player.queue)
        random.shuffle(tmp)
        player.queue.clear()
        player.queue.extend(tmp)
        await ctx.respond("Queue shuffled")

    @discord.slash_command()
    async def autoplay(self, ctx: discord.ApplicationContext):
        player = wavelink.NodePool.get_node().get_player(guild=ctx.guild)

        player.autoplay = not player.autoplay

        if player.autoplay:
            await ctx.respond("Autoplay enabled")
        else:
            await ctx.respond("Autoplay disabled")

    async def find_related(self, track: wavelink.Track, player: wavelink.Player):
        data = requests.get(
            f"https://www.googleapis.com/youtube/v3/search?part=snippet&relatedToVideoId={track.identifier}&type=video&order=rating&key={self.config['youtube_data_api_key']}")
        data = json.loads(data.content)["items"]
        await player.play(await wavelink.YouTubeTrack.search(query=data[1]["id"]['videoId'], return_first=True))

    async def _stop(self, player):
        await player.stop()
        player.queue.clear()
        await player.disconnect(force=False)

    async def ensure_url(self, url):
        found = re.findall(r'(https?://\S+)', url)
        output = []
        for i in found:
            ensured = ""
            for c in i:
                m = re.match(r"[A-Za-z\d_.\-~:/?=%]", c)
                if m is not None:
                    ensured += c
            output.append(ensured)
        return output