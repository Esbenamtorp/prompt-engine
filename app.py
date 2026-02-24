import os, re, time, json
from json_repair import repair_json
from urllib.parse import urlparse, urljoin
from flask import Flask, request, jsonify, render_template
import requests
from bs4 import BeautifulSoup

app = Flask(__name__)

API_KEY   = os.environ.get("ANTHROPIC_API_KEY", "")
MAX_PAGES = 3

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

def fetch(url):
    r = requests.get(url, headers=HEADERS, timeout=12)
    r.raise_for_status()
    return r.text

def parse(html, url):
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script","style","noscript","nav","footer","header","aside","iframe","svg"]):
        tag.decompose()
    title = (soup.find("title") or soup.find("h1") or "")
    title = title.get_text(strip=True) if hasattr(title, "get_text") else ""
    meta  = ""
    for m in soup.find_all("meta"):
        n = m.get("name","").lower(); p = m.get("property","").lower()
        if n in ("description","og:description") or p == "og:description":
            meta = m.get("content",""); break
    headings = []
    for lvl in ["h1","h2","h3"]:
        for h in soup.find_all(lvl):
            t = h.get_text(strip=True)
            if t and len(t) > 3:
                headings.append(f"[{lvl.upper()}] {t}")
    body = " ".join(soup.get_text(separator=" ", strip=True).split())
    return {"url": url, "title": title[:200], "meta": meta[:400],
            "headings": headings[:40], "body": body[:4000]}

def internal_links(html, base):
    soup = BeautifulSoup(html, "html.parser")
    origin = urlparse(base).netloc
    links  = set()
    for a in soup.find_all("a", href=True):
        href = a["href"].split("#")[0].split("?")[0]
        if not href: continue
        try:
            full = urljoin(base, href)
            if urlparse(full).netloc == origin and full != base:
                links.add(full)
        except: pass
    return list(links)

def crawl(start_url):
    pages   = []
    main_html = fetch(start_url)
    pages.append(parse(main_html, start_url))
    all_links = internal_links(main_html, start_url)
    pri_re    = re.compile(r"/(about|om|services|ydelser|produkt|product|platform|pricing|pris|why|solution)", re.I)
    priority  = [l for l in all_links if pri_re.search(l)]
    rest      = [l for l in all_links if l not in priority]
    candidates = (priority + rest)[:MAX_PAGES]
    for link in candidates:
        try:
            html = fetch(link)
            pages.append(parse(html, link))
            time.sleep(0.3)
        except: pass
    return pages

def build_context(pages):
    parts = []
    for p in pages:
        h = "\n".join(p["headings"][:15]) or "—"
        parts.append(
            f"--- SIDE: {p['url']} ---\n"
            f"Titel: {p['title']}\nMeta: {p['meta']}\n"
            f"Headings:\n{h}\nIndhold:\n{p['body'][:1600]}"
        )
    return "\n\n".join(parts)

SYSTEM = """Du er verdens bedste AEO-specialist (Answer Engine Optimization).
Analyser en hjemmeside og identificer de 25 vigtigste prompts, som målgruppen
ville skrive i AI-assistenter (ChatGPT, Claude, Perplexity, Gemini, Copilot)
for at finde en virksomhed som denne.
Svar KUN med valid JSON – ingen markdown, ingen kodeblokke, ingen forklaring."""

USER_TPL = """Analyser dette website og generer præcis 25 AEO-prompts.

WEBSITEINDHOLD:
{context}

Returner PRÆCIST dette JSON (ingen tekst udenfor JSON):
{{
  "company": "Firmanavn",
  "domain": "domæne.dk",
  "industry": "Branche / niche",
  "target_audience": "Kortfattet beskrivelse af målgruppen",
  "prompts": [
    {{
      "id": 1,
      "prompt": "Den præcise prompt en bruger ville skrive",
      "stage": "awareness | consideration | decision",
      "priority": "high | medium",
      "rationale": "Én sætning: hvorfor denne prompt er vigtig"
    }}
  ]
}}

Krav:
- Brug websitets sprog (dansk -> dansk, engelsk -> engelsk)
- Prompts lyder naturlige som rigtige brugere formulerer sig
- Ca. 8 awareness, 10 consideration, 7 decision
- 10 "high" priority og 15 "medium"
- Mix af korte og lange conversational prompts"""

def call_claude(context):
    resp = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
                        "model": "claude-haiku-4-5-20251001",
            "max_tokens": 4096,
            "system": SYSTEM,
            "messages": [{"role": "user", "content": USER_TPL.format(context=context)}],
        },
        timeout=60,
    )
    if resp.status_code != 200:
        err = resp.json().get("error", {}).get("message", f"API fejl {resp.status_code}")
        raise Exception(err)
    txt   = resp.json()["content"][0]["text"]
    clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", txt.strip(), flags=re.M)
        return json.loads(repair_json(clean))

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/generate", methods=["POST"])
def generate():
    if not API_KEY:
        return jsonify({"error": "API-noegle ikke konfigureret paa serveren."}), 500
    body = request.get_json(force=True) or {}
    url  = (body.get("url") or "").strip()
    if not url:
        return jsonify({"error": "URL mangler"}), 400
    if not url.startswith("http"):
        url = "https://" + url
    try:
        pages   = crawl(url)
        context = build_context(pages)
        data    = call_claude(context)
        data["pages_crawled"] = len(pages)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
