# homeassistant-tivo
TiVo media-player platform for current Home Assistant releases.

Based on ideas from the following sites:

```
https://community.home-assistant.io/t/control-tivo-box-over-telnet/12430/65
https://www.tivocommunity.com/community/index.php?threads/tivo-ui-control-via-telnet-no-hacking-required.392385/
https://community.home-assistant.io/t/tivo-media-player-component/851
https://charliemeyer.net/2012/12/04/remote-control-of-a-tivo-from-the-linux-command-line/
```

Working functions:
```
1. Channel up and down - uses previous and next track buttons
2. Power buttons
3. FWD and REV
4. PLAY and PAUSE
5. Retrieval of the current program title and image from Gracenote TV Listings
```

Available but not integrated into gui, etc:
```
1. Open guide, tivo menu, live tv, now playing
```

Copy the tivo folder to your CONFIG_DIR/custom_components/ directory.  This should now look like:
```
CONFIG_DIR/custom_components/tivo/media_player.py
```

It requires the following configuration:

```
media_player:
  - platform: tivo
    host: 192.168.0.22
    name: Tivo
    port: 31339
    device: 0
    debug: 0
#    gracenote_lineup_id: USA-OTA90210-DEFAULT  # example; replace with yours
#    gracenote_postal_code: "90210"
#    gracenote_country: USA
```
1. Set `debug: true` for additional logging.
2. Omit the Gracenote settings if you do not want guide metadata. The default
   guide path uses Gracenote's public listings grid and does not require an API
   license. Open `https://tvlistings.gracenote.com/grid-affiliates.html?aid=orbebb`,
   select your location and provider, and copy the `lineupId` from the grid URL.
3. If you already have a licensed Gracenote Video API key, add
   `gracenote_api_key`; the integration will then use the supported API instead.
4. Consumer-site credentials can still be supplied as `gracenote_username` and
   `gracenote_password`. Existing `zapuser` and `zappass` keys remain accepted
   for compatibility, but the direct public-grid configuration is preferred.

`host` is required. The repository's old Zeroconf path was incomplete and used
an API that is no longer compatible with current `python-zeroconf`.

This works by opening a socket connection to the Tivo device on its default port 31339.  Then using the following protocol, it can perform several commands:

https://www.tivo.com/assets/images/abouttivo/resources/downloads/brochures/TiVo_TCP_Network_Remote_Control_Protocol.pdf

It reads the response and parses that information to determine status. Simply
connecting without sending a command responds with status such as:

```
CH_STATUS 0613 LOCAL
```

This means channel status, channel 613, and channel was set by the remote.  If we set the channel, it should say REMOTE instead of LOCAL, or RECORDING if a recording is in process.

Goals:

```
1. Start recording, end recording
2. switch and possibly navigate screens

The protocol should be capable of the above but it is unclear to me how to connect that to hass.
```

Issues:

```
1. Socket timeout occurs when connecting to a Tivo which is currently playing a recording,
    etc. (i.e. not in LiveTV mode)  This should be captured now but requires further testing...
```

More to come...
