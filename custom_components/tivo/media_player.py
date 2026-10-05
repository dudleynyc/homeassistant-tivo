"""
Support for the Tivo receivers.

For more details about this platform, please refer to the documentation at
https://home-assistant.io/components/media_player.tivo/
"""
import asyncio
from calendar import timegm
from datetime import datetime, timedelta, timezone
import json

# from pytz import timezone
import logging
import socket
import time
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from homeassistant.components.media_player import PLATFORM_SCHEMA, MediaPlayerEntity
from homeassistant.components.media_player.const import (
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
)
from homeassistant.const import (
    CONF_DEVICE,
    CONF_HOST,
    CONF_NAME,
    CONF_PORT,
)
import homeassistant.helpers.config_validation as cv

from homeassistant.helpers.event import async_track_time_interval
import voluptuous as vol

_LOGGER = logging.getLogger(__name__)

DEFAULT_NAME = "Tivo Receiver"
DEFAULT_PORT = 31339
DEFAULT_DEVICE = "0"

CONF_UNIQUE_ID = "unique_id"
CONF_GRACENOTE_USERNAME = "gracenote_username"
CONF_GRACENOTE_PASSWORD = "gracenote_password"
CONF_GRACENOTE_API_KEY = "gracenote_api_key"
CONF_GRACENOTE_LINEUP_ID = "gracenote_lineup_id"
CONF_GRACENOTE_POSTAL_CODE = "gracenote_postal_code"
CONF_GRACENOTE_COUNTRY = "gracenote_country"
# Retained so existing configurations continue to work.
CONF_ZAPUSER = "zapuser"
CONF_ZAPPASS = "zappass"
CONF_DEBUG = "debug"

GUIDE_SCAN_INTERVAL = timedelta(minutes=5)
CONNECT_TIMEOUT = 5
STATUS_RESPONSE_TIMEOUT = 2
COMMAND_RESPONSE_TIMEOUT = 5
MAX_RECONNECT_DELAY = 60
STATUS_STALE_TIMEOUT = 4 * 60 * 60
DEFAULT_IMAGE_URL = "https://tvlistings.gracenote.com/assets/images/noImage165x220.jpg"

SUPPORT_TIVO = (
    MediaPlayerEntityFeature.PAUSE
    | MediaPlayerEntityFeature.PLAY_MEDIA
    | MediaPlayerEntityFeature.STOP
    | MediaPlayerEntityFeature.NEXT_TRACK
    | MediaPlayerEntityFeature.TURN_ON
    | MediaPlayerEntityFeature.TURN_OFF
    | MediaPlayerEntityFeature.PREVIOUS_TRACK
    | MediaPlayerEntityFeature.PLAY
)

DATA_TIVO = "data_tivo"

PLATFORM_SCHEMA = PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_HOST): cv.string,
        vol.Optional(CONF_UNIQUE_ID): cv.string,
        vol.Optional(CONF_NAME, default=DEFAULT_NAME): cv.string,
        vol.Optional(CONF_PORT, default=DEFAULT_PORT): cv.port,
        vol.Optional(CONF_DEVICE, default=DEFAULT_DEVICE): cv.string,
        vol.Optional(CONF_ZAPUSER, default=""): cv.string,
        vol.Optional(CONF_ZAPPASS, default=""): cv.string,
        vol.Optional(CONF_GRACENOTE_USERNAME, default=""): cv.string,
        vol.Optional(CONF_GRACENOTE_PASSWORD, default=""): cv.string,
        vol.Optional(CONF_GRACENOTE_API_KEY, default=""): cv.string,
        vol.Optional(CONF_GRACENOTE_LINEUP_ID, default=""): cv.string,
        vol.Optional(CONF_GRACENOTE_POSTAL_CODE, default=""): cv.string,
        vol.Optional(CONF_GRACENOTE_COUNTRY, default=""): cv.string,
        vol.Optional(CONF_DEBUG, default=False): cv.boolean,
    }
)


async def async_setup_platform(hass, config, async_add_entities, discovery_info=None):
    """Set up the Tivo platform."""
    known_devices = hass.data.get(DATA_TIVO)
    if not known_devices:
        known_devices = []
    guide_username = config.get(CONF_GRACENOTE_USERNAME) or config.get(CONF_ZAPUSER)
    guide_password = config.get(CONF_GRACENOTE_PASSWORD) or config.get(CONF_ZAPPASS)
    guide_api_key = config.get(CONF_GRACENOTE_API_KEY)
    guide_lineup_id = config.get(CONF_GRACENOTE_LINEUP_ID)
    guide_postal_code = config.get(CONF_GRACENOTE_POSTAL_CODE)
    guide_country = config.get(CONF_GRACENOTE_COUNTRY)
    guide_client = None
    debug = config.get(CONF_DEBUG)

    if guide_lineup_id or (guide_username and guide_password):
        guide_client = GracenoteClient(
            guide_username,
            guide_password,
            debug,
            api_key=guide_api_key,
            lineup_id=guide_lineup_id,
            postal_code=guide_postal_code,
            country=guide_country,
            local_timezone=hass.config.time_zone,
        )
        await hass.async_add_executor_job(guide_client.update)

    tivo = TivoDevice(
        config.get(CONF_UNIQUE_ID),
        config.get(CONF_NAME),
        config.get(CONF_HOST),
        config.get(CONF_PORT),
        config.get(CONF_DEVICE),
        guide_client,
        debug,
    )
    known_devices.append(config.get(CONF_HOST))
    # Add the entity immediately. Some TiVos do not emit CH_STATUS when a
    # connection opens, so waiting for an initial poll can delay or prevent
    # entity registration.
    async_add_entities([tivo], False)
    hass.data[DATA_TIVO] = known_devices

    async def gracenote_update(event_time):
        await hass.async_add_executor_job(guide_client.update)

    if guide_client:
        async_track_time_interval(hass, gracenote_update, GUIDE_SCAN_INTERVAL)

    return True


class TivoDevice(MediaPlayerEntity):
    """Representation of a Tivo receiver on the network."""

    def __init__(self, unique_id, name, host, port, device, guide_client, debug):
        """Initialize the device."""
        # Home Assistant requires a unique ID before an entity can be managed
        # from the UI. Keep an explicitly configured ID, but provide a stable
        # fallback for existing YAML configurations.
        self._unique_id = unique_id or f"tivo_{host}"
        self._name = name
        self._host = host
        self._port = port

        self.guide_client = guide_client

        self._available = False
        self._is_standby = False
        # The TCP remote protocol does not expose a general playback-status
        # query. Start in Home Assistant's explicit "on, state unknown" state
        # until the TiVo reports a live-TV channel or HA sends a playback
        # command.
        self._playback_state = MediaPlayerState.ON
        # Entity properties can be read before the first successful poll.
        self._current = {
            "channel": "no channel",
            "title": "TiVo state unavailable",
            "status": "Unknown",
            "mode": "UNKNOWN",
            "image": DEFAULT_IMAGE_URL,
        }
        self._ignore = {}
        self.sock = None
        self._status_writer = None
        self._status_writer_ready = asyncio.Event()
        self._status_writer_lock = asyncio.Lock()
        self._status_expires_at = None

        self.debug = debug

    async def async_added_to_hass(self):
        """Start listening for TiVo channel-status broadcasts."""
        await super().async_added_to_hass()
        # This listener is intentionally long-lived. Register it as a
        # background task so Home Assistant does not wait for it to finish
        # before completing startup.
        listener_task = self.hass.async_create_background_task(
            self._async_status_listener(), f"TiVo status listener: {self._name}"
        )
        self.async_on_remove(listener_task.cancel)

    async def _async_status_listener(self):
        """Maintain a connection that receives transient CH_STATUS events."""
        reconnect_delay = 1

        while True:
            writer = None
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(self._host, self._port),
                    timeout=CONNECT_TIMEOUT,
                )
                self._status_writer = writer
                self._status_writer_ready.set()
                self._available = True
                self.async_write_ha_state()

                if self.debug:
                    _LOGGER.debug("Listening for status from %s", self._name)

                while True:
                    timeout = None
                    if self._status_expires_at is not None:
                        timeout = max(
                            0, self._status_expires_at - time.monotonic()
                        )

                    try:
                        if timeout is None:
                            raw_status = await reader.readuntil(b"\r")
                        else:
                            raw_status = await asyncio.wait_for(
                                reader.readuntil(b"\r"), timeout=timeout
                            )
                    except TimeoutError:
                        self._clear_stale_status()
                        self.async_write_ha_state()
                        continue

                    status = raw_status.decode(errors="replace").strip()
                    if not status:
                        continue

                    words = status.split()
                    if words[0] != "CH_STATUS":
                        if self.debug:
                            _LOGGER.debug(
                                "Ignoring TiVo status message from %s: %s",
                                self._name,
                                status,
                            )
                        continue

                    reconnect_delay = 1
                    self.set_status(words)
                    self._available = True
                    self.async_write_ha_state()

            except asyncio.CancelledError:
                raise
            except (
                TimeoutError,
                OSError,
                asyncio.IncompleteReadError,
                asyncio.LimitOverrunError,
            ) as err:
                if self.debug:
                    _LOGGER.debug(
                        "TiVo status listener disconnected from %s: %s",
                        self._name,
                        err,
                    )
                self._available = False
                self.async_write_ha_state()
            finally:
                if self._status_writer is writer:
                    self._status_writer = None
                    self._status_writer_ready.clear()
                if writer:
                    writer.close()
                    try:
                        await writer.wait_closed()
                    except OSError:
                        pass

            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(reconnect_delay * 2, MAX_RECONNECT_DELAY)

    async def _async_send_code(self, code, cmdtype="IRCODE", extra=0):
        """Send a command over the persistent status connection."""
        try:
            await asyncio.wait_for(
                self._status_writer_ready.wait(), timeout=CONNECT_TIMEOUT
            )
            async with self._status_writer_lock:
                writer = self._status_writer
                if writer is None or writer.is_closing():
                    return False

                if extra:
                    code = f"{code} {extra}"
                command = f"{cmdtype} {code}\r" if cmdtype else f"{code}\r"

                if self.debug:
                    _LOGGER.debug("Sending request: '%s'", command)

                writer.write(command.encode())
                await writer.drain()
            return True
        except (TimeoutError, OSError, ConnectionError) as err:
            _LOGGER.warning("Unable to send command to %s: %s", self._name, err)
            return False

    def _clear_stale_status(self):
        """Clear Live TV details that have not been refreshed for four hours."""
        if self.debug:
            _LOGGER.debug("Clearing stale Live TV status for %s", self._name)

        self._current.update(
            {
                "channel": None,
                "title": None,
                "status": None,
                "mode": "UNKNOWN",
                "image": None,
            }
        )
        self._status_expires_at = None
        self._playback_state = MediaPlayerState.ON

    def update(self):
        """Fetch the current state without blocking Home Assistant's event loop."""
        self.get_status()

    def connect(self, host, port):
        try:
            if self.debug:
                _LOGGER.info("Connecting to device...")
            self.sock = socket.socket()
            self.sock.settimeout(CONNECT_TIMEOUT)
            self.sock.connect((host, port))
        except Exception:
            raise

    def disconnect(self):
        if self.debug:
            _LOGGER.info("Disconnecting from device...")
        self.sock.close()

    def get_status(self):
        if self.debug:
            _LOGGER.info("get_status called...")
        data = self.send_code("", "")
        """ e.g. CH_STATUS 0645 LOCAL """
        """ e.g. CH_STATUS 0645 RECORDING """

        words = data.split()
        if words and words[0] == "INVALID":
            self._available = False
            return

        self._available = True
        self.set_status(words)

    def set_status(self, words):
        if not words:
            _LOGGER.debug("device did not respond correctly...")
            return

        self._current["channel"] = "no channel"
        self._current["title"] = "no title"
        self._current["status"] = "no status"
        self._current["mode"] = "none"
        # returns no image
        self._current["image"] = DEFAULT_IMAGE_URL

        # A TiVo does not send CH_STATUS while playing recorded content or
        # showing some menus. A successful connection only proves that it is
        # online; it does not prove that media is playing or that it is idle.
        if words[0] == "no_channel" or len(words) < 3:
            self._current["title"] = "TiVo state unavailable"
            self._current["status"] = "Unknown"
            self._current["mode"] = "UNKNOWN"
            self._status_expires_at = None
            self._playback_state = MediaPlayerState.ON
            return

        if words[0] != "CH_STATUS":
            return

        # subchannel?
        if len(words) == 4:
            channel = words[1].lstrip("0") + "." + words[2].lstrip("0")
            channel = channel.zfill(4)
            status = words[3]
        else:
            channel = words[1].lstrip("0")
            channel = channel.zfill(4)
            status = words[2]

        self._current["channel"] = channel
        self._current["title"] = "Ch. {}".format(channel)
        self._current["status"] = status
        self._current["mode"] = "TV"
        self._status_expires_at = time.monotonic() + STATUS_STALE_TIMEOUT
        self._playback_state = MediaPlayerState.PLAYING

        if self.guide_client:
            guide_ch = channel.replace("-", ".")
            ch = self.guide_client.get_callsign(guide_ch) or channel
            self._current["channel"] = ch
            num = guide_ch.lstrip("0")
            ti = self.guide_client.get_title(guide_ch) or "Unknown program"
            if self.debug:
                _LOGGER.info("Channel:  %s", num)
                _LOGGER.info("Callsign: %s", ch)
                _LOGGER.info("Title:    %s", ti)

            self._current["title"] = "Ch. {} {}: {}".format(num, ch, ti)
            self._current["image"] = (
                self.guide_client.get_image_url(guide_ch)
                or self.guide_client.get_logo_url(guide_ch)
                or self.guide_client.NO_IMAGE_URL
            )

        self._is_standby = False

    def send_code(self, code, cmdtype="IRCODE", extra=0, bufsize=1024):
        data = ""
        if extra:
            code = code + " " + extra
            # can be '', IRCODE, KEYBOARD, or TELEPORT.  Usually it's IRCODE but we might switch to KEYBOARD since it can do more.

        try:
            self.connect(self._host, self._port)
            self.sock.settimeout(
                COMMAND_RESPONSE_TIMEOUT if code else STATUS_RESPONSE_TIMEOUT
            )
            if code:
                if cmdtype == "":
                    tosend = code + "\r"
                else:
                    tosend = cmdtype + " " + code + "\r"
            else:
                tosend = ""

            if self.debug:
                _LOGGER.debug("Sending request: '%s'", tosend)

            try:
                self.sock.sendall(tosend.encode())
                time.sleep(0.3)
                data = self.sock.recv(bufsize)
                if self.debug:
                    _LOGGER.debug("Received response: '%s'", data)
            except socket.timeout:
                if self.debug:
                    _LOGGER.warning("Connection timed out...")
                data = b"no_channel Video"

            return data.decode()
        except Exception:
            return "INVALID CONNECTION"
        finally:
            if self.sock:
                try:
                    self.disconnect()
                except OSError:
                    pass
                self.sock = None

    def channel_scan(self):
        for i in range(1, self._channel_max):
            res = self.send_code("SETCH", "IRCODE", str(i))
            words = res.split()
            if words[0] == "INVALID":
                self._ignore.append(str(i))

    @property
    def unique_id(self):
        """Return a unique ID."""
        return self._unique_id

    @property
    def available(self):
        """Return whether the TiVo accepted a network connection."""
        return self._available

    @property
    def should_poll(self):
        """Use the persistent TiVo status connection instead of polling."""
        return False

    # MediaPlayerEntity properties and methods
    @property
    def name(self):
        """Return the name of the device."""
        return self._name

    @property
    def state(self):
        """Return the state of the device."""
        if self._is_standby:
            return MediaPlayerState.OFF
        return self._playback_state

    @property
    def show_live(self):
        data = ""
        """Live TV. """
        """ Any client wishing to set a channel must wait for """
        """ LIVETV_READY before issuing a SETCH or FORCECH command. """
        data = self.send_code("LIVETV", "TELEPORT")
        self._current["mode"] = "TV"
        return data

    @property
    def show_guide(self):
        data = ""
        """Guide."""
        """ Also returns status as with NOWPLAYING, e.g. CH_STATUS 0613 LOCAL """
        data = self.send_code("GUIDE", "TELEPORT")
        self._current["mode"] = "GUIDE"
        return data

    @property
    def show_tivo(self):
        data = ""
        """Tivo menu."""
        self.send_code("TIVO", "TELEPORT")
        self._current["mode"] = "MENU"
        return data

    @property
    def show_now(self):
        data = b""
        """Now playing."""
        data = self.send_code("NOWPLAYING", "TELEPORT")
        self._current["mode"] = "NOWPLAYING"
        return data

    @property
    def show_vod(self):
        data = b""
        """ Activate Video on demand menu """
        data = self.send_code("VIDEO_ON_DEMAND", "KEYBOARD")
        self._current["mode"] = "VIDEO"
        return data

    def channel_set(self, channel):
        """Channel set."""
        data = self.show_live()
        # if(data.trim() == "LIVETV_READY"):
        self.send_code("SETCH", "", channel)

    def media_ch_up(self):
        """Channel up."""
        if self._current["mode"] == "TV":
            data = self.send_code("CHANNELUP")
            words = data.split()
            self.set_status(words)

    def media_ch_dn(self):
        """Channel down."""
        if self._current["mode"] == "TV":
            data = self.send_code("CHANNELDOWN")
            words = data.split()
            self.set_status(words)

    @property
    def media_content_id(self):
        """Return the content ID of current playing media."""
        if self._is_standby:
            return None
        return self._current.get("status")

    @property
    def media_duration(self):
        """Return the duration of current playing media in seconds."""
        if self._is_standby:
            return None

        return ""

    @property
    def media_title(self):
        """Return the title of current playing media."""
        if self._is_standby:
            return None
        return self._current.get("title")

    @property
    def media_image_url(self):
        """Return the image url of current playing media."""
        if self._is_standby:
            return None
        return self._current.get("image")

    @property
    def media_series_title(self):
        """Return the title of current episode of TV show."""
        if self._is_standby:
            return None
        elif "episodeTitle" in self._current:
            return self._current["episodeTitle"]
        return ""

    @property
    def supported_features(self):
        """Flag media player features that are supported."""
        return SUPPORT_TIVO

    @property
    def media_content_type(self):
        """Return the content type of current playing media."""
        if self._is_standby:
            return

        if "episodeTitle" in self._current:
            return MediaType.TVSHOW
        return MediaType.VIDEO

    @property
    def media_channel(self):
        """Return the channel current playing media."""
        if self._is_standby:
            return None

        status = self._current.get("status")
        channel = self._current.get("channel")
        if not status or not channel:
            return None
        return "{} ({})".format(status, channel)

    def turn_on(self):
        """Turn on the receiver. """
        if self._is_standby:
            self.send_code("STANDBY", "IRCODE")
            self._is_standby = False
            self._playback_state = MediaPlayerState.ON

    async def async_turn_on(self):
        """Turn on the receiver over the persistent connection."""
        if self._is_standby and await self._async_send_code("STANDBY"):
            self._is_standby = False
            self._playback_state = MediaPlayerState.ON
            self.async_write_ha_state()

    def turn_off(self):
        """Turn off the receiver. """
        if self._is_standby == False:
            self.send_code("STANDBY", "IRCODE")
            self.send_code("STANDBY", "IRCODE")
            self._is_standby = True

    async def async_turn_off(self):
        """Turn off the receiver over the persistent connection."""
        if not self._is_standby and await self._async_send_code("STANDBY"):
            await self._async_send_code("STANDBY")
            self._is_standby = True
            self.async_write_ha_state()

    def media_play(self):
        """Send play command."""
        if self._is_standby:
            return

        self.send_code("PLAY")
        self._playback_state = MediaPlayerState.PLAYING
        self.schedule_update_ha_state()

    async def async_media_play(self):
        """Send play over the connection used by the status listener."""
        if self._is_standby:
            return

        if await self._async_send_code("PLAY"):
            self._playback_state = MediaPlayerState.PLAYING
            self.async_write_ha_state()

    def media_pause(self):
        """Send pause command."""
        if self._is_standby:
            return None

        self.send_code("PAUSE", "IRCODE", 0, 0)
        # TiVo's network remote protocol does not report pause status in its
        # CH_STATUS response, so retain the state of commands sent through HA.
        self._playback_state = MediaPlayerState.PAUSED
        self.schedule_update_ha_state()

    async def async_media_pause(self):
        """Send pause over the connection used by the status listener."""
        if self._is_standby:
            return

        if await self._async_send_code("PAUSE"):
            self._playback_state = MediaPlayerState.PAUSED
            self.async_write_ha_state()

    def media_stop(self):
        """Send stop command. """
        if self._is_standby:
            return None

        if self._current["mode"] == "TV":
            return "INTV"

        data = self.send_code("STOP", "IRCODE", 0, 0)
        words = data.split()
        return words[2]

    async def async_media_stop(self):
        """Send stop over the connection used by the status listener."""
        if self._is_standby or self._current["mode"] == "TV":
            return

        if await self._async_send_code("STOP"):
            self._playback_state = MediaPlayerState.ON
            self.async_write_ha_state()

    def media_record(self):
        """ Start recording the current program """
        if self._is_standby:
            return

        self.send_code("RECORD", "IRCODE")

    def media_previous_track(self):
        """Send rewind command."""
        if self._is_standby:
            return

        self.send_code("REVERSE", "IRCODE", 0, 0)

    async def async_media_previous_track(self):
        """Send rewind over the persistent connection."""
        if self._is_standby:
            return

        await self._async_send_code("REVERSE")

    def media_next_track(self):
        """Send fast forward command."""
        if self._is_standby:
            return

        self.send_code("FORWARD", "IRCODE", 0, 0)

    async def async_media_next_track(self):
        """Send fast-forward over the persistent connection."""
        if self._is_standby:
            return

        await self._async_send_code("FORWARD")


class GracenoteClient:
    """Client for the Gracenote TV listings service that replaced Zap2it."""

    BASE_URL = "https://tvlistings.gracenote.com/"
    API_URL = "https://data.tmsapi.com/v1.1/"
    IMAGE_URL = "https://zpmc.tmsimg.com/"
    NO_IMAGE_URL = DEFAULT_IMAGE_URL
    USER_AGENT = (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/140.0 Safari/537.36"
    )

    def __init__(
        self,
        username,
        password,
        debug=False,
        *,
        api_key="",
        lineup_id="",
        postal_code="",
        country="",
        local_timezone="UTC",
    ):
        self._username = username
        self._password = password
        self._api_key = api_key
        self._api_lineup_id = lineup_id
        self._postal_code = postal_code
        self._configured_country = country
        self._local_timezone = local_timezone
        self.debug = debug

        self._channels = {}
        self._titles = {}
        self._images = {}
        self._logos = {}

    def get_callsign(self, ch):
        return self._channels.get(ch)

    def get_title(self, ch):
        return self._titles.get(ch)

    def get_image_url(self, ch):
        return self._images.get(ch)

    def get_logo_url(self, ch):
        return self._logos.get(ch)

    def update(self):
        if self._api_key and self._api_lineup_id:
            self.get_api_data()
        elif self._api_lineup_id:
            self.get_public_data()
        else:
            self.get_data()

    def get_public_data(self):
        """Fetch guide data from Gracenote's public listings grid."""
        country = self._configured_country or self._api_lineup_id.split("-", 1)[0]
        lineup_parts = self._api_lineup_id.split("-")
        headend_id = lineup_parts[1] if len(lineup_parts) > 1 else self._api_lineup_id
        if "OTA" in self._api_lineup_id:
            headend_id = "lineupId"
        params = urlencode(
            {
                "lineupId": self._api_lineup_id,
                "timespan": "1",
                "headendId": headend_id,
                "country": country,
                "timezone": self._local_timezone,
                "postalCode": self._postal_code,
                "isOverride": "true",
                "pref": "-",
                "aid": "orbebb",
                "languagecode": "en-us",
                "time": str(int(time.time())),
                "device": "X",
                "userId": "-",
            }
        )
        url = f"{self.BASE_URL}api/grid?{params}"
        request = Request(url, headers={"User-Agent": self.USER_AGENT}, method="GET")
        try:
            with urlopen(request, timeout=10) as response:
                self._zapraw = json.loads(response.read().decode("utf8"))
        except Exception as err:
            _LOGGER.warning("Unable to download public Gracenote listings: %s", err)
            return
        self.get_channels()
        self.get_titles()

    def get_api_data(self):
        """Fetch guide data using Gracenote's supported Video API."""
        start = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
        params = urlencode(
            {
                "startDateTime": start.strftime("%Y-%m-%dT%H:%MZ"),
                "endDateTime": (start + timedelta(hours=1)).strftime(
                    "%Y-%m-%dT%H:%MZ"
                ),
                "size": "Basic",
                "imageSize": "Sm",
                "api_key": self._api_key,
            }
        )
        url = (
            f"{self.API_URL}lineups/{quote(self._api_lineup_id, safe='')}/grid?"
            f"{params}"
        )
        try:
            with urlopen(Request(url, method="GET"), timeout=10) as response:
                grid = json.loads(response.read().decode("utf8"))
        except Exception as err:
            _LOGGER.warning("Unable to download Gracenote API listings: %s", err)
            return

        # Normalize the supported API response to the shape used by the old
        # consumer grid so channel/title lookup stays in one place.
        channels = []
        for station in grid:
            events = []
            for airing in station.get("airings", []):
                event = dict(airing)
                image = airing.get("program", {}).get("preferredImage", {}).get("uri")
                event["thumbnail"] = image or ""
                events.append(event)
            channels.append(
                {
                    "channelNo": station.get("channel", ""),
                    "callSign": station.get("callSign", ""),
                    "events": events,
                }
            )
        self._zapraw = {"channels": channels}
        self.get_channels()
        self.get_titles()

    def login(self):
        # Login and fetch a token
        host = self.BASE_URL
        loginpath = "api/user/login"
        login = host + loginpath

        tosend = {
            "emailid": self._username,
            "password": self._password,
            "usertype": "0",
            "facebookuser": "false",
        }
        tosend_json = json.dumps(tosend).encode("utf8")
        header = {"content-type": "application/json"}

        req = Request(url=login, data=tosend_json, headers=header, method="POST")

        try:
            with urlopen(req, timeout=5) as res:
                rawrtrn = res.read().decode("utf8")
        except Exception as err:
            _LOGGER.warning("Unable to log in to Gracenote: %s", err)
            return False

        try:
            result = json.loads(rawrtrn)
            self._token = result["token"]
            properties = result["properties"]
            self._zipcode = properties["2002"]
            self._country = properties["2003"]
            self._lineupId, self._device = properties["2004"].split(":", 1)
        except (KeyError, ValueError, json.JSONDecodeError) as err:
            _LOGGER.warning("Invalid Gracenote login response: %s", err)
            return False
        if self.debug:
            _LOGGER.debug("Received Gracenote login token")
        return True

    def get_data(self):
        if self.debug:
            _LOGGER.debug("Gracenote get_data called")
        if not self.login():
            return
        now = int(time.time())
        guide_params = self.get_guide_params()
        host = self.BASE_URL

        # Only get 1 hour of programming since we only need/want the current program titles
        param = (
            "?time="
            + str(now)
            + "&timespan=1&pref=-&"
            + urlencode(guide_params)
            + "&TMSID=&FromPage=TV%20Grid&ActivityID=1&OVDID=&isOverride=true"
        )
        url = host + "api/grid" + param
        if self.debug:
            _LOGGER.debug("Gracenote grid URL: %s", url)

        header = {"X-Requested-With": "XMLHttpRequest"}

        req = Request(url=url, headers=header, method="GET")

        try:
            with urlopen(req, timeout=5) as res:
                self._raw = res.read().decode("utf8")
            self._zapraw = json.loads(self._raw)
        except Exception as err:
            _LOGGER.warning("Unable to download Gracenote listings: %s", err)
            return

        if self.debug:
            _LOGGER.debug("Downloaded %d bytes of Gracenote guide data", len(self._raw))

        self.get_channels()
        self.get_titles()

    def get_channels(self):
        # Decode basic channel number to channel name from Gracenote data.
        if self.debug:
            _LOGGER.info("Gracenote get_channels called")
        self._channels = {}
        self._logos = {}
        for channelData in self._zapraw["channels"]:
            # Pad channel numbers to 4 chars to match values from Tivo device
            _ch = channelData["channelNo"].zfill(4)
            self._channels[_ch] = channelData["callSign"]
            logo = channelData.get("thumbnail")
            if logo:
                self._logos[_ch] = self.image_url(logo)

    def get_titles(self):
        # Decode program titles from Gracenote data.
        if self.debug:
            _LOGGER.info("Gracenote get_titles called")
        self._titles = {}
        self._images = {}
        # self._start  = {}
        # self._end    = {}

        for channelData in self._zapraw["channels"]:
            _ch = channelData["channelNo"].zfill(4)
            _ev = channelData["events"]

            if not _ev:
                if self.debug:
                    _LOGGER.warning("No events found for channel:  %s", _ch)
                continue

            tmp = _ev[0]
            prog = tmp["program"]

            start_utc = time.strptime(tmp["startTime"], "%Y-%m-%dT%H:%M:%SZ")
            start_time = timegm(start_utc)
            #            starthm    = time.strftime("%H:%M",  timezone('US/Central').localize(start_utc))

            end_utc = time.strptime(tmp["endTime"], "%Y-%m-%dT%H:%M:%SZ")
            end_time = timegm(end_utc)
            #            #endhm   = time.strftime("%H:%M", end_utc)
            #            endhm   = time.strftime("%H:%M", timezone('US/Central').localize(end_utc))
            #
            #            pgmtime = ' (' + starthm + ' - ' + endhm + ')'

            thumbnail = tmp.get("thumbnail")
            image = self.image_url(thumbnail) if thumbnail else None

            now = int(time.time())
            if start_time < now < end_time:
                title = prog["title"]
                self._titles[_ch] = title
                if image:
                    self._images[_ch] = image
                # + pgmtime

    def image_url(self, image):
        """Return an absolute HTTPS URL for either Gracenote response format."""
        if not image:
            return self.NO_IMAGE_URL
        if image.startswith("//"):
            return "https:" + image
        if image.startswith("http://"):
            return "https://" + image.removeprefix("http://")
        if image.startswith("https://"):
            return image
        if "/" in image or image.endswith((".jpg", ".jpeg", ".png")):
            return self.IMAGE_URL + image.lstrip("/")
        return self.IMAGE_URL + "assets/" + image + ".jpg?w=360&h=480"

    def get_guide_params(self):
        zparams = {}

        self._postalcode = self._zipcode
        zparams["token"] = self._token

        zparams["lineupId"] = self._country + "-" + self._lineupId + "-DEFAULT"
        zparams["headendId"] = self._lineupId
        zparams["device"] = self._device or "X"
        zparams["postalCode"] = self._postalcode
        zparams["country"] = self._country
        zparams["aid"] = "orbebb"

        return zparams
