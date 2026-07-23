"""Static site generator: measurements -> pages optimized for citation by LLMs.

What makes the content citable:
  - a direct factual answer in the first sentence (LLMs extract it verbatim);
  - structured tables + JSON-LD Dataset/Table (machine-readable);
  - a visible "Last verified" date + FAQ (FAQPage schema);
  - llms.txt as an index for AI crawlers.

Output: static HTML in out_dir, deployable to any static host (Cloudflare Pages/Vercel).
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from _log import logger
from config import DB_PATH
from db import connect
from aggregate import ttfb_ranking, Ranking, latency_series
from deprecations import load as load_deprecations, upcoming, recent

BASE_URL = os.environ.get("BASE_URL", "https://llmlatency.dev").rstrip("/")
SITE_NAME = "AI Latency Tracker"

REGION_LABELS = {
    "local": "this location", "eu-west": "Europe (West)", "eu-hetzner": "Europe (Germany)",
    "us-east": "US (East)", "us-west": "US (West)", "us-central": "US (Central)",
    "ap-tokyo": "Asia (Tokyo)", "ap-singapore": "Asia (Singapore)", "sa-east": "South America (São Paulo)",
}


def _u(rel: str) -> str:
    """Canonical URL without .html — Cloudflare Pages serves clean paths (.html -> 308 redirect).
    Files stay .html on disk, but all links/canonical/sitemap/llms.txt point to the clean URL."""
    return f"{BASE_URL}/{rel}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _label(region: str) -> str:
    return REGION_LABELS.get(region, region)


def _distinct(db_path: str, col: str) -> list[str]:
    with connect(db_path) as conn:
        rows = conn.execute(f"SELECT DISTINCT {col} FROM measurements ORDER BY {col}").fetchall()
    return [r[col] for r in rows]


# ─────────────────────────── HTML shell ───────────────────────────

_CSS = """
:root{--fg:#1a1a1a;--muted:#595959;--sub:#595959;--line:#d0d0d5;--bg:#fff;--accent:#0a7d43;--code:#f6f8fa}
@media(prefers-color-scheme:dark){:root{--fg:#e8e8e8;--muted:#9ba1a8;--sub:#9ba1a8;--line:#2a2f36;--bg:#0d1117;--accent:#4ac26b;--code:#161b22}}
:root[data-theme=dark]{--fg:#e8e8e8;--muted:#9ba1a8;--sub:#9ba1a8;--line:#2a2f36;--bg:#0d1117;--accent:#4ac26b;--code:#161b22}
:root[data-theme=light]{--fg:#1a1a1a;--muted:#595959;--sub:#595959;--line:#d0d0d5;--bg:#fff;--accent:#0a7d43;--code:#f6f8fa}
.s{color:var(--sub);font-size:.85rem}
*{box-sizing:border-box}body{font:16px/1.6 -apple-system,BlinkMacSystemFont,Segoe UI,Roboto,sans-serif;
color:var(--fg);background:var(--bg);margin:0;padding:2rem 1rem}
main{max-width:820px;margin:0 auto}h1{font-size:1.7rem;line-height:1.25;margin:.2em 0}
h2{font-size:1.2rem;margin-top:2rem;border-top:1px solid var(--line);padding-top:1.2rem}
.lead{font-size:1.1rem}.updated{color:var(--muted);font-size:.85rem;margin:.3rem 0 1.5rem}
table{width:100%;border-collapse:collapse;margin:1rem 0;font-variant-numeric:tabular-nums}
th,td{text-align:left;padding:.5rem .6rem;border-bottom:1px solid var(--line)}
th{font-size:.8rem;text-transform:uppercase;letter-spacing:.03em;color:var(--muted)}
td.n{text-align:right}a{color:var(--accent);text-decoration:underline}nav a{text-decoration:none}nav a:hover{text-decoration:underline}
nav{font-size:.9rem;margin-bottom:1rem}.rank{color:var(--muted);width:2rem}
footer{margin-top:3rem;padding-top:1rem;border-top:1px solid var(--line);color:var(--muted);font-size:.85rem}
details{margin:.5rem 0}summary{cursor:pointer;font-weight:600}code{background:var(--code);padding:.1em .3em;border-radius:3px}
"""


# WebMCP: a real in-browser tool for AI agents (navigator.modelContext).
# A separate constant (not an f-string) so the JS curly braces don't clash with _page.
_WEBMCP = r'''<script>
(function(){
  var n=typeof navigator!=='undefined'&&navigator;
  if(!n||!('modelContext' in n)||!n.modelContext||!n.modelContext.provideContext)return;
  try{n.modelContext.provideContext({tools:[{
    name:"get_ai_api_latency",
    description:"Measured latency (TTFB p50/p95) and uptime rankings of AI inference API providers by region, from llmlatency.dev.",
    inputSchema:{type:"object",properties:{region:{type:"string",description:"eu-hetzner, us-central, ap-tokyo or sa-east; omit for all"}}},
    execute:async function(a){
      var r=await fetch("/api/rankings.json");var d=await r.json();
      var reg=a&&a.region;var out=(reg&&d.regions[reg])?d.regions[reg]:d.regions;
      return {content:[{type:"text",text:JSON.stringify(out)}]};
    }
  }]});}catch(e){}
})();
</script>'''


def _jsonld_script(o: dict) -> str:
    """JSON-LD in <script>: escape < > & so the string can't close the tag early."""
    payload = json.dumps(o).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return f'<script type="application/ld+json">{payload}</script>'


def _page(*, title: str, description: str, canonical: str, body: str, jsonld: list[dict]) -> str:
    ld = "\n".join(_jsonld_script(o) for o in jsonld)
    _c = canonical.rstrip("/")
    md_href = f"{BASE_URL}/index.md" if _c == BASE_URL else f"{_c}.md"
    return f"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<meta name="description" content="{html.escape(description)}">
<link rel="canonical" href="{html.escape(canonical, quote=True)}">
<link rel="alternate" type="text/markdown" href="{html.escape(md_href, quote=True)}" title="Markdown for AI agents">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 16 16'><path d='M9 1 3 9h4l-1 6 7-9H9z' fill='%230a7d43'/></svg>">
<meta property="og:title" content="{html.escape(title)}"><meta property="og:type" content="website">
<style>{_CSS}</style>{ld}
</head><body><main>
<nav><a href="{BASE_URL}/">{SITE_NAME}</a> · <a href="{BASE_URL}/deprecations">Deprecations</a> · <a href="{BASE_URL}/changelog">Changelog</a></nav>
{body}
<footer>Independent, provider-neutral measurements. Updated automatically.
Method: distributed probes measure DNS→TCP→TLS→first-byte (edge) and time-to-first-token (inference)
per region on a schedule. <a href="{BASE_URL}/llms.txt">llms.txt</a> · <a href="{BASE_URL}/api/rankings.json">API</a></footer>
</main>{_WEBMCP}</body></html>"""


def _table(rankings: list[Ranking], metric_label: str) -> str:
    head = f"<tr><th scope=col class=rank>#</th><th scope=col>Provider</th><th scope=col>p50 {metric_label}</th><th scope=col>p95</th><th scope=col>Uptime</th><th scope=col>Samples</th></tr>"
    rows = "".join(
        f"<tr><td class=rank>{i}</td><td>{html.escape(r.provider)}</td>"
        f"<td class=n>{r.p50_ms:.0f} ms</td><td class=n>{r.p95_ms:.0f} ms</td>"
        f"<td class=n>{r.ok_rate:.0f}%</td><td class=n>{r.samples}</td></tr>"
        for i, r in enumerate(rankings, 1)
    )
    return f"<table>{head}{rows}</table>" if rankings else "<p>Data collecting — first cycle pending.</p>"


def _dataset_ld(name: str, url: str, rankings: list[Ranking]) -> dict:
    return {
        "@context": "https://schema.org", "@type": "Dataset", "name": name, "url": url,
        "dateModified": _now().date().isoformat(), "isAccessibleForFree": True,
        "creator": {"@type": "Organization", "name": SITE_NAME},
        "measurementTechnique": "Distributed network + inference latency probing",
        "variableMeasured": ["time-to-first-byte (ms)", "time-to-first-token (ms)", "uptime (%)"],
    }


def _faq_ld(qa: list[tuple[str, str]]) -> dict:
    return {
        "@context": "https://schema.org", "@type": "FAQPage",
        "mainEntity": [
            {"@type": "Question", "name": q,
             "acceptedAnswer": {"@type": "Answer", "text": a}} for q, a in qa
        ],
    }


# ─────────────────────────── pages ───────────────────────────

def region_page(db_path: str, region: str) -> str:
    net = ttfb_ranking(db_path, region, hours=24, probe_type="network")
    inf = ttfb_ranking(db_path, region, hours=24, probe_type="inference")
    label, date = _label(region), _now().strftime("%B %d, %Y")
    url = f"{BASE_URL}/region/{region}"

    esc_label = html.escape(label)
    if net:
        top = net[0]
        fact = (f"As of {date}, measured from {esc_label}, the fastest AI inference API by edge latency "
                f"(time-to-first-byte) is <strong>{html.escape(top.provider)}</strong> at "
                f"{top.p50_ms:.0f} ms p50 (n={top.samples}).")
    else:
        fact = f"Latency measurements from {esc_label} are being collected."

    inf_block = ""
    if inf:
        inf_block = f"<h2>Inference latency (time-to-first-token)</h2>{_table(inf, 'TTFT')}"

    qa = [
        (f"Which AI API is fastest from {label}?",
         f"By edge latency (TTFB), {net[0].provider if net else 'n/a'} is fastest at "
         f"{net[0].p50_ms:.0f} ms p50 as of {date}." if net else "Data collecting."),
        ("How is this measured?",
         "Distributed probes measure DNS, TCP, TLS and first-byte time to each provider's API host "
         "from this region on a schedule; inference latency measures time-to-first-token with a minimal request."),
    ]
    body = f"""
<h1>Fastest AI API from {html.escape(label)}</h1>
<p class="updated"><time datetime="{_now().isoformat()}">Last verified {date}</time> · edge + inference latency</p>
<p class="lead">{fact}</p>
<h2>Edge latency (time-to-first-byte)</h2>
{_table(net, 'TTFB')}
{inf_block}"""
    return _page(title=f"Fastest AI API from {label} — latency & uptime",
                 description=f"Live, provider-neutral latency and uptime of OpenAI, Anthropic, Google and others measured from {label}.",
                 canonical=url, body=body,
                 jsonld=[_dataset_ld(f"AI API latency from {label}", url, net), _faq_ld(qa)])


_CHART_COLORS = ["#1D9E75", "#D85A30", "#7F77DD", "#C08A00", "#D4537E", "#2E8BC0", "#5DCAA5"]


def _chart_svg(db_path: str, provider: str, regions: list[str]) -> str:
    """Inline SVG chart of p50 latency over time, one line per region. No external libraries."""
    series = {reg: s for reg in regions if (s := latency_series(db_path, provider, reg, hours=72))}
    if not any(len(v) >= 2 for v in series.values()):
        return ('<p class="s">Latency-history chart appears once a few hours of measurements accumulate '
                '(site is new — data is filling in).</p>')
    xs = sorted({t for v in series.values() for t, _ in v})
    xi = {t: i for i, t in enumerate(xs)}
    n = max(1, len(xs) - 1)
    ymax = (max(p for v in series.values() for _, p in v) or 1) * 1.15
    W, H, pl, pr, pt, pb = 680, 200, 46, 12, 12, 28
    iw, ih = W - pl - pr, H - pt - pb

    def fx(t: str) -> float:
        return pl + iw * (xi[t] / n)

    def fy(p: float) -> float:
        return pt + ih * (1 - p / ymax)

    grid = ""
    for frac in (0.0, 0.5, 1.0):
        y = pt + ih * (1 - frac)
        grid += (f'<line x1="{pl}" y1="{y:.0f}" x2="{W-pr}" y2="{y:.0f}" stroke="var(--line)" opacity="0.5"/>'
                 f'<text x="{pl-6}" y="{y+3:.0f}" text-anchor="end" font-size="10" fill="var(--sub)">{ymax*frac:.0f}</text>')
    # X labels: anchor the first at the left edge (start) and the last at the right edge (end)
    # so neither gets clipped by the SVG viewport.
    def _xlab(t: str, anchor: str) -> str:
        return (f'<text x="{fx(t):.0f}" y="{H-8}" text-anchor="{anchor}" font-size="10" '
                f'fill="var(--sub)">{html.escape(t[5:16].replace("T", " "))}</text>')
    xl = _xlab(xs[0], "start") + _xlab(xs[len(xs) // 2], "middle") + _xlab(xs[-1], "end")

    # Legend: lay out by actual label width with wrapping (a fixed step overlapped long
    # labels such as "South America (Sao Paulo)").
    lines = legend = ""
    lx, ly, row_h = pl, H + 10, 15
    for i, (reg, v) in enumerate(series.items()):
        c = _CHART_COLORS[i % len(_CHART_COLORS)]
        pts = " ".join(f"{fx(t):.1f},{fy(p):.1f}" for t, p in v)
        lines += f'<polyline points="{pts}" fill="none" stroke="{c}" stroke-width="2"/>'
        lbl = _label(reg)
        item_w = 21 + len(lbl) * 6.0 + 18                 # marker+gap + text + inter-item gap
        if lx > pl and lx + item_w > W - pr:              # doesn't fit -> wrap to next row
            lx, ly = pl, ly + row_h
        legend += (f'<line x1="{lx:.0f}" y1="{ly}" x2="{lx+16:.0f}" y2="{ly}" stroke="{c}" stroke-width="2.5"/>'
                   f'<text x="{lx+21:.0f}" y="{ly+4}" font-size="10" fill="var(--fg)">{html.escape(lbl)}</text>')
        lx += item_w
    vb_h = ly + 10
    return (f'<svg viewBox="0 0 {W} {vb_h:.0f}" width="100%" style="max-width:{W}px;height:auto" role="img" '
            f'aria-label="{html.escape(provider)} p50 latency over time by region">'
            f'<title>{html.escape(provider)} p50 latency over time (ms), by region</title>{grid}{xl}{lines}{legend}</svg>')


def provider_page(db_path: str, provider: str, regions: list[str]) -> str:
    date = _now().strftime("%B %d, %Y")
    url = f"{BASE_URL}/provider/{provider}"
    rows = []
    for reg in regions:
        rank = {r.provider: r for r in ttfb_ranking(db_path, reg, 24, "network")}.get(provider)
        if rank:
            rows.append(f"<tr><td>{html.escape(_label(reg))}</td><td class=n>{rank.p50_ms:.0f} ms</td>"
                        f"<td class=n>{rank.p95_ms:.0f} ms</td><td class=n>{rank.ok_rate:.0f}%</td></tr>")
    table = ("<table><tr><th scope=col>Region</th><th scope=col>p50 TTFB</th><th scope=col>p95</th><th scope=col>Uptime</th></tr>"
             + "".join(rows) + "</table>") if rows else "<p>Data collecting.</p>"
    body = f"""
<h1>{html.escape(provider)} — AI API latency & reliability by region</h1>
<p class="updated"><time datetime="{_now().isoformat()}">Last verified {date}</time></p>
<p class="lead">Edge latency and uptime of the {html.escape(provider)} API measured from each region, updated automatically.</p>
{table}
<h2>Latency over time (p50, by region)</h2>
{_chart_svg(db_path, provider, regions)}"""
    return _page(title=f"{provider} API latency & uptime by region",
                 description=f"Live latency and uptime of the {provider} AI API across regions.",
                 canonical=url, body=body, jsonld=[_dataset_ld(f"{provider} latency by region", url, [])])


def index_page(db_path: str, regions: list[str], providers: list[str]) -> str:
    date = _now().strftime("%B %d, %Y")
    reg_links = "".join(f'<li><a href="{BASE_URL}/region/{html.escape(r, quote=True)}">Fastest AI API from {html.escape(_label(r))}</a></li>' for r in regions)
    prov_links = "".join(f'<li><a href="{BASE_URL}/provider/{html.escape(p, quote=True)}">{html.escape(p)} latency by region</a></li>' for p in providers)
    body = f"""
<h1>{SITE_NAME}</h1>
<p class="updated"><time datetime="{_now().isoformat()}">Updated {date}</time></p>
<p class="lead">Independent, provider-neutral latency and uptime of AI inference APIs (OpenAI, Anthropic,
Google, Mistral, DeepSeek, xAI, OpenRouter), measured from multiple regions and updated automatically.</p>
<h2>By region</h2><ul>{reg_links}</ul>
<h2>By provider</h2><ul>{prov_links}</ul>"""
    return _page(title=f"{SITE_NAME} — live AI API latency & uptime by region",
                 description="Independent, provider-neutral latency and uptime of AI inference APIs, measured per region.",
                 canonical=f"{BASE_URL}/", body=body,
                 jsonld=[{"@context": "https://schema.org", "@type": "WebSite", "name": SITE_NAME, "url": f"{BASE_URL}/"}])


def changelog_page(db_path: str) -> str:
    """Freshness signal: notable latency changes over the last 24 hours."""
    date = _now().strftime("%B %d, %Y")
    with connect(db_path) as conn:
        rows = conn.execute(
            """SELECT provider, region, probe_type, ttfb_ms, ttft_ms, status, ts
               FROM measurements ORDER BY ts DESC LIMIT 20"""
        ).fetchall()
    items = "".join(
        f"<tr><td>{html.escape(r['ts'][:19])}</td><td>{html.escape(r['provider'])}</td>"
        f"<td>{html.escape(r['region'])}</td><td>{html.escape(r['probe_type'])}</td>"
        f"<td class=n>{(r['ttfb_ms'] or r['ttft_ms'] or 0):.0f} ms</td><td>{html.escape(r['status'])}</td></tr>"
        for r in rows
    )
    body = f"""
<h1>Changelog — recent AI API latency measurements</h1>
<p class="updated"><time datetime="{_now().isoformat()}">Updated {date}</time></p>
<p class="lead">Most recent probe results. A diff of ranking changes accumulates here as history builds —
this is itself high-intent, citable content (\"did [provider] get slower this week?\").</p>
<table><tr><th scope=col>Time (UTC)</th><th scope=col>Provider</th><th scope=col>Region</th><th scope=col>Probe</th><th scope=col>Latency</th><th scope=col>Status</th></tr>{items}</table>"""
    return _page(title=f"{SITE_NAME} — changelog", description="Recent AI API latency and uptime measurements.",
                 canonical=f"{BASE_URL}/changelog", body=body, jsonld=[])


def deprecations_page() -> str:
    """Freshness signal: model deprecation calendar. Only verified entries, stated as fact."""
    date = _now().strftime("%B %d, %Y")
    url = f"{BASE_URL}/deprecations"
    all_entries = load_deprecations()
    up = upcoming(all_entries)
    rec = recent(all_entries)

    def _table(items) -> str:
        if not items:
            return "<p>Calendar is being populated from official provider deprecation pages.</p>"
        rows = "".join(
            f"<tr><td>{html.escape(d.provider)}</td><td>{html.escape(d.model)}</td>"
            f"<td class=n>{html.escape(d.shutdown or 'TBA')}</td>"
            f"<td>{html.escape(d.replacement or '—')}</td>"
            f"<td><a href='{html.escape(d.source_url)}' rel='nofollow'>source</a></td></tr>"
            for d in items
        )
        return ("<table><tr><th scope=col>Provider</th><th scope=col>Model</th><th scope=col>Retires</th>"
                f"<th scope=col>Migrate to</th><th scope=col>Source</th></tr>{rows}</table>")

    fact = (f"As of {date}, the next AI model retirement is <strong>{html.escape(up[0].model)}</strong> "
            f"({html.escape(up[0].provider)}) on {html.escape(up[0].shutdown or 'TBA')}, "
            f"migrate to {html.escape(up[0].replacement or 'a newer model')}." if up
            else "The calendar is being populated from official provider deprecation pages.")
    qa = [(f"When is {d.model} being retired?",
           f"{d.model} ({d.provider}) retires on {d.shutdown}; migrate to {d.replacement}. Source: {d.source_url}")
          for d in (up[:6] + rec[:2])]

    body = f"""
<h1>AI model deprecation &amp; migration calendar</h1>
<p class="updated"><time datetime="{_now().isoformat()}">Updated {date}</time> · verified from official provider docs</p>
<p class="lead">{fact}</p>
<p>When each AI model is retired and what to migrate to. Every date is taken from the provider's official
deprecation page (linked per row) — nothing here is estimated.</p>
<h2>Upcoming retirements</h2>
{_table(up)}
<h2>Recently retired</h2>
{_table(rec)}"""
    return _page(title="AI model deprecation & migration calendar — when models retire, what to migrate to",
                 description="Verified schedule of AI model retirements (OpenAI, Anthropic, Azure) and recommended replacements, from official provider docs.",
                 canonical=url, body=body, jsonld=[_faq_ld(qa)])


def llms_txt(regions: list[str], providers: list[str]) -> str:
    reg = "\n".join(f"- [Fastest AI API from {_label(r)}]({BASE_URL}/region/{r}): edge + inference latency ranking, updated automatically" for r in regions)
    prov = "\n".join(f"- [{p} latency by region]({BASE_URL}/provider/{p}): latency and uptime of {p} across regions" for p in providers)
    return f"""# {SITE_NAME}

> Independent, provider-neutral latency and uptime of AI inference APIs (OpenAI, Anthropic, Google, Mistral, DeepSeek, xAI, OpenRouter), measured from multiple regions with distributed probes and updated automatically. Freshness and per-region measurement are the differentiator — figures here are measured, not scraped from pricing pages.

## Regions
{reg}

## Providers
{prov}

## About
- [AI model deprecation & migration calendar]({BASE_URL}/deprecations): when models are retired and what to migrate to
- [Changelog]({BASE_URL}/changelog): most recent measurements and ranking changes
"""


def sitemap(urls: list[str]) -> str:
    now = _now().date().isoformat()
    items = "".join(f"<url><loc>{html.escape(u)}</loc><lastmod>{now}</lastmod></url>" for u in urls)
    return f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{items}</urlset>'


# ─────────────────────── Markdown versions (for AI agents) ───────────────────────

def _md_table(headers: list[str], rows: list[list]) -> str:
    head = "| " + " | ".join(headers) + " |\n| " + " | ".join("---" for _ in headers) + " |\n"
    return head + "".join("| " + " | ".join(str(c) for c in r) + " |\n" for r in rows)


def region_md(db_path: str, region: str) -> str:
    net = ttfb_ranking(db_path, region, 24, "network")
    label, date = _label(region), _now().strftime("%B %d, %Y")
    out = [f"# Fastest AI API from {label}",
           f"Updated {date}. Independent, provider-neutral edge latency (TTFB) and uptime by provider, "
           f"measured from {label}, updated automatically.", ""]
    if net:
        t = net[0]
        out.append(f"As of {date}, measured from {label}, the fastest AI inference API by edge latency "
                   f"(time-to-first-byte) is **{t.provider}** at {t.p50_ms:.0f} ms p50 (n={t.samples}).")
        out += ["", "## Edge latency (time-to-first-byte)",
                _md_table(["#", "Provider", "p50", "p95", "Uptime", "Samples"],
                          [[i, r.provider, f"{r.p50_ms:.0f} ms", f"{r.p95_ms:.0f} ms",
                            f"{r.ok_rate:.0f}%", r.samples] for i, r in enumerate(net, 1)])]
    out += ["", f"Source: {BASE_URL}/region/{region}"]
    return "\n".join(out)


def provider_md(db_path: str, provider: str, regions: list[str]) -> str:
    date = _now().strftime("%B %d, %Y")
    rows = []
    for reg in regions:
        rank = {r.provider: r for r in ttfb_ranking(db_path, reg, 24, "network")}.get(provider)
        if rank:
            rows.append([_label(reg), f"{rank.p50_ms:.0f} ms", f"{rank.p95_ms:.0f} ms", f"{rank.ok_rate:.0f}%"])
    out = [f"# {provider} — AI API latency & reliability by region",
           f"Updated {date}. Edge latency and uptime of the {provider} API measured from each region.", ""]
    if rows:
        out.append(_md_table(["Region", "p50 TTFB", "p95", "Uptime"], rows))
    out += ["", f"Source: {BASE_URL}/provider/{provider}"]
    return "\n".join(out)


def deprecations_md() -> str:
    date = _now().strftime("%B %d, %Y")
    entries = load_deprecations()

    def tbl(items):
        return _md_table(["Provider", "Model", "Retires", "Migrate to", "Source"],
                         [[d.provider, d.model, d.shutdown or "TBA", d.replacement or "—", d.source_url] for d in items])
    return "\n".join(["# AI model deprecation & migration calendar",
                      f"Updated {date}. Verified from official provider docs — nothing estimated.", "",
                      "## Upcoming retirements", tbl(upcoming(entries)), "",
                      "## Recently retired", tbl(recent(entries)), "", f"Source: {BASE_URL}/deprecations"])


def changelog_md(db_path: str) -> str:
    with connect(db_path) as conn:
        rows = conn.execute("SELECT provider, region, probe_type, ttfb_ms, ttft_ms, status, ts "
                            "FROM measurements ORDER BY ts DESC LIMIT 20").fetchall()
    tbl = _md_table(["Time (UTC)", "Provider", "Region", "Probe", "Latency", "Status"],
                    [[r["ts"][:19], r["provider"], r["region"], r["probe_type"],
                      f"{(r['ttfb_ms'] or r['ttft_ms'] or 0):.0f} ms", r["status"]] for r in rows])
    return f"# {SITE_NAME} — changelog\n\nMost recent probe results.\n\n{tbl}\n\nSource: {BASE_URL}/changelog"


def index_md(regions: list[str], providers: list[str]) -> str:
    date = _now().strftime("%B %d, %Y")
    reg = "\n".join(f"- [Fastest AI API from {_label(r)}]({BASE_URL}/region/{r})" for r in regions)
    prov = "\n".join(f"- [{p}]({BASE_URL}/provider/{p})" for p in providers)
    return (f"# {SITE_NAME}\nUpdated {date}. Independent, provider-neutral latency and uptime of {len(providers)} "
            f"AI inference APIs, measured from {len(regions)} regions with distributed probes and updated "
            f"automatically. Figures are measured, not scraped.\n\n## By region\n{reg}\n\n## By provider\n{prov}\n\n"
            f"## More\n- [AI model deprecation & migration calendar]({BASE_URL}/deprecations)\n"
            f"- [llms.txt]({BASE_URL}/llms.txt) · [llms-full.txt]({BASE_URL}/llms-full.txt)\n")


def robots_txt() -> str:
    ai = ["GPTBot", "OAI-SearchBot", "ChatGPT-User", "PerplexityBot", "Perplexity-User", "ClaudeBot",
          "Claude-Web", "Claude-User", "Google-Extended", "Applebot-Extended", "CCBot", "Amazonbot",
          "meta-externalagent", "Bytespider"]
    blocks = "".join(f"User-agent: {b}\nAllow: /\n\n" for b in ai)
    return (f"# AI crawlers welcome — this site exists to be cited by AI answer engines.\n"
            f"User-agent: *\nAllow: /\n\n{blocks}Sitemap: {BASE_URL}/sitemap.xml\n")


def llms_full_txt(db_path: str, regions: list[str], providers: list[str]) -> str:
    """Full site content as markdown — for AI agents (llmstxt.org)."""
    parts = [index_md(regions, providers)]
    for r in regions:
        parts += ["\n---\n", region_md(db_path, r)]
    for p in providers:
        parts += ["\n---\n", provider_md(db_path, p, regions)]
    parts += ["\n---\n", deprecations_md()]
    return "\n".join(parts)


# ─────────────── Agent-Ready: real JSON API + OpenAPI + RFC 9727 catalog ───────────────

def rankings_json(db_path: str, regions: list[str], providers: list[str]) -> str:
    """Real ranking data as JSON — agents/developers can read it programmatically (CC-BY-4.0)."""
    data = {
        "generated": _now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": BASE_URL,
        "license": "CC-BY-4.0",
        "method": "distributed edge (DNS→TCP→TLS→TTFB) + inference (TTFT) probes, last 24h",
        "unit": "milliseconds",
        "providers_tracked": len(providers),
        "regions": {},
    }
    for r in regions:
        net = ttfb_ranking(db_path, r, 24, "network")
        data["regions"][r] = {
            "label": _label(r),
            "network": [{"rank": i, "provider": x.provider, "p50_ms": round(x.p50_ms),
                         "p95_ms": round(x.p95_ms), "uptime_pct": round(x.ok_rate), "samples": x.samples}
                        for i, x in enumerate(net, 1)],
        }
    return json.dumps(data, indent=2)


def openapi_json() -> str:
    spec = {
        "openapi": "3.1.0",
        "info": {
            "title": f"{SITE_NAME} API",
            "version": _now().date().isoformat(),
            "description": ("Read-only, provider-neutral latency (TTFB/TTFT) and uptime of AI inference APIs, "
                            "measured from distributed regions. Free, CC-BY-4.0. Pages also support markdown "
                            "content negotiation: send `Accept: text/markdown` to any page URL for a markdown body."),
            "license": {"name": "CC-BY-4.0", "url": "https://creativecommons.org/licenses/by/4.0/"},
        },
        "servers": [{"url": BASE_URL}],
        "paths": {
            "/api/rankings.json": {
                "get": {
                    "operationId": "getRankings",
                    "summary": "Provider latency & uptime rankings for every region (last 24h)",
                    "responses": {"200": {"description": "Ranking data",
                                          "content": {"application/json": {}}}},
                }
            }
        },
    }
    return json.dumps(spec, indent=2)


def api_catalog_json() -> str:
    """RFC 9727 linkset — entry point for automated API discovery by agents."""
    return json.dumps({"linkset": [{
        "anchor": f"{BASE_URL}/api/rankings.json",
        "service-desc": [{"href": f"{BASE_URL}/openapi.json", "type": "application/vnd.oai.openapi+json"}],
        "service-doc": [{"href": f"{BASE_URL}/", "type": "text/html"}],
        "service-meta": [{"href": f"{BASE_URL}/llms.txt", "type": "text/plain"}],
        "status": [{"href": f"{BASE_URL}/api/rankings.json"}],
    }]}, indent=2)


def skill_md() -> str:
    """A real Agent Skill: how to fetch the latency data programmatically (agentskills.io)."""
    return (
        "# AI API Latency Lookup\n\n"
        "Look up independent, measured latency (time-to-first-byte p50/p95) and uptime rankings of "
        "AI inference API providers, by region, from llmlatency.dev. Data is measured by distributed "
        "probes and refreshed automatically — not scraped.\n\n"
        "## Data\n\n"
        f"- Machine-readable rankings (JSON): `GET {BASE_URL}/api/rankings.json`\n"
        f"- OpenAPI description: `{BASE_URL}/openapi.json`\n"
        f"- Any page as markdown: send `Accept: text/markdown` to any page URL, or append `.md`\n"
        f"- Full corpus as markdown: `{BASE_URL}/llms-full.txt`\n\n"
        "## Regions\n\n"
        "`eu-hetzner` (Europe), `us-central` (US), `ap-tokyo` (Asia), `sa-east` (South America).\n\n"
        "## Example\n\n"
        "To answer \"which AI API is fastest from Europe\", fetch "
        f"`{BASE_URL}/api/rankings.json` and read `regions['eu-hetzner'].network[0]` "
        "(lowest p50 time-to-first-byte). License: CC-BY-4.0.\n"
    )


def agent_skills_index(skill_text: str) -> str:
    """Agent Skills Discovery index (Cloudflare RFC v0.2.0) — with a real sha256 of the skill file."""
    digest = hashlib.sha256(skill_text.encode("utf-8")).hexdigest()
    return json.dumps({
        "$schema": "https://raw.githubusercontent.com/cloudflare/agent-skills-discovery-rfc/main/schema.json",
        "version": "0.2.0",
        "skills": [{
            "name": "ai-api-latency-lookup",
            "type": "text/markdown",
            "description": ("Look up measured latency (TTFB p50/p95) and uptime rankings of AI inference API "
                            "providers by region, from llmlatency.dev."),
            "url": f"{BASE_URL}/skill-latency.md",
            "sha256": digest,
        }],
    }, indent=2)


# Cloudflare Pages advanced-mode worker: markdown content-negotiation (Accept: text/markdown),
# RFC 8288 Link headers on every response, RFC 9727 api-catalog with the correct media type.
_WORKER_JS = r'''const LINKS = [
  '</.well-known/api-catalog>; rel="api-catalog"',
  '</openapi.json>; rel="service-desc"; type="application/vnd.oai.openapi+json"',
  '</llms.txt>; rel="llms-txt"',
  '</llms-full.txt>; rel="alternate"; type="text/markdown"',
  '</sitemap.xml>; rel="sitemap"',
];
function addLinks(h) { for (const l of LINKS) h.append('Link', l); }
function jrpc(obj, status) {
  return new Response(JSON.stringify(obj), { status: status || 200, headers: { 'Content-Type': 'application/json' } });
}
const MCP_TOOL = {
  name: 'get_ai_api_latency',
  description: 'Measured latency (TTFB p50/p95) and uptime rankings of AI inference API providers by region, from llmlatency.dev.',
  inputSchema: { type: 'object', properties: { region: { type: 'string', description: 'eu-hetzner, us-central, ap-tokyo or sa-east; omit for all' } } },
};
async function handleMcp(request, env, url) {
  if (request.method !== 'POST') {
    return new Response('MCP Streamable HTTP endpoint — POST JSON-RPC 2.0 here.', {
      status: 405, headers: { 'Allow': 'POST', 'Content-Type': 'text/plain' } });
  }
  let msg;
  try { msg = await request.json(); } catch (e) { return jrpc({ jsonrpc: '2.0', id: null, error: { code: -32700, message: 'Parse error' } }); }
  const id = (msg && msg.id !== undefined) ? msg.id : null;
  const method = msg && msg.method;
  if (method === 'initialize') {
    return jrpc({ jsonrpc: '2.0', id, result: { protocolVersion: '2025-06-18', capabilities: { tools: {} }, serverInfo: { name: 'llmlatency-mcp', version: '1.0.0' } } });
  }
  if (method === 'tools/list') {
    return jrpc({ jsonrpc: '2.0', id, result: { tools: [MCP_TOOL] } });
  }
  if (method === 'tools/call') {
    const args = (msg.params && msg.params.arguments) || {};
    const r = await env.ASSETS.fetch(new URL('/api/rankings.json', url).toString());
    const data = await r.json();
    const reg = args.region;
    const out = (reg && data.regions && data.regions[reg]) ? data.regions[reg] : (data.regions || data);
    return jrpc({ jsonrpc: '2.0', id, result: { content: [{ type: 'text', text: JSON.stringify(out) }] } });
  }
  if (typeof method === 'string' && method.indexOf('notifications/') === 0) {
    return new Response(null, { status: 202 });
  }
  return jrpc({ jsonrpc: '2.0', id, error: { code: -32601, message: 'Method not found' } });
}
export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const accept = request.headers.get('Accept') || '';
    // Real MCP server (Streamable HTTP transport) — exposes the latency data as an MCP tool
    if (url.pathname === '/mcp') return handleMcp(request, env, url);
    // Well-known JSON endpoints served with correct media type (from non-dot assets for reliability)
    const WK = {
      '/.well-known/api-catalog': ['/api-catalog.json', 'application/linkset+json; charset=utf-8'],
      '/.well-known/agent-skills/index.json': ['/agent-skills-index.json', 'application/json; charset=utf-8'],
      '/.well-known/mcp/server-card.json': ['/mcp-server-card.json', 'application/json; charset=utf-8'],
    };
    if (WK[url.pathname]) {
      const [asset, ctype] = WK[url.pathname];
      const r = await env.ASSETS.fetch(new URL(asset, url).toString());
      const h = new Headers(r.headers);
      h.set('Content-Type', ctype);
      addLinks(h);
      return new Response(r.body, { status: r.status, headers: h });
    }
    // Markdown content negotiation: Accept: text/markdown on a clean page URL -> serve its .md twin.
    // Only for extension-less paths (real .md files are served natively); guard on the twin actually
    // being markdown so a soft-404 HTML fallback is never mislabeled as text/markdown.
    if (request.method === 'GET' && accept.includes('text/markdown')) {
      let target = null;
      const p = url.pathname;
      if (p === '/') target = '/index.md';
      else if (!/\.[a-z0-9]+$/i.test(p)) target = p.replace(/\/+$/, '') + '.md';
      if (target) {
        const md = await env.ASSETS.fetch(new URL(target, url).toString());
        const ct = md.headers.get('Content-Type') || '';
        if (md.status === 200 && ct.includes('markdown')) {
          const h = new Headers(md.headers);
          h.set('Content-Type', 'text/markdown; charset=utf-8');
          h.set('Vary', 'Accept');
          addLinks(h);
          return new Response(md.body, { status: 200, headers: h });
        }
      }
    }
    const res = await env.ASSETS.fetch(request);
    const h = new Headers(res.headers);
    addLinks(h);
    return new Response(res.body, { status: res.status, statusText: res.statusText, headers: h });
  },
};
'''


def mcp_server_card_json() -> str:
    """MCP Server Card (SEP-1649) — describes the real /mcp endpoint (Streamable HTTP)."""
    return json.dumps({
        "protocolVersion": "2025-06-18",
        "serverInfo": {"name": "llmlatency-mcp", "version": "1.0.0",
                       "description": "AI inference API latency & uptime by region (measured)."},
        "endpoint": f"{BASE_URL}/mcp",
        "transport": "streamable-http",
        "capabilities": {"tools": {"listChanged": False}},
        "tools": [{
            "name": "get_ai_api_latency",
            "description": "Measured latency (TTFB p50/p95) and uptime rankings of AI inference API providers by region.",
        }],
    }, indent=2)


def build(db_path: str = DB_PATH, out_dir: str = "site") -> int:
    regions = _distinct(db_path, "region")
    providers = _distinct(db_path, "provider")
    out = Path(out_dir)
    (out / "region").mkdir(parents=True, exist_ok=True)
    (out / "provider").mkdir(parents=True, exist_ok=True)

    urls = [f"{BASE_URL}/", f"{BASE_URL}/changelog", f"{BASE_URL}/deprecations"]
    (out / "index.html").write_text(index_page(db_path, regions, providers), encoding="utf-8")
    (out / "index.md").write_text(index_md(regions, providers), encoding="utf-8")
    (out / "changelog.html").write_text(changelog_page(db_path), encoding="utf-8")
    (out / "changelog.md").write_text(changelog_md(db_path), encoding="utf-8")
    (out / "deprecations.html").write_text(deprecations_page(), encoding="utf-8")
    (out / "deprecations.md").write_text(deprecations_md(), encoding="utf-8")
    # For AI agents: index (llms.txt), full content (llms-full.txt), robots.txt
    (out / "llms.txt").write_text(llms_txt(regions, providers), encoding="utf-8")
    (out / "llms-full.txt").write_text(llms_full_txt(db_path, regions, providers), encoding="utf-8")
    (out / "robots.txt").write_text(robots_txt(), encoding="utf-8")
    # Agent-Ready: real JSON API + OpenAPI + RFC 9727 catalog + Pages worker (md negotiation, Link headers)
    (out / "api").mkdir(parents=True, exist_ok=True)
    (out / ".well-known").mkdir(parents=True, exist_ok=True)
    (out / "api" / "rankings.json").write_text(rankings_json(db_path, regions, providers), encoding="utf-8")
    (out / "openapi.json").write_text(openapi_json(), encoding="utf-8")
    _catalog = api_catalog_json()
    (out / "api-catalog.json").write_text(_catalog, encoding="utf-8")           # worker serves it as linkset+json
    (out / ".well-known" / "api-catalog").write_text(_catalog, encoding="utf-8")  # direct access (if the dot-dir is served)
    _skill = skill_md()
    (out / "skill-latency.md").write_text(_skill, encoding="utf-8")
    (out / "agent-skills-index.json").write_text(agent_skills_index(_skill), encoding="utf-8")
    (out / "mcp-server-card.json").write_text(mcp_server_card_json(), encoding="utf-8")
    (out / "_worker.js").write_text(_WORKER_JS, encoding="utf-8")

    for r in regions:
        (out / "region" / f"{r}.html").write_text(region_page(db_path, r), encoding="utf-8")
        (out / "region" / f"{r}.md").write_text(region_md(db_path, r), encoding="utf-8")
        urls.append(f"{BASE_URL}/region/{r}")
    for p in providers:
        (out / "provider" / f"{p}.html").write_text(provider_page(db_path, p, regions), encoding="utf-8")
        (out / "provider" / f"{p}.md").write_text(provider_md(db_path, p, regions), encoding="utf-8")
        urls.append(f"{BASE_URL}/provider/{p}")

    (out / "sitemap.xml").write_text(sitemap(urls), encoding="utf-8")
    logger.info("built {} pages ({} regions, {} providers) → {}/", len(urls), len(regions), len(providers), out_dir)
    return len(urls)


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate static, LLM-citable site from measurements")
    ap.add_argument("--db", default=DB_PATH)
    ap.add_argument("--out", default="site")
    args = ap.parse_args()
    n = build(args.db, args.out)
    print(f"Built {n} pages into {args.out}/  (set BASE_URL env for production URLs)")


if __name__ == "__main__":
    main()
