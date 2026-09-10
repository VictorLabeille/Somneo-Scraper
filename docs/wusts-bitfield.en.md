# `wusts` is a bitfield — measurements

Frozen extract, 2026-09-09. Written for [pysomneo](https://github.com/theneweinstein/pysomneo)
and its Home Assistant integration. The living reference for this project is
[`somneo-api.md`](somneo-api.md), in French; this page will not be updated.

**Device:** Philips Somneo HF3671/01, `swverwifi` 2.2.5, firmware `pkgver` 30406, reached over
the local HTTPS API from a LAN host. Every number below was produced by a probe in
[`probes/`](../probes) and its raw output is in [`probes/results/`](../probes/results).

## The bits

`wusts` is not a state id. Each bit carries one independent fact, and they combine. Four are
confirmed by the Philips app's own `StatusProperties` tests:

```
isStandBy()      = bit11 == 0 && bit0 == 1
isUserMenu()     = bit11 == 0 && bit1 == 1
isAlarmActive()  = bit2 == 1  || bit11 == 1
isAlarmSnoozed() = bit4 == 1  && bit11 == 1
```

| Bit | Meaning | Source |
| --- | --- | --- |
| 0 | device idle — no light used for the last ~30 s | measured |
| 1 | set during the transient that follows switching a light off | measured |
| 2 | alarm active | app code |
| 3 | sunset | measured |
| 4 | snooze | app code |
| 6 | RelaxBreathing — appears in no published list | measured |
| 8 | light emitted (lamp, night light or sunset) | measured |
| 9 | audio source engaged | measured |
| 11 | device active | app code |

**Composition is what proves the model.** The audio player alone reads `513`; switching the
reading lamp on top of it reads `769` — that is `513 + 256`, bit 8 added, nothing else moved.
Two independent actions, two independent bits.

## What ordinary use produces

| State | `wusts` | Bits | Trials | In `STATUS` |
| --- | --- | --- | --- | --- |
| Idle | 1 | 0 | — | `off` |
| Transient after the reading lamp or a sunset is switched off | **2** | 1 | 12 | **`sunset` — wrong** |
| Reading lamp or night light, device idle | 257 | 0, 8 | 21 | `light-on` |
| Reading lamp switched on within 30 s of the last one | **258** | 1, 8 | 8 | **missing → `unknown`** |
| Sunset, sound off, device woken by a light | **264** | 3, 8 | 16 | **missing → `unknown`** |
| Sunset, sound off, device idle | **265** | 0, 3, 8 | 20 | **missing → `unknown`** |
| RelaxBreathing | **320** | 6, 8 | 16 | **missing → `unknown`** |
| Audio player, aux or FM | **513** | 0, 9 | 9 | **missing → `unknown`** |
| Audio player + reading lamp | **769** | 0, 8, 9 | 3 | **missing → `unknown`** |
| Sunset with sound | 776 / 777 | 3, 8, 9 (+0) | 7 / 8 | `sunset` |
| Alarm, dawn phase | 2309 | 0, 2, 8, 11 | 3 alarms | `wake-up` |
| Alarm, sound phase | 2817 | 0, 8, 9, 11 | 3 alarms | `on` |
| Snooze | 2321 | 0, 4, 8, 11 | never here | `snooze` |

`776` is exactly `264 + 512`, and `777` is `265 + 512`. The sunset on this device is silent
(`wudsk.snddv` is `off`), which is why it reads `264` or `265` where the table only knows the
values with sound. Same state, one setting apart — not a quirk of this unit.

## Two values for one sunset

Bit 0 tracks how long the device has been left alone. It clears as soon as any light is used,
and comes back within 30 seconds:

| Delay since the lamp was switched off | 15 s | 30 s | 60 s | 90 s | 120 s | 5 min |
| --- | --- | --- | --- | --- | --- | --- |
| Sunset reads | **264** | 265 | 265 | 265 | 265 | 265 |

Both values therefore occur in normal use, thirty seconds apart. A value table has to carry
both, or neither. The same rule applies to the reading lamp: `257` when the device is idle,
`258` when it is switched on right after another light — which is why an ordinary lamp can
report `unknown` today.

## `2` is not a sunset

Sampled every 0.5 s, `2` is held for **at least 7.46 s** — three consecutive runs agreed to
within 10 ms — and then gives way to `1`. It follows the reading lamp and the sunset alike, and
**never** the night light: 0 of 18 switch-offs on 2026-09-10 — four variants of network write
and the device's own button — against 5 of 5 for the lamp in the same session. What it actually represents is unknown — these measurements say when it appears and how
long it lasts, nothing more.

It reached the table one day after `776`, which is what someone sees who starts a sunset, stops
it and reads `wusts` again.

## Checked inside `pysomneo`, not only on the device

The `ai-improvements` branch was run against a real sunset with both tables:

| `STATUS` | `wusts` read | `somneo_status` published |
| --- | --- | --- |
| current | 265 | `unknown` |
| with `2` renamed, `264` and `265` added | 265 | `sunset` |

An earlier attempt adding only `264` was measured too, and left `somneo_status` at `unknown` —
which is why the fix carries both values.

## Not explained

`2321` (snooze) has never been reproduced here; it needs a real alarm followed by a snooze
press. What `2` designates is unknown. Bit 1 is annotated "user menu" in the app code, and
nothing here confirms or contradicts it — the display setting `dspon` leaves `wusts` untouched,
on its own and with the lamp on.

## Replaying this

Probes are standard-library Python and take the device address from SSDP discovery. Reads are
safe; the ones that write restore the device afterwards and never touch alarms.

| Probe | Answers |
| --- | --- |
| [`etats_wusts.py`](../probes/etats_wusts.py) | the values of ordinary states, repeated |
| [`veilleuse.py`](../probes/veilleuse.py) | night light against reading lamp |
| [`extinction_veilleuse.py`](../probes/extinction_veilleuse.py) | whether `2` follows the night light |
| [`contexte_wusts.py`](../probes/contexte_wusts.py) | bit 0 against the starting state |
| [`wusts_exhaustif.py`](../probes/wusts_exhaustif.py) | light levels, display, sound, RelaxBreathing |
| [`lecteur_sources.py`](../probes/lecteur_sources.py) | audio player, and composition with the lamp |
| [`prealable_bit0.py`](../probes/prealable_bit0.py) | what clears bit 0 |
| [`valide_correctif.py`](../probes/valide_correctif.py) | the fix, run inside `pysomneo` |

One caveat worth repeating: the device serves **a single TLS connection**. Two clients at once
and both fail. Stop anything else polling it before running a probe.
