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


def test_repeated_twitch_emotes_use_codepoint_ranges_and_static_cdn_urls() -> None:
    line = (
        "@badges=;display-name=Ada;emotes=25:2-6,8-12;id=emotes-1;user-id=42 "
        ":ada!ada@ada.tmi.twitch.tv PRIVMSG #wardogs :😀 Kappa Kappa"
    )

    event = parse_irc_line(line)

    assert event is not None and event.text == "😀 Kappa Kappa"
    assert [(emote.start, emote.end, emote.label) for emote in event.emotes] == [
        (2, 7, "Kappa"),
        (8, 13, "Kappa"),
    ]
    assert [emote.image_url for emote in event.emotes] == [
        "https://static-cdn.jtvnw.net/emoticons/v2/25/static/light/1.0",
        "https://static-cdn.jtvnw.net/emoticons/v2/25/static/light/1.0",
    ]


def test_twitch_emotes_reject_bad_ids_ranges_overlaps_and_clipped_text() -> None:
    text = "x" * 495 + "Kappa"
    line = (
        "@badges=;display-name=Ada;"
        "emotes=25:495-499/bad!:0-1/26:3-x/27:0-999/28:496-499;"
        "id=emotes-2;user-id=42 "
        f":ada!ada@ada.tmi.twitch.tv PRIVMSG #wardogs :{text}tail"
    )

    event = parse_irc_line(line)

    assert event is not None and len(event.text) == 500
    assert [(emote.start, emote.end, emote.label) for emote in event.emotes] == [
        (495, 500, "Kappa"),
    ]


def test_twitch_emote_offsets_follow_sanitized_text() -> None:
    line = (
        "@badges=;display-name=Ada;emotes=25:4-8;id=emotes-3;user-id=42 "
        ":ada!ada@ada.tmi.twitch.tv PRIVMSG #wardogs :a\u202eb Kappa"
    )

    event = parse_irc_line(line)

    assert event is not None and event.text == "ab Kappa"
    assert [(emote.start, emote.end, emote.label) for emote in event.emotes] == [(3, 8, "Kappa")]


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
