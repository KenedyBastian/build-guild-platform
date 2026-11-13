"""Standalone standard-library site builder, also shipped into every new guild."""
import html
import json
import re
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit


def text(value, name, maximum=300, required=True):
    if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
        raise ValueError(f"{name}: expected {'nonempty ' if required else ''}text, max {maximum} characters")
    if any(ord(c) < 32 and c not in "\n\t" for c in value):
        raise ValueError(f"{name}: control characters are not allowed")
    return value.strip()


def url(value, name):
    value = text(value, name, 1000, False)
    if not value:
        return ""
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError(f"{name}: use an HTTPS URL without credentials")
    if any(c.isspace() for c in value) or "\\" in value:
        raise ValueError(f"{name}: whitespace and backslashes are not allowed")
    return value


def keys(obj, allowed, name):
    if not isinstance(obj, dict) or set(obj) - set(allowed):
        raise ValueError(f"{name}: invalid object or unknown fields")


def validate_items(data):
    keys(data, {"schema_version", "items"}, "content")
    if data.get("schema_version") != 1 or not isinstance(data.get("items"), list) or len(data["items"]) > 100:
        raise ValueError("content: schema_version must be 1 with at most 100 items")
    result, seen = [], set()
    for item in data["items"]:
        keys(item, {"id", "kind", "title", "body", "url", "date"}, "item")
        ident = text(item.get("id"), "id", 64)
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", ident) or ident in seen:
            raise ValueError("item: invalid or duplicate id")
        seen.add(ident)
        kind = item.get("kind")
        if kind not in ("news", "event"):
            raise ValueError("kind: expected news or event")
        when = text(item.get("date"), "date", 10)
        if date.fromisoformat(when).isoformat() != when:
            raise ValueError("date: expected YYYY-MM-DD")
        result.append(dict(id=ident, kind=kind, title=text(item.get("title"), "title", 150),
                           body=text(item.get("body"), "body", 3000),
                           url=url(item.get("url", ""), "url"), date=when))
    return {"schema_version": 1, "items": result}


def validate_guild(data):
    keys(data, {"name", "state", "description", "owner", "slug", "support", "meeting",
               "meeting_url", "accent"}, "guild")
    result = {key: text(data.get(key), key, limit) for key, limit in
              (("name", 120), ("state", 2), ("description", 2000), ("owner", 39))}
    base_name = text(re.sub(r"(?:^|\s+)build\s+guild$", "", result["name"], flags=re.I), "base guild name", 100)
    result["name"] = text(base_name + " Build Guild", "display name", 120)
    slug_base = re.sub(r"[^a-z0-9]+", "-", base_name.lower()).strip("-")[:68].rstrip("-")
    default_slug = slug_base + "-build-guild"
    result["slug"] = text(data.get("slug") or default_slug, "slug", 80)
    states = "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC PR GU VI AS MP".split()
    if result["state"] not in states:
        raise ValueError("state: expected a US state or territory abbreviation")
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?", result["owner"]):
        raise ValueError("owner: invalid GitHub owner")
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,79}", result["slug"]):
        raise ValueError("slug: use lowercase letters, digits and hyphens; project sites only")
    support = data.get("support")
    if not isinstance(support, list) or not 1 <= len(support) <= 12:
        raise ValueError("support: provide 1-12 public teams or contacts")
    result["support"] = []
    for contact in support:
        keys(contact, {"name", "role", "url"}, "support")
        result["support"].append(dict(name=text(contact.get("name"), "support name", 100),
                                      role=text(contact.get("role"), "support role", 150),
                                      url=url(contact.get("url", ""), "support URL")))
    result["meeting"] = text(data.get("meeting", ""), "meeting", 500, False)
    result["meeting_url"] = url(data.get("meeting_url", ""), "meeting URL")
    result["accent"] = text(data.get("accent", "#8b72ff"), "accent", 7)
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", result["accent"]):
        raise ValueError("accent: expected #RRGGBB")
    return result


STYLE = """
:root{color-scheme:light dark;--bg:#0b1021;--panel:#172039;--text:#f5f7ff;--muted:#c3cee4;--link:#9fdfff}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--bg);color:var(--text);font:1.05rem/1.65 system-ui,sans-serif}
a{color:var(--link);text-decoration:underline;text-underline-offset:.2em}a:focus-visible{outline:3px solid #e8ad16;outline-offset:5px}
.wrap{width:min(1120px,92%);margin:auto}header{border-bottom:1px solid #52617d;padding:1.2rem 0}
nav{display:flex;flex-wrap:wrap;gap:1.2rem}main{padding-bottom:3rem}.hero{padding:4rem 0 2rem;border-top:6px solid var(--accent)}
h1{font-size:clamp(2.5rem,7vw,4.7rem);line-height:1.1;max-width:18ch}h2{font-size:1.7rem}
.eyebrow{letter-spacing:.13em;text-transform:uppercase}.intro{max-width:65ch;color:var(--muted);white-space:pre-wrap}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,290px),1fr));gap:1.2rem}
article,.meeting{background:var(--panel);border:1px solid #52617d;border-radius:1rem;padding:1.5rem;overflow-wrap:anywhere}
article p,.meeting p{white-space:pre-wrap}section{margin:2.5rem 0}small,time{color:var(--muted)}
footer{padding:2rem 0;border-top:1px solid #52617d}.skip{position:absolute;top:-100px}.skip:focus{top:0;background:var(--bg);padding:1rem;z-index:1}
@media(prefers-color-scheme:light){:root{--bg:#f4f6fb;--panel:#fff;--text:#15213a;--muted:#40516d;--link:#005a91}}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}}
"""


def render(guild, local, network):
    guild = validate_guild(guild)
    local, network = validate_items(local), validate_items(network)
    esc = lambda value: html.escape(value, quote=True)

    def link(label, target):
        return f'<a href="{esc(target)}" rel="noopener noreferrer">{esc(label)}</a>' if target else esc(label)

    def cards(content):
        if not content["items"]:
            return "<p>No announcements yet. Check back soon.</p>"
        return '<div class="grid">' + "".join(
            f'<article><small>{esc(i["kind"].title())}</small><h3>{link(i["title"], i["url"])}</h3>'
            f'<time datetime="{i["date"]}">{i["date"]}</time><p>{esc(i["body"])}</p></article>'
            for i in sorted(content["items"], key=lambda i: (i["date"], i["id"]), reverse=True)) + "</div>"

    support = '<div class="grid">' + "".join(
        f'<article><h3>{link(c["name"], c["url"])}</h3><p>{esc(c["role"])}</p></article>'
        for c in guild["support"]) + "</div>"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="description" content="{esc(guild['description'])}"><title>{esc(guild['name'])}</title>
<meta property="og:title" content="{esc(guild['name'])}"><meta name="application-name" content="{esc(guild['name'])}">
<style>{STYLE}</style></head><body style="--accent:{guild['accent']}">
<a class="skip" href="#main">Skip to content</a>
<header><nav class="wrap" aria-label="Main"><a href="#about">About</a><a href="#local">Guild updates</a><a href="#network">Network</a><a href="#support">Support</a></nav></header>
<main id="main" class="wrap"><section class="hero" id="about"><p class="eyebrow">{esc(guild['state'])} / Build Guild</p>
<h1>{esc(guild['name'])}</h1><p class="intro">{esc(guild['description'])}</p></section>
<section class="meeting" aria-labelledby="meeting-title"><h2 id="meeting-title">Build together</h2>
<p>{esc(guild['meeting']) or 'Meeting details will be announced here.'}</p>
{link('Public meeting or registration details', guild['meeting_url']) if guild['meeting_url'] else ''}</section>
<section id="local"><h2>From your guild</h2>{cards(local)}</section>
<section id="network"><h2>Across the Build Guild network</h2>{cards(network)}</section>
<section id="support"><h2>Your support team</h2>{support}</section></main>
<footer><div class="wrap">Built for builders. Local identity. Shared learning.</div></footer></body></html>"""


def build(root):
    load = lambda name: json.loads((root / "data" / name).read_text(encoding="utf-8"))
    page = render(load("guild.json"), load("local.json"), load("network.json"))
    output = root / "_site"
    output.mkdir(exist_ok=True)
    (output / "index.html").write_text(page, encoding="utf-8")
    (output / ".nojekyll").write_text("", encoding="utf-8")


if __name__ == "__main__":
    build(Path(__file__).resolve().parents[1])
