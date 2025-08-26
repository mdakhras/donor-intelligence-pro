import os
import re
import json
from datetime import datetime
from typing import List, Dict, Tuple
import logging
import requests
from bs4 import BeautifulSoup
from duckduckgo_search import DDGS

RAW_INPUTS_DIR = "data/raw_inputs"
os.makedirs(RAW_INPUTS_DIR, exist_ok=True)

# ---------- ENV KNOBS ----------
DOMAIN_PINNING = os.getenv("DONORINTEL_DOMAIN_PINNING", "1") not in ("0", "false", "False")
REQUIRE_NAME_IN_BODY_NONPINNED = os.getenv("DONORINTEL_REQUIRE_NAME_IN_BODY", "1") not in ("0", "false", "False")
MAX_RESULTS_DEFAULT = int(os.getenv("DONORINTEL_MAX_RESULTS", "5"))
QUERY_LANG = os.getenv("DONORINTEL_QUERY_LANG", "en-us")  # ddg language hint (not strict)
SAFESEARCH = os.getenv("DONORINTEL_SAFESEARCH", "off")     # off|moderate|strict
BODY_LINE_LIMIT = int(os.getenv("DONORINTEL_BODY_LINE_LIMIT", "200"))
DISABLE_PREFILTER_FOR_PINNED = os.getenv("DONORINTEL_DISABLE_PREFILTER_FOR_PINNED", "1") not in ("0", "false", "False")

logging.info(f"[web_scraper] Settings: DOMAIN_PINNING={DOMAIN_PINNING}, REQUIRE_NAME_IN_BODY_NONPINNED={REQUIRE_NAME_IN_BODY_NONPINNED}, MAX_RESULTS_DEFAULT={MAX_RESULTS_DEFAULT}, QUERY_LANG={QUERY_LANG}, SAFESEARCH={SAFESEARCH}, BODY_LINE_LIMIT={BODY_LINE_LIMIT}, DISABLE_PREFILTER_FOR_PINNED={DISABLE_PREFILTER_FOR_PINNED}")

# Optional registry for trusted domains (extend over time)
DONOR_REGISTRY: Dict[str, Dict[str, List[str]]] = {
    "Swedish International Development Cooperation Agency (Sida)": {
        "synonyms": ["Sida", "Swedish International Development Cooperation Agency", "Government of Sweden"],
        "domains": ["sida.se", "government.se", "swedenabroad.se"],
    },
    "United States Agency for International Development (USAID)": {
        "synonyms": ["USAID", "United States Agency for International Development"],
        "domains": ["usaid.gov"],
    },
    "Foreign, Commonwealth & Development Office (FCDO)": {
        "synonyms": ["FCDO", "UK aid", "UK Foreign, Commonwealth & Development Office"],
        "domains": ["gov.uk"],
    },
    "Deutsche Gesellschaft für Internationale Zusammenarbeit (GIZ)": {
        "synonyms": ["GIZ", "Deutsche Gesellschaft fuer Internationale Zusammenarbeit"],
        "domains": ["giz.de"],
    },
    "United Nations Development Programme (UNDP)": {
        "synonyms": ["UNDP", "United Nations Development Programme"],
        "domains": ["undp.org"],
    },
    "United Nations Children's Fund (UNICEF)": {
        "synonyms": ["UNICEF"],
        "domains": ["unicef.org"],
    },
    "World Bank": {
        "synonyms": ["World Bank", "IBRD", "IDA"],
        "domains": ["worldbank.org"],
    },
    # Add more as needed...
}

# Drift magnets (only considered off-topic if they are NOT the requested donor)
OFF_TOPIC_BASE = [
    "Bill & Melinda Gates Foundation",
    "Gates Foundation",
    "Ford Foundation",
    "Open Society Foundations",
    "Open Society Foundation",
    "OSF",
]

STOPWORDS = {
    "and","of","for","the","a","an","&",
    "agency","foundation","fund","bank","ministry","department",
    "international","cooperation","development","affairs","programme","program",
    "office","directorate","general","state","government","governments",
}

# ---------- BASIC HELPERS ----------
def _strip_trailing_digits(name: str) -> str:
    return re.sub(r"\s+\d+$", "", (name or "").strip())

def _split_paren(name: str) -> Tuple[str, str]:
    m = re.match(r"^(.*)\(([^)]+)\)\s*$", name.strip())
    return (m.group(1).strip(), m.group(2).strip()) if m else (name, "")

def _make_acronym(name: str) -> str:
    tokens = re.split(r"[\s\-]+", re.sub(r"[^\w\s\-]", " ", name))
    letters = [w[0] for w in tokens if w and re.sub(r"[^a-z0-9]+", "", w.lower()) not in STOPWORDS]
    ac = "".join(letters).upper()
    return ac if len(ac) >= 3 else ""

def _registry_lookup(name: str) -> Tuple[str, List[str], List[str]]:
    clean = _strip_trailing_digits(name)
    base, par = _split_paren(clean)

    # reverse map for registry lookup
    rev = {}
    for canonical, meta in DONOR_REGISTRY.items():
        all_names = {canonical} | set(meta.get("synonyms", []))
        for n in all_names:
            rev[re.sub(r"[^a-z0-9]+", " ", n.lower()).strip()] = canonical

    for cand in {clean, base, par}:
        key = re.sub(r"[^a-z0-9]+", " ", cand.lower()).strip()
        if key in rev:
            canon = rev[key]
            meta = DONOR_REGISTRY[canon]
            syns = list({canon, *meta.get("synonyms", [])})
            return canon, syns, meta.get("domains", [])

    # unknown donor → auto synonyms
    syns = set([clean])
    if base: syns.add(base)
    if par: syns.add(par)
    ac = par or _make_acronym(base or clean)
    if ac: syns.add(ac)
    return clean, list(syns), []

def _off_topic_for(canonical: str, synonyms: List[str]) -> List[str]:
    synset = {s.lower() for s in synonyms} | {canonical.lower()}
    return [d for d in OFF_TOPIC_BASE if d.lower() not in synset]

def _site_filters(domains: List[str]) -> str:
    if not (DOMAIN_PINNING and domains):
        return ""
    return " (" + " OR ".join([f"site:{d}" for d in domains]) + ")"

def _contains_any_text(text: str, needles: List[str]) -> bool:
    if not text or not needles:
        return False
    s = text.lower()
    return any(n.lower() in s for n in needles)

def _contains_any_word(text: str, needles: List[str]) -> bool:
    if not text or not needles:
        return False
    t = text.lower()
    for n in needles:
        if not n: 
            continue
        # word boundary-ish: allow acronym or phrase
        pat = r"\b" + re.escape(n.lower()) + r"\b"
        if re.search(pat, t):
            return True
    return False

def _domain_of(url: str) -> str:
    try:
        from urllib.parse import urlparse
        return urlparse(url).netloc.lower()
    except Exception:
        return ""

# ---------- FETCH ----------
def fetch_and_clean_content(url: str) -> str:
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (compatible; DonorIntelBot/1.0; +https://example.org/bot)",
            "Accept-Language": "en;q=0.9",
        }
        resp = requests.get(url, timeout=18, headers=headers)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # keep relevant meta
        metas = []
        for k in ["title","og:title","og:description","description"]:
            m = soup.find("meta", attrs={"name": k}) or soup.find("meta", property=k)
            if m and m.get("content"):
                metas.append(m.get("content"))
        title_tag = soup.find("title")
        if title_tag and title_tag.text:
            metas.insert(0, title_tag.text)

        for tag in soup(["script","style","noscript"]):
            tag.decompose()

        text = soup.get_text(separator="\n")
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        body = "\n".join(lines[:BODY_LINE_LIMIT])
        if metas:
            body = "\n".join(metas) + "\n\n" + body
        return body
    except Exception as e:
        return f"[Error fetching {url}]: {e}"

# ---------- MAIN ----------
def search_donor_articles(donor_name, region=None, theme=None, max_results=MAX_RESULTS_DEFAULT, save_to_disk=True):
    """
    Robust generic donor search with domain pinning, synonym expansion,
    relaxed filters for pinned domains, and multi-tier fallback queries.
    """
    canonical, synonyms, reg_domains = _registry_lookup(donor_name or "")
    off_topic = _off_topic_for(canonical, synonyms)
    site_q = _site_filters(reg_domains)

    # Build query tiers (no literal 'AND' — DDG treats it as a word)
    # Tier 1: precise + pinned domains
    base = [f"\"{canonical}\""]
    if theme:  base.append(f"\"{theme}\"")
    if region: base.append(f"\"{region}\"")
    kw_core = "(\"strategic plan\" OR \"funding priorities\" OR \"annual report\" OR strategy OR priorities OR grants)"
    q1 = " ".join(base + [kw_core]) + site_q

    # Tier 2: name + keywords (no domain pinning)
    q2 = " ".join(base + [kw_core])

    # Tier 3: synonyms OR-group + keywords (broad)
    syn_or = "(" + " OR ".join([f"\"{s}\"" for s in synonyms if s]) + ")"
    q3 = " ".join([syn_or, kw_core])

    # Tier 4: PDF bias fallback (annual report, strategy documents)
    pdf_kw = "(filetype:pdf OR \"annual report\" OR strategy)"
    q4 = " ".join([syn_or, pdf_kw]) + site_q

    queries = [q1, q2, q3, q4]

    results = []
    seen = set()

    def _log(msg):
        print(f"[web_scraper] {msg}")

    _log(f"Canonical: {canonical} | Synonyms: {synonyms} | Domains: {reg_domains}")
    _log(f"Queries:\n 1) {q1}\n 2) {q2}\n 3) {q3}\n 4) {q4}")

    with DDGS() as ddgs:
        for qi, q in enumerate(queries, start=1):
            if len(results) >= max_results:
                break
            try:
                # over-fetch to allow filtering
                hits = ddgs.text(
                    q,
                    safesearch=SAFESEARCH,
                    max_results=max(30, max_results * 5),
                    timelimit=None,  # you can set 365d if you want older docs
                    region=QUERY_LANG
                )
            except Exception as e:
                _log(f"DDG query failed (tier {qi}): {e}")
                continue

            tier_kept = tier_seen = 0
            for r in hits:
                url = r.get("href") or r.get("url") or r.get("link")
                title = (r.get("title") or "").strip()
                snippet = (r.get("body") or r.get("snippet") or "").strip()
                if not url:
                    continue

                nurl = url.split("#")[0]
                if nurl in seen:
                    continue

                tier_seen += 1
                dom = _domain_of(url)
                pinned = any(dom.endswith(d) for d in reg_domains) if reg_domains else False

                # ---------- PREFILTER ----------
                pre_blob = f"{title} {snippet} {url}"
                if not pinned or not DISABLE_PREFILTER_FOR_PINNED:
                    # require donor mention in title/snippet/url (word boundary)
                    if not _contains_any_word(pre_blob, synonyms):
                        continue
                # reject drift donors unless they are the target
                if _contains_any_text(pre_blob, off_topic):
                    continue

                # ---------- FETCH BODY ----------
                body = fetch_and_clean_content(url)

                # ---------- POSTFILTER ----------
                if not pinned:
                    # for non-pinned domains, require donor mention in body or title
                    if not (_contains_any_word(body, synonyms) or _contains_any_word(title, synonyms)):
                        continue
                else:
                    # pinned: accept even if body is sparse/error — but still drop obvious drift
                    if _contains_any_text(body, off_topic):
                        continue

                cleaned = {
                    "title": title,
                    "url": url,
                    "body": body,
                    "date": r.get("date") or datetime.utcnow().isoformat(),
                    "canonical": canonical,
                }
                results.append(cleaned)
                seen.add(nurl)
                tier_kept += 1

                if len(results) >= max_results:
                    break

            _log(f"Tier {qi}: seen={tier_seen}, kept={tier_kept}, total={len(results)}")

    if save_to_disk:
        filename = generate_filename(canonical, region, theme)
        filepath = os.path.join(RAW_INPUTS_DIR, filename)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        print(f"[📝] Saved {len(results)} items to {filepath}")

    # If still nothing, log the reasons likely responsible
    if not results:
        _log("No results kept. Likely causes: (1) donor name not appearing in snippets/body, (2) overly strict domain pinning, (3) dynamic pages blocked. Try setting DONORINTEL_DOMAIN_PINNING=0 or DONORINTEL_REQUIRE_NAME_IN_BODY=0 temporarily.")
    return results

def generate_filename(donor_name, region, theme):
    donor_slug = re.sub(r"[^a-zA-Z0-9]", "_", (donor_name or "").lower())
    region_slug = re.sub(r"[^a-zA-Z0-9]", "_", (region or "global").lower())
    theme_slug = re.sub(r"[^a-zA-Z0-9]", "_", (theme or "general").lower())
    ts = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    return f"{donor_slug}_{region_slug}_{theme_slug}_{ts}.json"
