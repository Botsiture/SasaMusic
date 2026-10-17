# Copyright (c) 2025 AnonymousX1025
# Licensed under the MIT License.
# This file is part of AnonXMusic


import os
import re
import yt_dlp
import random
import asyncio
import aiohttp
from pathlib import Path
from urllib.parse import urlparse

from py_yt import Playlist, VideosSearch

from anony import config, logger
from anony.helpers import Track, utils


class YouTube:
    def __init__(self):
        # Backward-compat: __main__.py still does `if yt.api: ...`
        # We no longer use NexGenApi; OneGrab is called directly via aiohttp.
        self.api = None

        self.base = "https://www.youtube.com/watch?v="
        self.api_base = getattr(
            config, "ONEGRAB_API_URL", "https://api.onegrab.fun"
        ).rstrip("/")
        self.api_key = getattr(config, "API_KEY", None)
        self.cookies = []
        self.checked = False
        self.cookie_dir = "anony/cookies"
        self.warned = False
        self.regex = re.compile(
            r"(https?://)?(www\.|m\.|music\.)?"
            r"(youtube\.com/(watch\?v=|shorts/|playlist\?list=)|youtu\.be/)"
            r"([A-Za-z0-9_-]{11}|PL[A-Za-z0-9_-]+)([&?][^\s]*)?"
        )
        self.iregex = re.compile(
            r"https?://(?:www\.|m\.|music\.)?(?:youtube\.com|youtu\.be)"
            r"(?!/(watch\?v=[A-Za-z0-9_-]{11}|shorts/[A-Za-z0-9_-]{11}"
            r"|playlist\?list=PL[A-Za-z0-9_-]+|[A-Za-z0-9_-]{11}))\S*"
        )

    # ------------------------------------------------------------------ #
    # Cookies
    # ------------------------------------------------------------------ #
    def get_cookies(self):
        if not self.checked:
            try:
                for file in os.listdir(self.cookie_dir):
                    if file.endswith(".txt"):
                        self.cookies.append(f"{self.cookie_dir}/{file}")
            except FileNotFoundError:
                Path(self.cookie_dir).mkdir(parents=True, exist_ok=True)
            self.checked = True
        if not self.cookies:
            if not self.warned:
                self.warned = True
                logger.warning("Cookies are missing; downloads might fail.")
            return None
        return random.choice(self.cookies)

    async def save_cookies(self, urls: list[str]) -> None:
        logger.info("Saving cookies from urls...")
        Path(self.cookie_dir).mkdir(parents=True, exist_ok=True)
        async with aiohttp.ClientSession() as session:
            for url in urls:
                name = url.split("/")[-1]
                link = "https://batbin.me/raw/" + name
                async with session.get(link) as resp:
                    resp.raise_for_status()
                    with open(f"{self.cookie_dir}/{name}.txt", "wb") as fw:
                        fw.write(await resp.read())
        self.cookies.clear()
        self.checked = False
        logger.info(f"Cookies saved in {self.cookie_dir}.")

    # ------------------------------------------------------------------ #
    # URL validation
    # ------------------------------------------------------------------ #
    def valid(self, url: str) -> bool:
        return bool(re.match(self.regex, url))

    def invalid(self, url: str) -> bool:
        return bool(re.match(self.iregex, url))

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _fmt_duration(self, sec) -> str:
        try:
            sec = int(sec)
        except (ValueError, TypeError):
            return "0:00"
        if sec < 0:
            sec = 0
        return f"{sec // 60}:{sec % 60:02d}"

    def _extract_id(self, video_id: str) -> str:
        if not video_id.startswith("http"):
            safe = re.sub(r"[^\w\-]", "_", video_id)
            return safe[:64] or "media"
        m = re.search(
            r"(?:v=|youtu\.be/|shorts/|embed/)([A-Za-z0-9_-]{11})",
            video_id,
        )
        if m:
            return m.group(1)
        safe = re.sub(r"[^\w\-]", "_", video_id)
        return (safe[-50:] or "media")

    async def _api_get(self, path: str, params: dict) -> dict | None:
        if not self.api_key:
            return None
        headers = {"X-API-Key": self.api_key}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.api_base}{path}",
                    params=params,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as resp:
                    if resp.status != 200:
                        logger.warning(
                            "OneGrab %s -> HTTP %s", path, resp.status
                        )
                        return None
                    return await resp.json()
        except Exception as e:
            logger.warning("OneGrab API error (%s): %s", path, e)
            return None

    # ------------------------------------------------------------------ #
    # Search
    # ------------------------------------------------------------------ #
    async def search(
        self, query: str, m_id: int, video: bool = False
    ) -> Track | None:
        if self.api_key:
            data = await self._api_get(
                "/api/search",
                {
                    "query": query,
                    "platform": "youtube",
                    "limit": 1,
                    "type": "track",
                },
            )
            if data is not None:
                results = data.get("results") or []
                if not results:
                    return None
                r = results[0]
                dur = r.get("duration", 0) or 0
                thumb = r.get("thumbnail") or ""
                return Track(
                    id=r.get("id"),
                    channel_name=r.get("channel") or "",
                    duration=self._fmt_duration(dur),
                    duration_sec=int(dur) if dur else 0,
                    message_id=m_id,
                    title=(r.get("title") or "Unknown")[:25],
                    thumbnail=thumb.split("?")[0] if thumb else "",
                    url=r.get("url"),
                    view_count=r.get("views", "") or "",
                    video=video,
                )

        try:
            _search = VideosSearch(query, limit=1, with_live=False)
            results = await _search.next()
        except Exception as e:
            logger.warning("py_yt search failed: %s", e)
            return None

        if results and results["result"]:
            data = results["result"][0]
            thumb = data.get("thumbnails", [{}])[-1].get("url") or ""
            return Track(
                id=data.get("id"),
                channel_name=data.get("channel", {}).get("name"),
                duration=data.get("duration"),
                duration_sec=utils.to_seconds(data.get("duration")),
                message_id=m_id,
                title=data.get("title")[:25],
                thumbnail=thumb.split("?")[0] if thumb else "",
                url=data.get("link"),
                view_count=data.get("viewCount", {}).get("short"),
                video=video,
            )
        return None

    # ------------------------------------------------------------------ #
    # Playlist
    # ------------------------------------------------------------------ #
    async def playlist(
        self, limit: int, user: str, url: str, video: bool
    ) -> list[Track | None]:
        tracks: list[Track | None] = []

        if self.api_key:
            data = await self._api_get("/api/get_url", {"url": url})
            if data is not None:
                results = data.get("results") or []
                for r in results[:limit]:
                    r_url = r.get("url")
                    if not r_url:
                        continue
                    dur = r.get("duration", 0) or 0
                    thumb = r.get("thumbnail") or ""
                    tracks.append(
                        Track(
                            id=r.get("id"),
                            channel_name=r.get("channel") or "",
                            duration=self._fmt_duration(dur),
                            duration_sec=int(dur) if dur else 0,
                            title=(r.get("title") or "Unknown")[:25],
                            thumbnail=thumb.split("?")[0] if thumb else "",
                            url=r_url,
                            user=user,
                            view_count=r.get("views", "") or "",
                            video=video,
                        )
                    )
                return tracks

        try:
            plist = await Playlist.get(url)
            for data in plist["videos"][:limit]:
                thumb = data.get("thumbnails", [{}])[-1].get("url") or ""
                link = data.get("link") or ""
                tracks.append(
                    Track(
                        id=data.get("id"),
                        channel_name=data.get("channel", {}).get("name", ""),
                        duration=data.get("duration"),
                        duration_sec=utils.to_seconds(data.get("duration")),
                        title=data.get("title")[:25],
                        thumbnail=thumb.split("?")[0] if thumb else "",
                        url=link.split("&list=")[0] if link else "",
                        user=user,
                        view_count="",
                        video=video,
                    )
                )
        except Exception as e:
            logger.warning("Playlist fallback failed: %s", e)
        return tracks

    # ------------------------------------------------------------------ #
    # Download helpers
    # ------------------------------------------------------------------ #
    async def _download_cdn(
        self, cdnurl: str, file_id: str, is_video: bool
    ) -> str | None:
        Path("downloads").mkdir(parents=True, exist_ok=True)

        for ext in ("mp4", "mp3", "webm", "m4a", "opus", "mkv"):
            p = Path(f"downloads/{file_id}.{ext}")
            if p.exists():
                return str(p)

        link = urlparse(cdnurl)

        if (
            link.scheme in {"http", "https"}
            and link.hostname in {"t.me", "telegram.me"}
        ):
            parts = link.path.strip("/").split("/")
            if (
                len(parts) == 2
                and parts[0] != "c"
                and parts[1].isdigit()
            ):
                channel, msg_id = parts[0], int(parts[1])
                try:
                    from anony import app as _app

                    message = await _app.get_messages(channel, msg_id)
                    if not message or not message.media:
                        logger.warning(
                            "Telegram CDN message has no media"
                        )
                        return None
                    downloaded = await _app.download_media(
                        message, file_name="downloads/"
                    )
                    if not downloaded:
                        return None
                    ext = Path(downloaded).suffix or (
                        ".mp4" if is_video else ".mp3"
                    )
                    target = Path(f"downloads/{file_id}{ext}")
                    if Path(downloaded) != target:
                        try:
                            Path(downloaded).replace(target)
                        except OSError:
                            return str(downloaded)
                    return str(target)
                except Exception as e:
                    logger.warning(
                        "Telegram CDN download failed: %s", e
                    )
                    return None
            logger.warning(
                "Unsupported Telegram link format: %s", cdnurl
            )
            return None

        ext = "mp4" if is_video else "mp3"
        filename = f"downloads/{file_id}.{ext}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    cdnurl, timeout=aiohttp.ClientTimeout(total=600)
                ) as resp:
                    if resp.status != 200:
                        logger.warning(
                            "CDN HTTP %s for %s", resp.status, cdnurl
                        )
                        return None
                    with open(filename, "wb") as f:
                        async for chunk in resp.content.iter_chunked(65536):
                            if chunk:
                                f.write(chunk)
            return filename
        except Exception as e:
            logger.warning("HTTP CDN download failed: %s", e)
            try:
                Path(filename).unlink(missing_ok=True)
            except Exception:
                pass
            return None

    # ------------------------------------------------------------------ #
    # Download
    # ------------------------------------------------------------------ #
    async def download(
        self, video_id: str, video: bool = False
    ) -> str | None:
        url = (
            video_id
            if video_id.startswith("http")
            else self.base + video_id
        )
        file_id = self._extract_id(video_id)

        if self.api_key:
            data = await self._api_get(
                "/api/track",
                {"url": url, "video": "true" if video else "false"},
            )
            if data and data.get("cdnurl"):
                is_video = bool(data.get("is_video", video))
                if video and not is_video:
                    logger.warning(
                        "OneGrab returned audio for video request; "
                        "falling back to yt-dlp"
                    )
                else:
                    file_path = await self._download_cdn(
                        data["cdnurl"], file_id, is_video
                    )
                    if file_path:
                        return file_path
            elif data is not None:
                logger.warning("OneGrab /api/track returned no cdnurl")

        ext = "mp4" if video else "webm"
        filename = f"downloads/{file_id}.{ext}"

        if Path(filename).exists():
            return filename

        Path("downloads").mkdir(parents=True, exist_ok=True)

        cookie = self.get_cookies()
        base_opts = {
            "outtmpl": "downloads/%(id)s.%(ext)s",
            "quiet": True,
            "noplaylist": True,
            "geo_bypass": True,
            "no_warnings": True,
            "overwrites": False,
            "nocheckcertificate": True,
        }
        if cookie:
            base_opts["cookiefile"] = cookie

        if video:
            ydl_opts = {
                **base_opts,
                "format": (
                    "(bestvideo[height<=?720][width<=?1280][ext=mp4])"
                    "+(bestaudio)"
                ),
                "merge_output_format": "mp4",
            }
        else:
            ydl_opts = {
                **base_opts,
                "format": "bestaudio[ext=webm][acodec=opus]",
            }

        def _download():
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                try:
                    ydl.download([url])
                except (
                    yt_dlp.utils.DownloadError,
                    yt_dlp.utils.ExtractorError,
                ):
                    return None
                except Exception as ex:
                    logger.warning("Download failed: %s", ex)
                    return None

            matches = sorted(
                Path("downloads").glob(f"{file_id}.*"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            for p in matches:
                if p.is_file():
                    return str(p)
            return None

        return await asyncio.to_thread(_download)
