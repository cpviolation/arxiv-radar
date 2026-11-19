#!/usr/bin/env python3
"""
arxiv_radar.py — daily arXiv digest filtered by interests, with a static site.

Standard library only (Python >= 3.11, for tomllib).

Commands:
    python3 arxiv_radar.py run              # fetch, save data/digests/YYYY-MM-DD.json, generate site/
    python3 arxiv_radar.py run --days 7     # custom window
    python3 arxiv_radar.py render           # regenerate the site only, from saved data
    python3 arxiv_radar.py run --from-file feed.xml --no-state   # offline test

Layout:
    config.toml              topics, keywords, categories, site settings
    data/seen.json           IDs already published (avoids repeats from one day to the next)
    data/digests/*.json      one file per day: the source the site is generated from
    site/                    generated site (not versioned, published by GitHub Pages)
"""

import argparse
import html
import json
import re
import shutil
import sys
import time
import tomllib
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
DIGESTS = DATA / "digests"
SEEN = DATA / "seen.json"
SITE = ROOT / "site"
ASSETS = ROOT / "site_assets"

API_URL = "https://export.arxiv.org/api/query"
ATOM = "{http://www.w3.org/2005/Atom}"
ARXIV = "{http://arxiv.org/schemas/atom}"
OPENSEARCH = "{http://a9.com/-/spec/opensearch/1.1/}"
PAGE_SIZE = 200
API_DELAY_S = 3.1          # arXiv asks for at least 3 s between requests
MAX_RESULTS_TOTAL = 5000

MONTHS = ["January", "February", "March", "April", "May", "June", "July",
          "August", "September", "October", "November", "December"]
WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def log(msg):
    print(msg, file=sys.stderr)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def load_config():
    cfg = tomllib.loads((ROOT / "config.toml").read_text(encoding="utf-8"))
    groups = cfg.get("category_groups", {})
    sets = cfg.get("term_sets", {})

    def expand_cats(items):
        out = set()
        for it in items or []:
            out |= set(groups.get(it, [it]))
        return out

    topics = []
    for t in cfg["topics"]:
        terms = list(t.get("terms", []))
        for s in t.get("term_sets", []):
            terms += sets[s]
        req = []
        for s in t.get("require_sets", []):
            req += sets[s]
        topics.append({
            "name": t["name"],
            "slug": t["slug"],
            "color": t.get("color", "#555"),
            "patterns": [re.compile(p, re.IGNORECASE) for p in terms],
            "require": [re.compile(p, re.IGNORECASE) for p in req],
            "cats": expand_cats(t.get("cats")),
            "primary_only": t.get("primary_only", False),
            "crosslist": expand_cats(t.get("also_if_crosslisted")),
        })
    cfg["_topics"] = topics
    return cfg


# ---------------------------------------------------------------------------
# arXiv API
# ---------------------------------------------------------------------------

def build_query(categories, start, end):
    cats = " OR ".join(f"cat:{c}" for c in categories)
    f = "%Y%m%d%H%M"
    return f"({cats}) AND submittedDate:[{start.strftime(f)} TO {end.strftime(f)}]"


def fetch_page(query, start_idx):
    params = urllib.parse.urlencode({
        "search_query": query, "start": start_idx, "max_results": PAGE_SIZE,
        "sortBy": "submittedDate", "sortOrder": "descending",
    })
    req = urllib.request.Request(f"{API_URL}?{params}",
                                 headers={"User-Agent": "arxiv-radar/1.0 (daily personal digest)"})
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                return r.read()
        except Exception as e:
            wait = 10 * (attempt + 1)
            log(f"  API error ({e}), retrying in {wait}s")
            time.sleep(wait)
    raise RuntimeError("arXiv API unreachable after 5 attempts")


def parse_feed(xml_bytes):
    root = ET.fromstring(xml_bytes)
    total_el = root.find(f"{OPENSEARCH}totalResults")
    total = int(total_el.text) if total_el is not None and total_el.text else 0
    papers = []
    for e in root.findall(f"{ATOM}entry"):
        url = (e.findtext(f"{ATOM}id") or "").strip()
        if "/abs/" not in url:
            continue  # API error entry
        arxiv_id = re.sub(r"v\d+$", "", url.rsplit("/abs/", 1)[-1])
        prim = e.find(f"{ARXIV}primary_category")
        papers.append({
            "id": arxiv_id,
            "url": f"https://arxiv.org/abs/{arxiv_id}",
            "title": " ".join((e.findtext(f"{ATOM}title") or "").split()),
            "abstract": " ".join((e.findtext(f"{ATOM}summary") or "").split()),
            "authors": [a.findtext(f"{ATOM}name") or "" for a in e.findall(f"{ATOM}author")],
            "published": e.findtext(f"{ATOM}published") or "",
            "primary": prim.get("term") if prim is not None else "",
            "cats": [c.get("term") for c in e.findall(f"{ATOM}category")],
        })
    return total, papers


def fetch_all(categories, start, end):
    query = build_query(categories, start, end)
    log(f"Query: {query}")
    papers, idx, total = [], 0, None
    while True:
        t, page = parse_feed(fetch_page(query, idx))
        total = t if total is None else total
        papers.extend(page)
        idx += PAGE_SIZE
        log(f"  fetched {len(papers)}/{total}")
        if not page or idx >= total or idx >= MAX_RESULTS_TOTAL:
            break
        time.sleep(API_DELAY_S)
    return papers


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def classify(paper, topics):
    text = f"{paper['title']} {paper['title']} {paper['abstract']}"  # the title counts double
    cats = set(paper["cats"])
    hits = {}
    for t in topics:
        if t["cats"]:
            if t["primary_only"]:
                if paper["primary"] not in t["cats"]:
                    continue
            elif not (cats & t["cats"]) and not (cats & t["crosslist"]):
                continue
        if t["require"] and not any(p.search(text) for p in t["require"]):
            continue
        score = sum(1 for p in t["patterns"] if p.search(text))
        if score:
            hits[t["slug"]] = score
    return hits


# ---------------------------------------------------------------------------
# "run" command
# ---------------------------------------------------------------------------

def cmd_run(args, cfg):
    now = datetime.now(timezone.utc)
    f = cfg["fetch"]
    days = args.days or (f.get("days_monday", 4) if now.weekday() == 0 else f.get("days_default", 2))
    start = now - timedelta(days=days)

    if args.from_file:
        _, papers = parse_feed(Path(args.from_file).read_bytes())
    else:
        papers = fetch_all(f["categories"], start, now)

    seen = [] if args.no_state else json.loads(SEEN.read_text()) if SEEN.exists() else []
    seen_set = set(seen)
    unique = {p["id"]: p for p in papers if p["id"] not in seen_set}

    by_topic = {t["slug"]: [] for t in cfg["_topics"]}
    for p in unique.values():
        hits = classify(p, cfg["_topics"])
        p["topics"] = list(hits)
        for slug, score in hits.items():
            by_topic[slug].append((score, p))
    limit = f.get("max_per_topic", 15)
    for slug in by_topic:
        by_topic[slug].sort(key=lambda x: (-x[0], x[1]["published"]))
        by_topic[slug] = [p for _, p in by_topic[slug][:limit]]

    n_selected = len({p["id"] for ps in by_topic.values() for p in ps})
    log(f"New papers examined: {len(unique)}, selected: {n_selected}")

    date_str = now.strftime("%Y-%m-%d")
    if unique or args.from_file:
        DIGESTS.mkdir(parents=True, exist_ok=True)
        digest = {
            "date": date_str,
            "generated": now.isoformat(timespec="minutes"),
            "window": [start.isoformat(timespec="minutes"), now.isoformat(timespec="minutes")],
            "scanned": len(unique),
            "topics": by_topic,
        }
        (DIGESTS / f"{date_str}.json").write_text(
            json.dumps(digest, ensure_ascii=False, indent=1), encoding="utf-8")
    else:
        log("No new papers (holiday or empty window): no digest for today.")

    if not args.no_state and not args.from_file:
        # save all examined IDs, not just the selected ones, so that a change
        # to the keywords does not pull in old papers
        SEEN.parent.mkdir(parents=True, exist_ok=True)
        SEEN.write_text(json.dumps((seen + sorted(unique))[-60000:]))

    render_site(cfg)


# ---------------------------------------------------------------------------
# Site generation
# ---------------------------------------------------------------------------

def esc(s):
    return html.escape(s or "", quote=True)


def format_date(date_str, weekday=True):
    d = datetime.strptime(date_str, "%Y-%m-%d")
    s = f"{d.day} {MONTHS[d.month - 1]} {d.year}"
    return f"{WEEKDAYS[d.weekday()]} {s}" if weekday else s


def authors_short(authors, n=4):
    if len(authors) <= n:
        return ", ".join(authors)
    return f"{', '.join(authors[:n])} and {len(authors) - n} others"


def page(cfg, title, body, rel=""):
    site = cfg["site"]
    return f"""<!doctype html>
<html lang="{esc(site.get('language', 'en'))}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<meta name="description" content="{esc(site.get('description', ''))}">
<link rel="alternate" type="application/rss+xml" title="{esc(site['title'])}" href="{rel}feed.xml">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Newsreader:opsz,wght@6..72,400;6..72,500;6..72,600&family=IBM+Plex+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<link rel="stylesheet" href="{rel}style.css">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><text y='.9em' font-size='90'>📡</text></svg>">
<script>
window.MathJax = {{ tex: {{ inlineMath: [['$','$'], ['\\\\(','\\\\)']] }}, options: {{ skipHtmlTags: ['script','style','textarea'] }} }};
</script>
<script defer src="https://cdn.jsdelivr.net/npm/mathjax@3.2.2/es5/tex-chtml.js"></script>
</head>
<body>
<div class="wrap">
<header class="masthead">
  <a class="brand" href="{rel}index.html">{esc(site['title'])}</a>
  <nav><a href="{rel}archive.html">Archive</a><a href="{rel}feed.xml">RSS</a></nav>
</header>
{body}
<footer class="foot">
  <p>Generated automatically from the arXiv API. Titles and abstracts belong to their respective authors; each entry links to the original page.</p>
</footer>
</div>
</body>
</html>
"""


def histogram(cfg, digest, rel=""):
    counts = [(t, len(digest["topics"].get(t["slug"], []))) for t in cfg["_topics"]]
    peak = max([c for _, c in counts] + [1])
    rows = []
    for t, c in counts:
        width = 0 if c == 0 else max(4, round(100 * c / peak))
        target = f'href="#{t["slug"]}"'
        rows.append(
            f'<a class="hrow{" empty" if c == 0 else ""}" {target} style="--c:{t["color"]}">'
            f'<span class="hname">{esc(t["name"])}</span>'
            f'<span class="hbar"><span style="width:{width}%"></span></span>'
            f'<span class="hcount">{c}</span></a>')
    return f'<div class="histo" role="list">{"".join(rows)}</div>'


def render_paper(p, cfg, current_slug):
    names = {t["slug"]: t for t in cfg["_topics"]}
    others = [names[s] for s in p.get("topics", []) if s != current_slug and s in names]
    also = ""
    if others:
        links = ", ".join(f'<a href="#{t["slug"]}" style="--c:{t["color"]}">{esc(t["name"])}</a>' for t in others)
        also = f'<p class="also">Also in {links}</p>'
    cats = " ".join(f'<span class="cat{" prim" if c == p["primary"] else ""}">{esc(c)}</span>'
                    for c in [p["primary"]] + [c for c in p["cats"] if c != p["primary"]][:3])
    return f"""<article class="paper">
  <h3><a href="{esc(p['url'])}">{esc(p['title'])}</a></h3>
  <p class="authors">{esc(authors_short(p['authors']))}</p>
  <p class="cats">{cats}</p>
  <details><summary>Abstract</summary><p>{esc(p['abstract'])}</p>
  <p class="links"><a href="{esc(p['url'])}">arXiv page</a><a href="{esc(p['url'].replace('/abs/', '/pdf/'))}">PDF</a></p></details>
  {also}
</article>"""


def render_day(cfg, digest, prev_d, next_d):
    sections = []
    for t in cfg["_topics"]:
        items = digest["topics"].get(t["slug"], [])
        inner = "".join(render_paper(p, cfg, t["slug"]) for p in items) or \
            '<p class="none">No papers on this topic in today’s window.</p>'
        sections.append(f'<section id="{t["slug"]}" class="topic" style="--c:{t["color"]}">'
                        f'<h2>{esc(t["name"])} <span class="n">{len(items)}</span></h2>{inner}</section>')
    total = len({p["id"] for ps in digest["topics"].values() for p in ps})
    nav = '<nav class="daynav">'
    nav += f'<a href="{prev_d}.html">‹ {esc(format_date(prev_d, False))}</a>' if prev_d else "<span></span>"
    nav += f'<a href="{next_d}.html">{esc(format_date(next_d, False))} ›</a>' if next_d else "<span></span>"
    nav += "</nav>"
    head = f"""<div class="dayhead">
  <h1>{esc(format_date(digest['date']))}</h1>
  <p class="summary">{total} papers selected out of {digest['scanned']} new on arXiv</p>
  {histogram(cfg, digest, rel="")}
</div>"""
    return head + "".join(sections) + nav


def render_site(cfg):
    files = sorted(DIGESTS.glob("*.json")) if DIGESTS.exists() else []
    digests = [json.loads(f.read_text(encoding="utf-8")) for f in files]
    if SITE.exists():
        shutil.rmtree(SITE)
    SITE.mkdir(parents=True)
    shutil.copy(ASSETS / "style.css", SITE / "style.css")
    (SITE / ".nojekyll").write_text("")
    title = cfg["site"]["title"]

    dates = [d["date"] for d in digests]
    for i, d in enumerate(digests):
        prev_d = dates[i - 1] if i > 0 else None
        next_d = dates[i + 1] if i + 1 < len(dates) else None
        html_day = page(cfg, f"{title}: {format_date(d['date'], False)}", render_day(cfg, d, prev_d, next_d))
        (SITE / f"{d['date']}.html").write_text(html_day, encoding="utf-8")

    if digests:
        latest = digests[-1]
        body = render_day(cfg, latest, dates[-2] if len(dates) > 1 else None, None)
        (SITE / "index.html").write_text(page(cfg, title, body), encoding="utf-8")
    else:
        body = ('<div class="dayhead"><h1>No digest yet</h1><p class="summary">'
                'The first one will be published on the next workflow run. '
                'You can start it now from Actions › Daily digest › Run workflow.</p></div>')
        (SITE / "index.html").write_text(page(cfg, title, body), encoding="utf-8")

    # archive
    rows = []
    for d in reversed(digests):
        total = len({p["id"] for ps in d["topics"].values() for p in ps})
        rows.append(f'<li><a href="{d["date"]}.html">{esc(format_date(d["date"]))}</a>'
                    f'<span class="acount">{total} papers</span></li>')
    arch = ('<div class="dayhead"><h1>Archive</h1><p class="summary">'
            f'{len(digests)} digests published</p></div><ul class="archive">{"".join(rows)}</ul>')
    (SITE / "archive.html").write_text(page(cfg, f"{title}: archive", arch), encoding="utf-8")

    write_feed(cfg, digests[-30:])
    log(f"Site generated in {SITE} ({len(digests)} days)")


def write_feed(cfg, digests):
    site = cfg["site"]
    base = site["base_url"].rstrip("/") + "/"
    names = {t["slug"]: t["name"] for t in cfg["_topics"]}
    items = []
    for d in reversed(digests):
        parts = []
        for slug, papers in d["topics"].items():
            if papers:
                lis = "".join(f'<li><a href="{esc(p["url"])}">{esc(p["title"])}</a></li>' for p in papers)
                parts.append(f"<h3>{esc(names.get(slug, slug))}</h3><ul>{lis}</ul>")
        dt = datetime.fromisoformat(d["generated"])
        link = f"{base}{d['date']}.html"
        items.append(f"""<item>
<title>{esc(site['title'])}: {esc(format_date(d['date']))}</title>
<link>{link}</link><guid isPermaLink="true">{link}</guid>
<pubDate>{format_datetime(dt)}</pubDate>
<description>{esc("".join(parts) or "No papers selected.")}</description>
</item>""")
    feed = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<title>{esc(site['title'])}</title><link>{base}</link>
<description>{esc(site.get('description', ''))}</description>
<language>{esc(site.get('language', 'en'))}</language>
{"".join(items)}
</channel></rss>
"""
    (SITE / "feed.xml").write_text(feed, encoding="utf-8")


# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="arxiv-radar: daily arXiv digest")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="fetch, classify and generate the site")
    r.add_argument("--days", type=int, help="days to cover (default from config)")
    r.add_argument("--no-state", action="store_true", help="ignore data/seen.json")
    r.add_argument("--from-file", help="test: use a local Atom feed")
    sub.add_parser("render", help="regenerate the site only, from saved data")
    args = ap.parse_args()

    cfg = load_config()
    if args.cmd == "run":
        cmd_run(args, cfg)
    else:
        render_site(cfg)


if __name__ == "__main__":
    main()
