[Reading 360 lines from start (total: 360 lines, 0 remaining)]

#!/usr/bin/env python3
import difflib
import hashlib
import json
import os
import re
import shutil
import tempfile
import urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict

BASE = os.path.dirname(os.path.abspath(__file__))
CHANNELS_FILE = os.path.join(BASE, "channels.json")
OUT_FILE = os.path.join(BASE, "guide.xml")
REPORT_FILE = os.path.join(BASE, "matches.json")
SOURCE_URL = "https://epg.iptv.cat/epg.xml"

NUMBER_WORDS = {
    "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8",
    "nine": "9", "ten": "10"
}

SPECIAL_ALIASES = {
    "Channel 9 Australia • Sydney": [
        "Channel Nine Sydney", "Channel 9 Sydney", "Nine Sydney", "9 Sydney", "9HD Sydney"
    ],
    "Channel 9 Australia • 9Now Backup": [
        "Channel Nine Sydney", "Channel 9 Sydney", "Nine Sydney", "9 Sydney", "9HD Sydney"
    ],
}

def canonical(text):
    s = (text or "").lower().strip()
    s = s.replace("&", " and ")
    s = s.replace("+1", " plus 1 ").replace("+", " plus ")
    s = s.replace("•", " ")
    s = re.sub(r"\([^)]*\)", " ", s)
    s = re.sub(r"^\s*(ukfhd|ukhd|uksd|uk|us|usa|au)\s*[:| -]+\s*", "", s)
    s = re.sub(r"\b(uhd|fhd|full\s*hd|hd|sd|hevc|4k|50\s*fps|50fps|backup|new)\b", " ", s)
    s = re.sub(r"\bfhd\s+", " ", s)
    s = re.sub(r"\bsky\s+sport\b", "sky sports", s)
    s = re.sub(r"\btnt\s+sport\b", "tnt sports", s)
    s = re.sub(r"\bpremier\s+sport\b", "premier sports", s)
    s = re.sub(r"\bnat\s+geo\b", "national geographic", s)
    s = re.sub(r"\bitvbe\b", "itv be", s)
    s = re.sub(r"\bitv\s*1\b", "itv 1", s)
    s = re.sub(r"\b5\s*star\b", "5star", s)
    s = re.sub(r"\b5\s*usa\b", "5usa", s)
    s = re.sub(r"\b5\s*action\b", "5action", s)
    s = re.sub(r"\bgreat!\b", "great", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    words = [NUMBER_WORDS.get(w, w) for w in s.split()]
    s = " ".join(words)
    if s == "itv":
        s = "itv 1"
    s = re.sub(r"^bbc\s+0*([1-4])$", r"bbc \1", s)
    return re.sub(r"\s+", " ", s).strip()

def compact(text):
    return re.sub(r"[^a-z0-9]+", "", canonical(text))

def similarity(a, b):
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    ca, cb = compact(a), compact(b)
    if ca and ca == cb:
        return 0.995
    ratio = difflib.SequenceMatcher(None, a, b).ratio()
    sa, sb = set(a.split()), set(b.split())
    jac = len(sa & sb) / len(sa | sb) if (sa | sb) else 0.0
    contains = 0.0
    if len(a) >= 4 and len(b) >= 4 and (a in b or b in a):
        shorter = min(len(a), len(b))
        longer = max(len(a), len(b))
        contains = 0.90 * (shorter / longer) + 0.08
    return max(ratio * 0.72 + jac * 0.28, contains)

def wanted_country(group):
    if group.startswith(("01 ", "02 ", "03 ", "04 ", "05 ", "06 ")):
        return "uk"
    if group.startswith("07 "):
        return "us"
    if group.startswith("08 "):
        return "au"
    return ""

def source_country(cid):
    if "." in cid:
        return cid.rsplit(".", 1)[-1].lower()
    return ""

def download(url, path):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 Plex-EPG-Builder"})
    with urllib.request.urlopen(req, timeout=180) as r, open(path, "wb") as f:
        shutil.copyfileobj(r, f, length=1024 * 1024)

def read_source_channels(path):
    out = {}
    for event, elem in ET.iterparse(path, events=("end",)):
        if elem.tag.endswith("channel"):
            cid = elem.attrib.get("id", "")
            if cid:
                names = []
                icon = ""
                for child in list(elem):
                    tag = child.tag.rsplit("}", 1)[-1]
                    if tag == "display-name" and child.text:
                        names.append(child.text.strip())
                    elif tag == "icon" and not icon:
                        icon = child.attrib.get("src", "")
                if names:
                    out[cid] = {"names": names, "icon": icon}
            elem.clear()
    return out

def choose_match(wanted, source):
    desired_names = [wanted["name"]] + SPECIAL_ALIASES.get(wanted["name"], [])
    desired_forms = [canonical(x) for x in desired_names]
    country = wanted_country(wanted["group"])
    hint = (wanted.get("hint_id") or "").strip()

    if hint in source:
        best_hint = max(
            similarity(desired, canonical(n))
            for desired in desired_forms
            for n in source[hint]["names"]
        )
        if best_hint >= 0.76:
            return hint, best_hint + 0.05, "validated-hint"

    scored = []
    for cid, info in source.items():
        ctry = source_country(cid)
        best = 0.0
        for desired in desired_forms:
            desired_tokens = set(desired.split())
            for display in info["names"]:
                best = max(best, similarity(desired, canonical(display)))
            id_stem = cid.rsplit(".", 1)[0]
            best = max(best, similarity(desired, canonical(id_stem)) * 0.94)

            names_canon = [canonical(n) for n in info["names"]]
            overlap = any(desired_tokens & set(n.split()) for n in names_canon)
            if not overlap and compact(desired) not in {compact(n) for n in names_canon}:
                best -= 0.12

        if country:
            if ctry == country:
                best += 0.045
            elif ctry in {"uk", "us", "au", "ca", "ie", "nz"}:
                best -= 0.10

        scored.append((best, cid))

    scored.sort(reverse=True)
    if not scored:
        return None
    best_score, best_id = scored[0]
    second = scored[1][0] if len(scored) > 1 else 0.0

    if best_score >= 0.985:
        return best_id, best_score, "name-exact"
    if best_score >= 0.82 and (best_score - second >= 0.035):
        return best_id, best_score, "name-fuzzy"
    return None

def _clean_text(text):
    text = re.sub(r"<[^>]+>", " ", text or "")
    return re.sub(r"\s+", " ", text).strip()


def load_block_episodes():
    """Fetch real The Block season/episode metadata from TVMaze."""
    url = "https://api.tvmaze.com/singlesearch/shows?q=The%20Block&embed=episodes"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 Plex-EPG-Builder"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
        episodes = []
        for e in data.get("_embedded", {}).get("episodes", []):
            season, number = e.get("season"), e.get("number")
            if not season or not number:
                continue
            summary = _clean_text(e.get("summary") or "")
            episodes.append({
                "season": int(season),
                "number": int(number),
                "name": _clean_text(e.get("name") or ""),
                "airdate": e.get("airdate") or "",
                "summary": summary,
                "summary_key": canonical(summary),
            })
        print(f"Loaded {len(episodes)} The Block episodes from TVMaze.")
        return episodes
    except Exception as exc:
        print(f"Warning: could not load The Block episode metadata: {exc}")
        return []


def match_block_episode(elem, episodes):
    desc = next((c.text or "" for c in elem if c.tag.rsplit("}", 1)[-1] == "desc"), "").strip()
    desc_key = canonical(_clean_text(desc))
    if desc_key:
        exact = [e for e in episodes if e["summary_key"] and e["summary_key"] == desc_key]
        if len(exact) == 1:
            return exact[0]

        scored = sorted(
            ((similarity(desc_key, e["summary_key"]), e) for e in episodes if e["summary_key"]),
            key=lambda x: x[0], reverse=True
        )
        if scored and scored[0][0] >= 0.94:
            return scored[0][1]

    # Fallback only for Sydney prime-time broadcasts, not daytime repeats.
    start = elem.attrib.get("start", "")
    try:
        from datetime import datetime, timezone
        from zoneinfo import ZoneInfo
        dt = datetime.strptime(start[:19], "%Y%m%d%H%M%S %z").astimezone(ZoneInfo("Australia/Sydney"))
        if 18 <= dt.hour <= 22:
            candidates = [e for e in episodes if e["airdate"] == dt.date().isoformat()]
            if len(candidates) == 1:
                return candidates[0]
    except Exception:
        pass
    return None


def enrich_programme(elem, source_id, block_episodes):
    """Add real episodic metadata Plex needs for The Block series recording."""
    if source_id != "channel9sydney.au":
        return
    title = next((c.text or "" for c in elem if c.tag.rsplit("}", 1)[-1] == "title"), "").strip()
    if title.casefold() != "the block":
        return

    episode = match_block_episode(elem, block_episodes)
    tags = [c.tag.rsplit("}", 1)[-1] for c in elem]

    if episode:
        for child in list(elem):
            if child.tag.rsplit("}", 1)[-1] in {"episode-num", "sub-title"}:
                elem.remove(child)

        subtitle = ET.SubElement(elem, "sub-title")
        subtitle.text = episode["name"]
        onscreen = ET.SubElement(elem, "episode-num", {"system": "onscreen"})
        onscreen.text = f"S{episode['season']:02d}E{episode['number']:02d}"
        xmltv = ET.SubElement(elem, "episode-num", {"system": "xmltv_ns"})
        xmltv.text = f"{episode['season'] - 1}.{episode['number'] - 1}."
    elif "episode-num" not in tags:
        # Fallback if TVMaze is temporarily unavailable: still make it episodic,
        # but avoid pretending a synthetic value is a real season/episode number.
        desc = next((c.text or "" for c in elem if c.tag.rsplit("}", 1)[-1] == "desc"), "").strip()
        key = re.sub(r"\s+", " ", desc).strip().casefold() or elem.attrib.get("start", "")[:8]
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()[:12].upper()
        ep = ET.SubElement(elem, "episode-num", {"system": "onscreen"})
        ep.text = f"ID{(int(digest, 16) % 900000) + 100000:06d}"

    if "category" not in tags:
        cat = ET.SubElement(elem, "category")
        cat.text = "Series"

def main():
    with open(CHANNELS_FILE, "r", encoding="utf-8") as f:
        wanted = json.load(f)

    os.makedirs(BASE, exist_ok=True)
    fd, source_path = tempfile.mkstemp(prefix="plex_epg_", suffix=".xml")
    os.close(fd)
    try:
        print("Downloading source EPG...")
        download(SOURCE_URL, source_path)
        print("Reading EPG channel list...")
        source = read_source_channels(source_path)
        print(f"Source has {len(source):,} channels.")
        block_episodes = load_block_episodes()

        matches = []
        source_to_targets = defaultdict(list)

        for ch in wanted:
            target_id = f"{int(ch['order']):04d}"
            chosen = choose_match(ch, source)
            if chosen:
                src_id, score, method = chosen
                src_display = source[src_id]["names"][0]
                matches.append({
                    "order": ch["order"], "target_id": target_id,
                    "name": ch["name"], "group": ch["group"],
                    "source_id": src_id, "source_name": src_display,
                    "score": round(score, 3), "method": method
                })
                source_to_targets[src_id].append(target_id)
            else:
                matches.append({
                    "order": ch["order"], "target_id": target_id,
                    "name": ch["name"], "group": ch["group"],
                    "source_id": None, "source_name": None,
                    "score": None, "method": "unmatched"
                })

        tmp_out = OUT_FILE + ".tmp"
        with open(tmp_out, "wb") as out:
            out.write(b'<?xml version="1.0" encoding="UTF-8"?>\n')
            out.write(b'<tv generator-info-name="Iain Plex Ordered EPG">\n')

            for ch in wanted:
                tid = f"{int(ch['order']):04d}"
                el = ET.Element("channel", {"id": tid})
                dn = ET.SubElement(el, "display-name")
                dn.text = ch["name"]
                if ch.get("logo"):
                    ET.SubElement(el, "icon", {"src": ch["logo"]})
                out.write(ET.tostring(el, encoding="utf-8"))
                out.write(b"\n")

            print("Copying programme data for matched channels...")
            for event, elem in ET.iterparse(source_path, events=("end",)):
                if elem.tag.endswith("programme"):
                    src_id = elem.attrib.get("channel", "")
                    targets = source_to_targets.get(src_id)
                    if targets:
                        enrich_programme(elem, src_id, block_episodes)
                        original = src_id
                        for tid in targets:
                            elem.attrib["channel"] = tid
                            out.write(ET.tostring(elem, encoding="utf-8"))
                            out.write(b"\n")
                        elem.attrib["channel"] = original
                    elem.clear()

            out.write(b"</tv>\n")

        os.replace(tmp_out, OUT_FILE)
        with open(REPORT_FILE, "w", encoding="utf-8") as f:
            json.dump(matches, f, ensure_ascii=False, indent=2)

        matched = sum(1 for m in matches if m["source_id"])
        print(f"Done: {matched}/{len(matches)} channels matched.")
        print(f"Guide: {OUT_FILE}")
        print(f"Report: {REPORT_FILE}")
        unmatched = [m["name"] for m in matches if not m["source_id"]]
        if unmatched:
            print("Unmatched (kept in guide without programme data):")
            for name in unmatched:
                print(f"  - {name}")
    finally:
        try:
            os.unlink(source_path)
        except FileNotFoundError:
            pass

if __name__ == "__main__":
    main()

[executed on device: app01 (f788a237-cd27-4d86-a7a7-990b55244036)]