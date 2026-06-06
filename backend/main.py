from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests
from bs4 import BeautifulSoup
from groq import Groq
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import json
from supabase import create_client
from dotenv import load_dotenv
import os
import unicodedata
import re

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

groq_clients = [
    Groq(api_key=os.getenv(f"GROQ_API_KEY{i}"))
    for i in range(1, 5)
]

supabase = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_SECRET_KEY")
)

# ---------------------------------------------------------------------------
# Domain-specific entity schemas
# Each domain defines the entity labels that make sense for its SEO context.
# The LLM is instructed to use ONLY these labels during extraction and filtering.
# ---------------------------------------------------------------------------

DOMAIN_SCHEMAS: dict[str, dict] = {
    "telecom": {
        "description": "telecommunications, mobile data plans, internet service, network carriers",
        "labels": {
            "BRAND":     "Mobile carriers, ISPs, or telecom companies (e.g. 中華電信, AT&T, StarHub)",
            "PLAN_TYPE": "Plan tiers or service types (e.g. 吃到飽, unlimited, prepaid, postpaid)",
            "DEVICE":    "Specific phone models or hardware (e.g. iPhone 16, Samsung Galaxy S25, 5G router)",
            "PRICE":     "Monthly fees or prices in currency only (e.g. NT$699, 月租費699元). Do NOT include data quotas (GB), speeds (Mbps/Gbps), or any non-monetary values.",
            "LOCATION":  "Countries, cities, or coverage areas relevant to the service",
        },
    },
    "ecommerce": {
        "description": "online shopping, product reviews, retail, buying guides, marketplace",
        "labels": {
            "BRAND":    "Retailers, manufacturers, or marketplace names",
            "PRODUCT":  "Specific product names or models being sold or reviewed",
            "PRICE":    "Prices, discounts, or promotional deals",
            "CATEGORY": "Product types or categories (e.g. laptops, running shoes)",
            "LOCATION": "Shipping regions or relevant store locations",
        },
    },
    "finance": {
        "description": "banking, investing, insurance, personal finance, financial products",
        "labels": {
            "BRAND":       "Banks, brokerages, insurance companies, or fintech platforms",
            "PRODUCT":     "Financial products (credit cards, funds, accounts, loans)",
            "REGULATION":  "Financial laws, compliance standards, or regulatory frameworks",
            "AMOUNT":      "Interest rates, fees, return percentages, or monetary thresholds",
            "INSTITUTION": "Central banks, stock exchanges, or regulatory agencies",
        },
    },
    "health": {
        "description": "healthcare, medicine, wellness, fitness, medical conditions and treatments",
        "labels": {
            "CONDITION":   "Medical conditions or diseases",
            "TREATMENT":   "Therapies, procedures, or medical interventions",
            "DRUG":        "Medications, supplements, or pharmaceutical products",
            "EXPERT":      "Named doctors, researchers, or medical professionals",
            "INSTITUTION": "Hospitals, clinics, research centers, or health agencies",
        },
    },
    "tech": {
        "description": "software, hardware, SaaS, AI, developer tools, cloud platforms",
        "labels": {
            "BRAND":      "Tech companies, software vendors, or platform names",
            "PRODUCT":    "Specific software products, hardware, or named platforms",
            "TECHNOLOGY": "Programming languages, frameworks, or technical paradigms (e.g. React, Kubernetes)",
            "EXPERT":     "Named developers, researchers, or tech thought leaders",
            "CONCEPT":    "Key technical concepts or methodologies central to the topic",
        },
    },
    "travel": {
        "description": "tourism, travel destinations, hotels, flights, vacation planning",
        "labels": {
            "BRAND":    "Airlines, hotel chains, or travel booking platforms",
            "LOCATION": "Destinations, cities, countries, or landmarks",
            "PRODUCT":  "Travel packages, visa types, or ticket classes",
            "PRICE":    "Fares, accommodation costs, or travel deals",
            "EVENT":    "Festivals, seasons, or events relevant to travel timing",
        },
    },
    "general": {
        "description": "general topic not clearly fitting a specific vertical",
        "labels": {
            "BRAND":    "Major brands, companies, or named organizations",
            "EXPERT":   "Notable named public figures or domain authorities",
            "PRODUCT":  "Named products or services",
            "CONCEPT":  "Key topical phrases or ideas central to the subject",
            "LOCATION": "Relevant geographic locations",
        },
    },
}


class SearchRequest(BaseModel):
    keyword: str


_TECH_STANDARDS = frozenset({
    "2g", "3g", "4g", "5g", "6g", "4g+", "5g+",
    "lte", "lte-a", "volte", "hspa", "edge", "gprs",
    "wifi", "wi-fi", "nfc", "bluetooth",
})

_NOISE_PATTERNS = (
    re.compile(r'^\+?\d[\d\s\-()+]{7,}$'),                    # phone numbers
    re.compile(r'^\d{1,2}:\d{2}\s*[-~–]\s*\d{1,2}:\d{2}'),   # business hours
    re.compile(r'^\d+(\.\d+)?\s*(GB|TB|MB)\b', re.I),          # data quotas
    re.compile(r'^\d+(\.\d+)?\s*(Mbps|Gbps|Kbps)\b', re.I),   # network speeds
)

def _is_noise(text: str, label: str = "") -> bool:
    t = text.strip()
    if t.lower() in _TECH_STANDARDS:
        return True
    if label == "LOCATION" and t.lower() in _LOCATION_BLOCKLIST:
        return True
    return any(p.match(t) for p in _NOISE_PATTERNS)


def _truncate_entity(text):
    text = text.strip("【】")
    chinese_chars = sum(1 for c in text if '一' <= c <= '鿿')
    if chinese_chars > len(text) / 2:
        return text[:12]
    words = text.split()
    return " ".join(words[:4])


def _is_valid_entity(text):
    stripped = text.strip()
    if len(stripped) <= 1:
        return False
    if stripped.isdigit():
        return False
    if all(
        unicodedata.category(c) in ('Po', 'Ps', 'Pe', 'Pi', 'Pf', 'Pd', 'Pc', 'So', 'Sm', 'Sk', 'Sc', 'Zs')
        or not c.isalnum()
        for c in stripped
    ):
        return False
    return True


def _parse_llm_json(raw):
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw)


def _format_label_defs(label_defs: dict[str, str]) -> str:
    return "\n".join(f"- {k}: {v}" for k, v in label_defs.items())


_CORPORATE_SUFFIXES = (
    "股份有限公司", "有限公司", "股份有限", "公司",
    " Co., Ltd.", " Co.,Ltd.", " Co.,", " Co.", " Ltd.", " Inc.", " Corp.",
    " Corporation", " Company",
    " Telecommunications", " Telecom", " Technologies", " Technology",
    " Holdings", " Group", " International",
)

# Traditional Chinese equivalents for common simplified variants
_ZH_NORMALIZE: dict[str, str] = {
    "台湾": "台灣",
    "电信": "電信",
    "联通": "聯通",
    "移动": "移動",
}

_LOCATION_BLOCKLIST = frozenset({
    "ptt", "dcard", "facebook", "instagram", "line", "youtube",
    "google", "reddit", "twitter", "x", "tiktok", "plurk",
})


def _strip_corporate_suffix(text: str) -> str:
    # Loop until stable so multi-suffix names strip fully in one call
    # e.g. "Far EasTone Telecommunications Co.," → "Far EasTone" in two passes
    prev = None
    while text != prev:
        prev = text
        for suffix in _CORPORATE_SUFFIXES:
            if text.endswith(suffix):
                text = text[:-len(suffix)].strip()
                break
    return text


def _build_cluster_counter(names: list[str], label: str) -> Counter:
    # Normalize simplified Chinese variants → traditional
    names = [_ZH_NORMALIZE.get(n, n) for n in names]

    # Strip corporate suffixes so "中華電信股份有限" → "中華電信"
    if label in ("BRAND", "INSTITUTION"):
        names = [_strip_corporate_suffix(n) for n in names]

    raw = Counter(names)

    # Substring deduplication: merge shorter/less-frequent variant into the
    # more-frequent/longer canonical form.
    # e.g. "台哥大" (count=2) absorbed into "台灣大哥大" (count=2, longer)
    keys = sorted(raw.keys(), key=lambda x: (-raw[x], -len(x)))
    absorbed: set[str] = set()

    for i, canonical in enumerate(keys):
        if canonical in absorbed:
            continue
        for variant in keys[i + 1:]:
            if variant in absorbed:
                continue
            if variant in canonical or canonical in variant:
                raw[canonical] += raw[variant]
                del raw[variant]
                absorbed.add(variant)

    return raw


# ---------------------------------------------------------------------------
# Step 0 — Domain detection
# ---------------------------------------------------------------------------

def detect_domain(keyword: str) -> str:
    domain_options = "\n".join(
        f'- "{k}": {v["description"]}'
        for k, v in DOMAIN_SCHEMAS.items()
    )
    for client in groq_clients:
        try:
            response = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{
                    "role": "user",
                    "content": (
                        "Classify this SEO keyword into exactly one domain. "
                        "Return ONLY the domain key as a plain string with no quotes or explanation.\n\n"
                        f'Keyword: "{keyword}"\n\n'
                        f"Available domains:\n{domain_options}"
                    ),
                }],
                temperature=0,
            )
            detected = response.choices[0].message.content.strip().strip('"').lower()
            return detected if detected in DOMAIN_SCHEMAS else "general"
        except Exception:
            continue
    return "general"


# ---------------------------------------------------------------------------
# Step 1 — Entity extraction (per article, domain-aware labels)
# ---------------------------------------------------------------------------

def extract_entities(text: str, label_defs: dict[str, str], start_idx: int = 0) -> list[tuple[str, str]]:
    valid_labels = set(label_defs.keys())
    n = len(groq_clients)
    for i in range(n):
        client = groq_clients[(start_idx + i) % n]
        try:
            response = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[{
                    "role": "user",
                    "content": (
                        "Extract named entities for SEO content analysis. "
                        'Return ONLY a JSON array: [{"text": "entity name", "label": "ENTITY_TYPE"}]\n\n'
                        "Use ONLY these labels:\n"
                        f"{_format_label_defs(label_defs)}\n\n"
                        f"Text: {text[:10000]}"
                    ),
                }],
                temperature=0,
            )
            parsed = _parse_llm_json(response.choices[0].message.content)
            return [
                (_truncate_entity(item["text"]), item["label"])
                for item in parsed
                if isinstance(item, dict)
                and _is_valid_entity(item.get("text", ""))
                and item.get("label") in valid_labels
                and not _is_noise(item.get("text", ""), item.get("label", ""))
            ]
        except Exception:
            continue
    return []



# ---------------------------------------------------------------------------
# Scraper
# ---------------------------------------------------------------------------

def scrape_article(url: str) -> str:
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        res = requests.get(url, headers=headers, timeout=5)
        soup = BeautifulSoup(res.text, "html.parser")
        paragraphs = soup.find_all("p")
        return " ".join(p.get_text() for p in paragraphs)
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/analyze")
def analyze(req: SearchRequest):
    # Step 0: Detect domain → pick the right entity schema
    domain = detect_domain(req.keyword)
    label_defs = DOMAIN_SCHEMAS[domain]["labels"]

    # Step 1: Fetch top 10 Google results via ValueSERP
    serp_res = requests.get(
        "https://api.valueserp.com/search",
        params={
            "api_key": os.getenv("VALUESERP_API_KEY"),
            "q": req.keyword,
            "num": 10,
            "output": "json",
        },
    ).json()
    organic = serp_res.get("organic_results", [])

    def _process(args):
        rank, item = args
        url = item.get("link", "")
        title = item.get("title", "")
        # Each article starts on a different key; falls back to others if rate limited
        entities = extract_entities(scrape_article(url), label_defs, start_idx=rank)
        return {"rank": rank + 1, "title": title, "url": url, "entities": entities}

    # Step 2: Scrape and extract all 10 articles in parallel across 4 API keys
    with ThreadPoolExecutor(max_workers=10) as executor:
        raw_results = list(executor.map(_process, enumerate(organic[:10])))

    all_entities: list[tuple[str, str]] = [
        e for r in raw_results for e in r["entities"]
    ]

    # Step 4: Rebuild per-article results
    results = []
    for raw in raw_results:
        entity_counts = Counter(t for t, _ in raw["entities"])
        results.append({
            "rank": raw["rank"],
            "title": raw["title"],
            "url": raw["url"],
            "entity_count": len(raw["entities"]),
            "top_entities": dict(entity_counts.most_common(5)),
        })

    # Step 5: Cluster by entity type
    type_clusters: dict[str, list] = {}
    for text, label in all_entities:
        type_clusters.setdefault(label, []).append(text)

    cluster_summary = {
        label: dict(_build_cluster_counter(items, label).most_common(5))
        for label, items in type_clusters.items()
    }

    # Step 6: Persist to Supabase
    supabase.table("searches").insert({
        "keyword": req.keyword,
        "result_count": len(results),
        "clusters": cluster_summary,
    }).execute()

    return {
        "keyword": req.keyword,
        "domain": domain,
        "results": results,
        "clusters": cluster_summary,
    }


@app.get("/history")
def history():
    res = supabase.table("searches").select("*").order("created_at", desc=True).limit(10).execute()
    return res.data
