"""A loaded server's side endpoints, for `make playperf`: slow_proxy.py LISTEN UPSTREAM DELAY_SECONDS.

Forwards http://127.0.0.1:LISTEN to :UPSTREAM and holds every request of the watch page's info column (tags, notes, transcripts,
tracks, segments, queue, enrichment, titles) for DELAY_SECONDS before forwarding. Unlike a Playwright route, the held requests keep
their browser connections, which is the point: Chrome allows 6 per host over plain http, and the video's own request must not
queue behind them. One request per connection (Connection: close); Host and Origin are rewritten to the upstream's.
"""
import asyncio
import re
import sys

LISTEN, UPSTREAM, DELAY = int(sys.argv[1]), int(sys.argv[2]), float(sys.argv[3])
SLOW = re.compile(rb"^(GET|PUT|POST) /api/(library/[^/ ]+/(provenance|summary|notes|transcripts|subtitle-tracks|segments|tags|mute-ranges)|me/watch-queue|enrichment|titles/)")


async def pipe(source: asyncio.StreamReader, sink: asyncio.StreamWriter) -> None:
    try:
        while data := await source.read(65536):
            sink.write(data)
            await sink.drain()
    except OSError:
        pass
    finally:
        sink.close()


async def handle(client: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        head = await client.readuntil(b"\r\n\r\n")
        length = re.search(rb"content-length: *(\d+)", head, re.I)
        body = await client.readexactly(int(length[1])) if length else b""
        head = head.replace(b"127.0.0.1:%d" % LISTEN, b"127.0.0.1:%d" % UPSTREAM)
        if SLOW.match(head):
            await asyncio.sleep(DELAY)
        head = re.sub(rb"(?i)\r\nconnection:[^\r]*", b"", head).replace(b"\r\n\r\n", b"\r\nConnection: close\r\n\r\n", 1)
        upstream, out = await asyncio.open_connection("127.0.0.1", UPSTREAM)
        out.write(head + body)
        await out.drain()
        await asyncio.gather(pipe(upstream, writer), pipe(client, out))
    except (OSError, asyncio.IncompleteReadError):
        writer.close()


async def main() -> None:
    server = await asyncio.start_server(handle, "127.0.0.1", LISTEN)
    async with server:
        await server.serve_forever()


asyncio.run(main())
