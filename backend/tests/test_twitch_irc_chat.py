import time

from app.services.live_chat_viewer import LiveChatViewer
from app.services.twitch_irc_chat import ProviderLiveChatFetcher, TwitchChatFetcher, parse_irc_line, twitch_channel

MSG = "@badges=subscriber/12,moderator/1;display-name=Panda_Fan;id=abc-1;user-id=42 :panda_fan!panda_fan@panda_fan.tmi.twitch.tv PRIVMSG #wardogs :W stream"
BITS = "@badges=;bits=100;display-name=Cheery;id=abc-2;user-id=43 :cheery!cheery@cheery.tmi.twitch.tv PRIVMSG #wardogs :cheer100 lets go"
SUB = r"@badges=subscriber/0;display-name=Newbie;id=abc-3;msg-id=sub;system-msg=Newbie\ssubscribed\sat\sTier\s1.;user-id=44 :tmi.twitch.tv USERNOTICE #wardogs"


def test_irc_lines_become_normalized_events() -> None:
    event = parse_irc_line(MSG)
    assert event is not None and event.id == "twitch:abc-1" and event.text == "W stream" and event.kind == "message"
    assert event.author.name == "Panda_Fan" and event.author.badges == ("member", "moderator") and event.author.channel_id == "42"
    bits = parse_irc_line(BITS)
    assert bits.kind == "paid_message" and bits.amount == "100 Bits"
    sub = parse_irc_line(SUB)
    assert sub.kind == "system" and sub.text == "Newbie subscribed at Tier 1."
    assert parse_irc_line(":tmi.twitch.tv 001 justinfan123 :Welcome") is None
    assert parse_irc_line("PING :tmi.twitch.tv") is None


def test_channel_urls() -> None:
    assert twitch_channel("https://www.twitch.tv/WarDogs") == "wardogs"
    assert twitch_channel("https://twitch.tv/wardogs?ref=x") == "wardogs"
    assert twitch_channel("https://www.twitch.tv/videos/123") is None
    assert twitch_channel("https://www.youtube.com/watch?v=IIMr8iqlF4M") is None


class FakeConn:
    def __init__(self, lines: list[str]) -> None:
        self.sent = b""
        self.chunks = [("\r\n".join(lines) + "\r\n").encode()]

    def __enter__(self):  # noqa: ANN204
        return self

    def __exit__(self, *exc) -> None:  # noqa: ANN002
        return None

    def sendall(self, data: bytes) -> None:
        self.sent += data

    def recv(self, _size: int) -> bytes:
        if self.chunks:
            return self.chunks.pop(0)
        time.sleep(0.05)
        raise TimeoutError


def test_reader_joins_anonymously_answers_ping_and_feeds_the_viewer() -> None:
    conn = FakeConn(["PING :tmi.twitch.tv", MSG, BITS])
    twitch = TwitchChatFetcher(connect=lambda: conn)
    viewer = LiveChatViewer(ProviderLiveChatFetcher(youtube=None, twitch=twitch), poll_interval_seconds=0)
    viewer.read("https://www.twitch.tv/wardogs", "u1")
    deadline = time.monotonic() + 2
    texts: list[str] = []
    while time.monotonic() < deadline and len(texts) < 2:
        texts = [event["text"] for event in viewer.read("https://www.twitch.tv/wardogs", "u1")["events"]]
    assert texts == ["W stream", "cheer100 lets go"]
    assert b"NICK justinfan" in conn.sent and b"JOIN #wardogs" in conn.sent and b"PONG :tmi.twitch.tv" in conn.sent
    assert b"PASS SCHMOOPIIE" in conn.sent  # anonymous read-only login, never a real credential
    key = next(iter(twitch._readers))
    twitch.close(key)
    assert key not in twitch._readers


def test_youtube_sources_still_route_to_the_youtube_fetcher() -> None:
    class YouTube:
        def bootstrap(self, source_url, owner_user_id):  # noqa: ANN001
            return "yt"

    router = ProviderLiveChatFetcher(YouTube(), TwitchChatFetcher(connect=lambda: FakeConn([])))
    assert router.bootstrap("https://www.youtube.com/watch?v=IIMr8iqlF4M", "u") == "yt"
