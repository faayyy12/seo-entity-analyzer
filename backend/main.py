from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests
from bs4 import BeautifulSoup
from groq import Groq
from collections import Counter
import json
from supabase import create_client
from dotenv import load_dotenv
import os
import unicodedata

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))

supabase = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_SECRET_KEY")
)

class SearchRequest(BaseModel):
    keyword: str

def _truncate_entity(text):
    # Strip Chinese bracket characters
    text = text.strip("【】")
    # Determine if mostly Chinese (Unicode CJK block)
    chinese_chars = sum(1 for c in text if '一' <= c <= '鿿')
    if chinese_chars > len(text) / 2:
        return text[:8]
    # English/numbers: keep up to 3 words
    words = text.split()
    return " ".join(words[:3])

def _is_valid_entity(text):
    stripped = text.strip()
    if len(stripped) <= 1:
        return False
    if stripped.isdigit():
        return False
    if all(unicodedata.category(c) in ('Po', 'Ps', 'Pe', 'Pi', 'Pf', 'Pd', 'Pc', 'So', 'Sm', 'Sk', 'Sc', 'Zs') or not c.isalnum() for c in stripped):
        return False
    return True

def extract_entities(text):
    try:
        response = groq_client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[{
                "role": "user",
                "content": (
                    "Extract all named entities from this text. Return ONLY a JSON array in this format: "
                    '[{"text": "entity name", "label": "ENTITY_TYPE"}]. '
                    "Use these labels: PERSON, ORG, GPE, DATE, MONEY, PRODUCT, CARDINAL. "
                    f"Text: {text[:10000]}"
                )
            }],
            temperature=0,
        )
        raw = response.choices[0].message.content.strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        parsed = json.loads(raw)
        return [
            (_truncate_entity(item["text"]), item["label"])
            for item in parsed
            if isinstance(item, dict) and _is_valid_entity(item.get("text", ""))
        ]
    except Exception:
        return []

def scrape_article(url):
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        res = requests.get(url, headers=headers, timeout=5)
        soup = BeautifulSoup(res.text, "html.parser")
        paragraphs = soup.find_all("p")
        text = " ".join([p.get_text() for p in paragraphs])
        return text
    except:
        return ""

@app.post("/analyze")
def analyze(req: SearchRequest):
    # Step 1: Call ValueSERP
    serp_url = "https://api.valueserp.com/search"
    params = {
        "api_key": os.getenv("VALUESERP_API_KEY"),
        "q": req.keyword,
        "num": 10,
        "output": "json"
    }
    serp_res = requests.get(serp_url, params=params).json()
    organic = serp_res.get("organic_results", [])

    results = []
    all_entities = []

    # Step 2: Scrape each article and extract entities
    for i, item in enumerate(organic[:10]):
        url = item.get("link", "")
        title = item.get("title", "")
        text = scrape_article(url)
        entities = extract_entities(text)
        entity_counts = Counter([e[0] for e in entities])
        all_entities.extend(entities)

        results.append({
            "rank": i + 1,
            "title": title,
            "url": url,
            "entity_count": len(entities),
            "top_entities": dict(entity_counts.most_common(5))
        })

    # Step 3: Cluster by entity type
    type_clusters = {}
    for text, label in all_entities:
        if label not in type_clusters:
            type_clusters[label] = []
        type_clusters[label].append(text)

    cluster_summary = {
        label: dict(Counter(items).most_common(5))
        for label, items in type_clusters.items()
    }

    # Step 4: Save to Supabase
    supabase.table("searches").insert({
        "keyword": req.keyword,
        "result_count": len(results),
        "clusters": cluster_summary
    }).execute()

    return {
        "keyword": req.keyword,
        "results": results,
        "clusters": cluster_summary
    }

@app.get("/history")
def history():
    res = supabase.table("searches").select("*").order("created_at", desc=True).limit(10).execute()
    return res.data