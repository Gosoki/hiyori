"""Backup 地震情報 source: the 気象庁 (JMA) XML feed.

P2P地震情報 is a single point of failure for the only feature in this dashboard
that can actually matter, so when its WebSocket goes quiet we fall back to JMA's
own publication feed and keep the quake screen alive.

What this is NOT: a replacement for P2P. JMA publishes by *polling* Atom feed, so
reports land up to a minute late, and the public feed carries no 緊急地震速報 —
during a P2P outage there is no EEW, only 地震情報 after the fact. main.py only
runs this while P2P is down, and the frontend flags the degraded state.

Reports are normalized into exactly the shape earthquake.normalize_quake produces,
including P2P's "YYYY/MM/DD HH:MM:SS" originTime, so quake_key() de-duplicates the
same quake across the two sources.
"""
import datetime
import re
import xml.etree.ElementTree as ET

import httpx

from condget import get_if_changed
from earthquake import ISSUE_LABEL, scale_label

UA = {"User-Agent": "hiyori/1.0"}

ATOM = "{http://www.w3.org/2005/Atom}"
JMX = "{http://xml.kishou.go.jp/jmaxml1/}"
HEAD = "{http://xml.kishou.go.jp/jmaxml1/informationBasis1/}"
BODY = "{http://xml.kishou.go.jp/jmaxml1/body/seismology1/}"
EB = "{http://xml.kishou.go.jp/jmaxml1/elementBasis1/}"

# 震源・震度に関する情報 — the full report (hypocentre + per-prefecture intensities),
# the JMA equivalent of the P2P 551 we already render. The intensity-only 震度速報
# (VXSE51) and hypocentre-only 震源に関する情報 (VXSE52) are skipped: each carries
# half the screen's fields, and VXSE53 follows within a couple of minutes anyway.
REPORT_KIND = "VXSE53"

# JMA writes 震度 as 1..4, 5-, 5+, 6-, 6+, 7; P2P uses the numeric scale codes the
# rest of the app (colours, thresholds, badges) is built on. Translate on the way in.
JMA_INTENSITY = {"1": 10, "2": 20, "3": 30, "4": 40, "5-": 45,
                 "5+": 50, "6-": 55, "6+": 60, "7": 70}

# 固定付加文 codes on a 地震情報; anything else leaves the tsunami row hidden.
TSUNAMI_BY_TEXT = [
    ("大津波警報", "MajorWarning"),
    ("津波警報", "Warning"),
    ("津波注意報", "Watch"),
    ("津波の心配はありません", "None"),
    ("若干の海面変動", "NonEffective"),
    ("調査中", "Checking"),
]


def _intensity_scale(text):
    return JMA_INTENSITY.get((text or "").strip(), -1)


def _origin_time(iso):
    """JMA's '2026-08-05T18:06:00+09:00' -> P2P's '2026/08/05 18:06:00'.

    Matching P2P's spelling exactly is what lets quake_key() recognise a quake we
    may already have seen from the other source — see _quake_time for which of
    JMA's two timestamps that actually is.
    """
    try:
        dt = datetime.datetime.fromisoformat((iso or "").strip())
    except ValueError:
        return ""
    return dt.strftime("%Y/%m/%d %H:%M:%S")


def _quake_time(quake):
    """The timestamp P2P calls `earthquake.time`, which is JMA's **ArrivalTime**.

    Verified against a report published by both sources: JMA gave OriginTime
    18:05:00 and ArrivalTime 18:06:00 for a quake P2P timestamped 18:06:00 — and
    JMA's own headline says "１８時０６分ころ". Keying off OriginTime instead would
    put the same quake in the 🗾 list twice whenever the two sources overlap.
    """
    if quake is None:
        return ""
    return (_origin_time(quake.findtext(BODY + "ArrivalTime"))
            or _origin_time(quake.findtext(BODY + "OriginTime")))


def _coordinate(text):
    """jmx_eb:Coordinate '+35.7+139.7-120000/' -> (lat, lon, depth_km).

    Latitude and longitude are signed decimals; the third component is depth in
    METRES below sea level (so negative), and may be absent for a shallow report.
    """
    m = re.match(r"([+-]\d+(?:\.\d+)?)([+-]\d+(?:\.\d+)?)(?:([+-]\d+(?:\.\d+)?))?", (text or "").strip())
    if not m:
        return -200, -200, -1
    lat, lon = float(m.group(1)), float(m.group(2))
    depth = -1 if m.group(3) is None else abs(float(m.group(3))) / 1000.0
    return lat, lon, int(depth) if depth >= 0 else -1


def _tsunami(body):
    for c in body.iter(BODY + "ForecastComment"):
        text = "".join(c.itertext())
        for needle, value in TSUNAMI_BY_TEXT:
            if needle in text:
                return value
    return "Unknown"


def parse_report(xml_bytes):
    """One VXSE53 document -> an event in earthquake.normalize_quake's shape.

    Returns None for drills, test transmissions and anything that isn't a usable
    地震情報 — the caller treats None as "nothing to publish", not as an error.
    """
    root = ET.fromstring(xml_bytes)
    if (root.findtext(JMX + "Control/" + JMX + "Status") or "").strip() != "通常":
        return None                       # 訓練 / 試験 — never put a drill on the wall

    head = root.find(HEAD + "Head")
    body = root.find(BODY + "Body")
    if head is None or body is None:
        return None
    info_type = (head.findtext(HEAD + "InfoType") or "").strip()

    quake = body.find(BODY + "Earthquake")
    hypo_area = quake.find(BODY + "Hypocenter/" + BODY + "Area") if quake is not None else None
    lat = lon = -200
    depth = -1
    if hypo_area is not None:
        lat, lon, depth = _coordinate(hypo_area.findtext(EB + "Coordinate"))

    magnitude = -1
    if quake is not None:
        try:
            magnitude = float(quake.findtext(EB + "Magnitude"))
        except (TypeError, ValueError):
            magnitude = -1

    # Per-prefecture intensities, same grouping the P2P path produces.
    regions, max_scale = [], -1
    obs = body.find(BODY + "Intensity/" + BODY + "Observation")
    if obs is not None:
        max_scale = _intensity_scale(obs.findtext(BODY + "MaxInt"))
        for pref in obs.findall(BODY + "Pref"):
            scale = _intensity_scale(pref.findtext(BODY + "MaxInt"))
            name = (pref.findtext(BODY + "Name") or "").strip()
            if name and scale > 0:
                regions.append({"name": name, "scale": scale, "label": scale_label(scale)})
    regions.sort(key=lambda r: r["scale"], reverse=True)

    return {
        "kind": "quake",
        "id": (head.findtext(HEAD + "EventID") or "").strip(),
        "bulletin": (head.findtext(HEAD + "Serial") or "").strip(),
        "issueLabel": ISSUE_LABEL["DetailScale"],
        "originTime": _quake_time(quake),
        "hypocenter": {
            "name": ((hypo_area.findtext(BODY + "Name") if hypo_area is not None else "") or "").strip() or "調査中",
            "depth": depth,
            "magnitude": magnitude,
            "latitude": lat,
            "longitude": lon,
        },
        "maxScale": max_scale,
        "maxIntensity": scale_label(max_scale),
        "tsunami": _tsunami(body),
        "regions": regions,
        "cancelled": info_type == "取消",
        "source": "jma",                  # so /api/health and the UI can say where it came from
    }


async def fetch_reports(feed_url, seen, limit=3, cond=None):
    """Newest 地震情報 from JMA's feed that aren't in `seen` (a set of report URLs).

    `seen` is updated in place. Returns oldest-first so callers can publish them in
    the order they happened. Feed and report fetches are independent: one unreadable
    report is skipped rather than losing the rest.

    `cond` (optional dict) enables conditional GET on the feed: JMA republishes
    eqvol.xml every few minutes at most, and it is ~500 KB, so while we poll it
    every minute most polls come back as a 304 and cost nothing.
    """
    out = []
    async with httpx.AsyncClient(timeout=20, headers=UA, follow_redirects=True) as client:
        if cond is None:
            r = await client.get(feed_url)
            r.raise_for_status()
        else:
            r = await get_if_changed(client, feed_url, cond)
            if r is None:                 # 304: nothing new since the last poll
                return []
        feed = ET.fromstring(r.content)

        links = [e.find(ATOM + "link").get("href")
                 for e in feed.findall(ATOM + "entry")
                 if e.find(ATOM + "link") is not None]
        fresh = [u for u in links if u and REPORT_KIND in u and u not in seen][:limit]

        # First run: record what is already published without replaying it as news —
        # otherwise a fallback that kicks in at 3am would take the screen over for a
        # quake that happened hours ago.
        priming = not seen
        # Everything except the reports we are about to download counts as seen.
        # The fresh ones are added only once their download succeeded: a 503 on the
        # one report that matters would otherwise mark it seen and never retry —
        # a silently lost alert during the very outage the fallback exists for.
        seen.update(u for u in links if u not in fresh)
        # Forget URLs that have scrolled off the feed: they can never come back as
        # "fresh", and without this the set grows for as long as the fallback runs.
        # (Only when the feed actually listed something — an empty feed must not
        # empty `seen` and turn the next poll into a replay-everything priming pass.)
        if links:
            seen.intersection_update(links)
        if priming:
            seen.update(fresh)
            return []

        for url in reversed(fresh):       # oldest first
            try:
                rep = await client.get(url)
                rep.raise_for_status()
            except Exception:
                continue                  # left unseen on purpose: the next poll retries it
            seen.add(url)                 # downloaded — a parse failure is deterministic, don't retry
            try:
                event = parse_report(rep.content)
            except Exception:
                continue                  # one bad report must not lose the others
            if event and event["originTime"]:
                out.append(event)
    return out
