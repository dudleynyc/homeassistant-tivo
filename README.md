# TiVo for Home Assistant

A custom Home Assistant media-player integration for controlling TiVo DVRs and
TiVo Mini boxes over the local network. It can also add current-program titles
and artwork using Gracenote TV Listings.

This is a legacy YAML platform modernized for current Home Assistant and Python
releases. It is not an official Home Assistant or TiVo integration.

## Features

- Power, play, pause, and stop controls
- Channel up and down while watching Live TV
- Fast-forward and rewind during recorded playback
- Current channel, callsign, and program title
- Current-program artwork with the channel logo as a fallback
- TiVo DVR and TiVo Mini support through the TiVo network remote-control
  protocol
- Public Gracenote guide lookup without a paid API license

Home Assistant's standard media-player card does not have separate channel and
fast-forward/rewind buttons. The previous and next buttons are therefore
context-sensitive:

- Live TV: previous/next changes the channel down/up.
- Recorded playback: previous/next sends rewind/fast-forward.

## Before installing

Enable **Network Remote Control** on every TiVo you want to add. The exact menu
path varies by TiVo Experience version, but it is normally under **Settings >
Remote & Devices** or **Settings > Remote, CableCARD & Devices**.

Each TiVo needs its own IP address or resolvable hostname in the configuration.
The integration does not discover devices through Bonjour. A DHCP reservation
is recommended so each address remains stable.

This integration works with TiVo DVRs and TiVo Mini, Mini VOX, and Mini LUX
boxes that support TiVo's network remote-control protocol. It does not support
the Android-based TiVo Stream 4K.

## Install with HACS

This repository is not in the default HACS catalog, so add it as a custom
repository:

1. Open **HACS > Integrations** in Home Assistant.
2. Open the menu in the upper-right corner and choose **Custom repositories**.
3. Enter `https://github.com/dudleynyc/homeassistant-tivo`.
4. Select **Integration** as the category and add the repository.
5. Find **TiVo**, choose **Download**, and restart Home Assistant.

To install an update later, download the new version from HACS and restart Home
Assistant again.

## Manual installation

Copy `custom_components/tivo` from this repository into your Home Assistant
configuration directory so the files are located at:

```text
CONFIG_DIR/custom_components/tivo/
```

Restart Home Assistant after copying or updating the files.

## Configure a TiVo

Add one entry to `configuration.yaml` for each TiVo:

```yaml
media_player:
  - platform: tivo
    host: 192.168.1.101
    name: Living Room TiVo
    unique_id: living_room_tivo
    port: 31339
    debug: false
    gracenote_lineup_id: YOUR_LINEUP_ID
    gracenote_postal_code: "YOUR_ZIP_CODE"
    gracenote_country: USA

  - platform: tivo
    host: 192.168.1.102
    name: Bedroom TiVo Mini
    unique_id: bedroom_tivo_mini
    port: 31339
    debug: false
    gracenote_lineup_id: YOUR_LINEUP_ID
    gracenote_postal_code: "YOUR_ZIP_CODE"
    gracenote_country: USA
```

Restart Home Assistant after changing the YAML configuration.

### Configuration options

| Option | Required | Default | Description |
| --- | --- | --- | --- |
| `host` | Yes | — | TiVo IP address or resolvable hostname. |
| `name` | No | `Tivo Receiver` | Name displayed in Home Assistant. |
| `unique_id` | No | Generated from `host` | Stable ID used by Home Assistant's entity registry. An explicit value is recommended. |
| `port` | No | `31339` | TiVo network remote-control port. |
| `debug` | No | `false` | Enables additional integration logging. |
| `gracenote_lineup_id` | No | — | Gracenote lineup used for guide metadata. |
| `gracenote_postal_code` | With guide data | — | Postal or ZIP code associated with the lineup. Quote numeric ZIP codes. |
| `gracenote_country` | No | Inferred from lineup | Three-letter country code such as `USA` or `CAN`. |
| `gracenote_api_key` | No | — | Licensed Gracenote Video API key, if you have one. |

You may omit all `gracenote_*` settings if you only want remote control and do
not want guide metadata.

## Find your Gracenote lineup ID

No Gracenote account or paid API license is needed for the public guide method.

### Provider lookup method

Open the following URL, replacing `<ZIP_CODE>` with your ZIP code:

```text
https://tvlistings.gracenote.com/gapzap_webapi/api/Providers/getPostalCodeProviders/USA/<ZIP_CODE>/gapzap/en
```

The page returns JSON containing the available television providers. Find the
entry matching your provider and location, then copy its `lineupId` value. A
cable provider entry will look similar to this:

```json
{
  "name": "Provider - Digital",
  "location": "Your city",
  "lineupId": "USA-HEADEND-X",
  "postalCode": "ZIP_CODE"
}
```

Use that value as `gracenote_lineup_id` and the matching postal code as
`gracenote_postal_code`.

### Listings-page method

Alternatively, open the [Gracenote TV listings
page](https://tvlistings.gracenote.com/grid-affiliates.html?aid=orbebb), choose
your location and provider, and inspect the resulting grid URL for its
`lineupId` value.

Consumer-site usernames and passwords are not licensed Gracenote API keys and
are not required for the public guide method. The legacy `gracenote_username`,
`gracenote_password`, `zapuser`, and `zappass` settings remain accepted only for
compatibility with older configurations.

## Behavior and limitations

- TiVo's protocol reports the current channel during Live TV but does not
  reliably report playback state or channel status while playing recordings or
  displaying menus.
- Play and pause state is tracked for commands sent through Home Assistant.
  Using the physical remote may not immediately update the Home Assistant icon.
- During recorded playback, the entity remains online even when the TiVo sends
  no channel-status response.
- Gracenote's public listings service is undocumented and could change without
  notice.
- Home Assistant exposes one primary media image. The integration prefers
  current-program artwork, falls back to the channel logo, and finally uses a
  generic placeholder.

## Troubleshooting

If a TiVo is unavailable or does not respond:

1. Confirm **Network Remote Control** is enabled on that TiVo.
2. Confirm the configured `host` is still the TiVo's current address.
3. Confirm Home Assistant can reach TCP port `31339` on the TiVo.
4. Check that Home Assistant and the TiVo are on networks allowed to communicate
   with each other.
5. Set `debug: true`, restart Home Assistant, and inspect the logs for entries
   from `custom_components.tivo`.

If guide titles or images are missing, verify the lineup ID and postal code by
opening the provider lookup URL above.

## Protocol and acknowledgements

The integration connects locally on TCP port `31339` and implements TiVo TCP
Remote Protocol version 1.1. TiVo's original download is no longer available,
but a [reader-hosted copy is available on
Yumpu](https://www.yumpu.com/en/document/view/42026982/tcp-remote-protocol-version-11-tivo).
The PDF is not bundled with this repository because its copyright notice
prohibits reproduction without written permission.

It builds on work and discussion from:

- [Home Assistant community: Control TiVo box over
  telnet](https://community.home-assistant.io/t/control-tivo-box-over-telnet/12430/65)
- [Home Assistant community: TiVo media-player
  component](https://community.home-assistant.io/t/tivo-media-player-component/851)
- [TiVo Community: UI control via
  telnet](https://www.tivocommunity.com/community/index.php?threads/tivo-ui-control-via-telnet-no-hacking-required.392385/)
- [Charlie Meyer: Remote control of a TiVo from the Linux command
  line](https://charliemeyer.net/2012/12/04/remote-control-of-a-tivo-from-the-linux-command-line/)
