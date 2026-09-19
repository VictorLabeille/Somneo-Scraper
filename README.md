# Somneo-Scraper

**Take a connected alarm clock off the internet without losing what it was bought for.**

The Philips Somneo HF3671/01 is a wake-up light with sensors for temperature, humidity, light and
noise. It talks to Philips servers around the clock. This is the local server that makes those
servers unnecessary: it polls the device over its own LAN API, keeps the history the device never
keeps, and relays control to a phone app.

It is the back end of **[SleepMaxxer](https://github.com/VictorLabeille/SleepMaxxer)**, the Android
app that replaces the vendor's SleepMapper.

## The problem this solves

The Somneo exposes a local REST API over HTTPS, discovered by SSDP, entirely independent of the
cloud. No authentication — not for reading, not for writing.

The catch is what took the project apart and rebuilt it: **that API has no memory.** It answers
with the present moment and the night in progress, nothing more. SleepMapper's history charts do
not come from the device at all; they come from the Philips cloud, which the device feeds itself
with one sample every 15 minutes.

So cutting the internet does not merely inconvenience the vendor app — it deletes the history
outright. A local collector is not an optimisation here. It is the only way the data survives
isolation. And by sampling faster than a quarter of an hour, it records more than the original
ever did.

## How it is built

A Radxa Zero sits on the back of the alarm clock, powered from the clock's own USB port. It runs
Armbian from eMMC, and a single-process FastAPI server that holds **one connection at a time** to
the device.

That constraint is not a style choice. The Somneo has roughly 25 KB of free heap and returns
`500 Timeout` under a burst — it is the hardware, not a bug. Two rules enforce the single
connection by construction: only `gateway.py` imports the device library, and uvicorn runs one
worker. The measurements behind it are public, on
[pysomneo issue #8](https://github.com/theneweinstein/pysomneo/issues/8#issuecomment-5604537884):

| Connection pool setting | Worst request |
| --- | --- |
| pool=5, block=False (the library's default) | 17 552 ms |
| pool=1, block=True | 2 645 ms |
| pool=1, block=True, no retries | 936 ms |

Python · FastAPI · SQLite · [`pysomneo`](https://github.com/theneweinstein/pysomneo) 6.0 async ·
SSDP for finding the clock, mDNS for the app finding the collector.

## Mapping the device

The protocol reference in `docs/somneo-api.md` was produced by cross-checking **two independent
sources**: probing the device port by port, and decompiling the vendor app
(`com.philips.src.hss` 3.22.0-rc.1). It covers all 21 ports, the meaning of every JSON field, the
authentication scheme the firmware does not use, and the traps.

Some of what it establishes is a negative result, and those are kept as carefully as the rest.
**The clock cannot be set from the LAN**: direct writes are refused, the time-server setting is
ignored, and the cloud session that does set it is encrypted. The device gains about ten seconds a
day. The collector therefore measures the drift and reports it rather than pretending to fix it.

## Given back upstream

The reverse engineering closed questions that had been open in `pysomneo` since 2022. Three issue
write-ups and three pull requests, all public:

- [#8](https://github.com/theneweinstein/pysomneo/issues/8#issuecomment-5604537884) — request
  timeouts, diagnosed and measured · [PR #26](https://github.com/theneweinstein/pysomneo/pull/26)
- [#13](https://github.com/theneweinstein/pysomneo/issues/13#issuecomment-5575549169) — display
  settings · [PR #27](https://github.com/theneweinstein/pysomneo/pull/27)
- [#16](https://github.com/theneweinstein/pysomneo/issues/16#issuecomment-5589477019) — additional
  settings found · [PR #25](https://github.com/theneweinstein/pysomneo/pull/25)

## Repository map

| Path | What is there |
| --- | --- |
| `collector/` | The server: collection, nights, catch-up, control relay. Its own README covers running and testing it |
| `docs/somneo-api.md` | The device protocol: 21 ports, field semantics, collection strategy, traps |
| `docs/radxa.md` | The board: hardware in service, WiFi, SSH, LED, flashing procedure and its traps |
| `probes/` | The measurement probes and their raw results — how every number here was obtained, and how to replay it |
| `radxa-config/` | Configuration deployed to the board in service |
| `radxa-flash/` | One-off tooling that put Armbian on the eMMC. Finished and archived |
| `AGENTS.md` | Conventions, scope and hard rules for anyone — human or agent — working on this repo |

Documentation is in French; this page is not.

## Licence

**GPL-3.0**, required by `pysomneo`, of which this is a derived work.

## Related

- [SleepMaxxer](https://github.com/VictorLabeille/SleepMaxxer) — the Android app that consumes this
- [`pysomneo`](https://github.com/theneweinstein/pysomneo) — the device library
- [`theneweinstein/somneo`](https://github.com/theneweinstein/somneo) — the Home Assistant
  integration by the same author
- [Radxa-Zero-ULTRA-CASE](https://github.com/RoversX/Radxa-Zero-ULTRA-CASE) — the printed case
