#!/usr/bin/env python3
"""
PharmaDeal Intelligence — 自动数据更新脚本
每天北京时间 07:00 由 GitHub Actions 自动运行
从多个公开来源抓取最新医药融资/BD交易信息
"""

import json
import re
import os
import sys
import hashlib
from datetime import datetime, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# === CONFIG ===
DATA_FILE = Path(__file__).parent / "data.json"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}
TIMEOUT = 30

# === HELPERS ===

def load_existing():
    """Load existing deals from data.json."""
    if DATA_FILE.exists():
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_deals(deals):
    """Save deals to data.json, sorted by date descending."""
    deals.sort(key=lambda d: d["date"], reverse=True)
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(deals, f, ensure_ascii=False, indent=2)
    print(f"[OK] Saved {len(deals)} deals to {DATA_FILE}")


def deal_id(d):
    """Generate a unique ID for deduplication."""
    raw = f"{d['date']}|{d['company']}|{d['event'][:30]}"
    return hashlib.md5(raw.encode()).hexdigest()


def deduplicate(existing, new_deals):
    """Merge new deals into existing, avoiding duplicates."""
    seen = {deal_id(d) for d in existing}
    added = 0
    for d in new_deals:
        clean_deal(d)
        did = deal_id(d)
        if did not in seen:
            existing.append(d)
            seen.add(did)
            added += 1
            print(f"  [NEW] {d['date']} | {d['type']} | {d['company']} | {d['event'][:40]}")
    return existing, added


def clean_deal(d):
    """Clean and normalize a deal record."""
    # Remove source suffixes from event titles
    event = d.get("event", "")
    event = re.sub(r"\s*[-–—]\s*(?:ByDrug|新浪财经|东方财富|雪球|财联社|财新|投资界|药智新闻|凤凰网|维科号|21财经|证券时报|中[^\s]{0,10})$", "", event)
    event = re.sub(r"\s*[-–—]\s*[A-Za-z][A-Za-z\s.]{0,30}$", "", event)
    d["event"] = event.strip()[:80]

    # Clean company name
    company = d.get("company", "")
    # Remove prefixes like "月", "年", numbers-only prefixes
    company = re.sub(r"^[\d年月]+", "", company)
    # Remove "快讯 | " type prefixes
    company = re.sub(r"^快讯\s*[|｜]\s*", "", company)
    if len(company) < 2 or company == d["event"][:len(company)]:
        # Try re-extracting from event
        cm = re.search(r"([\u4e00-\u9fff]{2,8}(?:医药|生物|制药|药业|药|生命|健华|神州|博泰|生科))", d["event"])
        if cm:
            company = cm.group(1)
    d["company"] = company.strip()[:20]

    # Ensure amount is numeric
    if not isinstance(d.get("amount"), (int, float)):
        d["amount"] = 0

    # Ensure all required fields exist
    for field in ["date", "type", "company", "partner", "event", "area", "amount", "stage", "region", "geography", "source", "sourceUrl"]:
        if field not in d:
            d[field] = "—" if field != "amount" else 0


def safe_get(url, **kwargs):
    """Safe HTTP GET with error handling."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT, **kwargs)
        resp.raise_for_status()
        return resp
    except Exception as e:
        print(f"  [WARN] Failed to fetch {url}: {e}")
        return None


def parse_amount(text):
    """Extract deal amount in 亿美元 from text."""
    text = text.replace(",", "").replace("，", "")
    # Match patterns like $1.5B, 15亿美元, 1.53 billion, etc.
    patterns = [
        (r"(\d+\.?\d*)\s*(?:billion|B)\s*(?:USD|\$|美元)?", lambda m: float(m.group(1)) * 10),
        (r"\$\s*(\d+\.?\d*)\s*(?:billion|B)", lambda m: float(m.group(1)) * 10),
        (r"(\d+\.?\d*)\s*亿\s*美元", lambda m: float(m.group(1))),
        (r"(\d+\.?\d*)\s*亿\s*(?:元|人民币)", lambda m: float(m.group(1)) / 7.2),
        (r"(\d+\.?\d*)\s*(?:million|M)\s*(?:USD|\$|美元)?", lambda m: float(m.group(1)) / 100),
        (r"\$\s*(\d+\.?\d*)\s*(?:million|M)", lambda m: float(m.group(1)) / 100),
    ]
    for pat, conv in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            return round(conv(m), 2)
    return 0


def classify_deal(text):
    """Classify deal type from text."""
    text_lower = text.lower()
    if any(kw in text for kw in ["并购", "收购", "acquisition", "acquire", "buyout"]):
        return "并购"
    if any(kw in text for kw in ["IPO", "上市", "ipo", "public offering"]):
        return "IPO"
    if any(kw in text for kw in ["许可", "授权", "license", "licensing", "合作", "collaboration", "partnership", "BD"]):
        return "BD/许可"
    if any(kw in text for kw in ["融资", "轮", "funding", "raise", "financing", "series", "round"]):
        return "融资"
    return "BD/许可"


def classify_area(text):
    """Classify therapeutic area from text."""
    area_map = {
        "肿瘤": ["肿瘤", "癌", "cancer", "oncolog", "ADC", "CAR-T", "PD-1", "PD-L1"],
        "免疫": ["免疫", "immun", "autoimmun", "炎症", "inflam"],
        "代谢": ["代谢", "GLP-1", "糖尿病", "diabet", "metabol", "肥胖", "obes"],
        "神经科学": ["神经", "neuro", "CNS", "精神", "阿尔茨海默", "alzheimer"],
        "罕见病": ["罕见", "rare", "orphan"],
        "抗感染": ["感染", "infect", "抗病毒", "antivir", "疫苗", "vaccin"],
        "心血管": ["心血管", "cardiov", "心脏", "cardiac"],
        "眼科": ["眼", "ophthalm", "视网膜", "retina"],
    }
    for area, keywords in area_map.items():
        if any(kw.lower() in text.lower() for kw in keywords):
            return area
    return "肿瘤"


# =========================================================
# SOURCE 1: 同花顺医药新闻
# =========================================================
def scrape_10jqka():
    """Scrape pharma news from 同花顺."""
    print("\n[SOURCE] 同花顺财经...")
    deals = []
    urls = [
        "https://news.10jqka.com.cn/cjzx_list/",
        "https://news.10jqka.com.cn/realtimenews.html",
    ]
    for url in urls:
        resp = safe_get(url)
        if not resp:
            continue
        resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "lxml")
        articles = soup.select("ul.list-con li, .news_list li, .listContent li")
        for art in articles[:20]:
            link_el = art.select_one("a")
            if not link_el:
                continue
            title = link_el.get_text(strip=True)
            href = link_el.get("href", "")
            # Filter for pharma deal keywords
            pharma_kw = ["医药", "药", "生物", "融资", "并购", "授权", "许可", "BD",
                         "license", "合作", "IPO", "CAR-T", "ADC", "抗体"]
            if not any(kw in title for kw in pharma_kw):
                continue
            # Try to get date
            date_el = art.select_one(".arc_time, .time, time, span.rq")
            date_str = ""
            if date_el:
                date_text = date_el.get_text(strip=True)
                m = re.search(r"(\d{4})[-./ ](\d{1,2})[-./ ](\d{1,2})", date_text)
                if m:
                    date_str = f"{m.group(1)}-{m.group(2).zfill(2)}-{m.group(3).zfill(2)}"
            if not date_str:
                date_str = datetime.now().strftime("%Y-%m-%d")

            # Extract company name from title
            company = ""
            company_patterns = [
                r"([\u4e00-\u9fff]{2,6}(?:医药|生物|制药|药业|健华|神州|生科))",
                r"([A-Z][a-zA-Z]+\s+(?:Therapeutics|Pharma|Bio|Sciences?))",
            ]
            for pat in company_patterns:
                cm = re.search(pat, title)
                if cm:
                    company = cm.group(1)
                    break
            if not company:
                company = title[:10]

            deals.append({
                "date": date_str,
                "type": classify_deal(title),
                "company": company,
                "partner": "—",
                "event": title[:80],
                "area": classify_area(title),
                "amount": parse_amount(title),
                "stage": "—",
                "region": "中国",
                "geography": "中国",
                "source": "同花顺财经",
                "sourceUrl": href if href.startswith("http") else f"https://news.10jqka.com.cn{href}",
            })
    print(f"  Found {len(deals)} potential deals from 同花顺")
    return deals


# =========================================================
# SOURCE 2: 医药魔方 PharmCube
# =========================================================
def scrape_pharmcube():
    """Scrape pharma news from 医药魔方."""
    print("\n[SOURCE] 医药魔方 PharmCube...")
    deals = []
    url = "https://bydrug.pharmcube.com/news/newsList"
    resp = safe_get(url)
    if not resp:
        # Try alternative URL
        resp = safe_get("https://bydrug.pharmcube.com/")
    if not resp:
        return deals

    resp.encoding = "utf-8"
    soup = BeautifulSoup(resp.text, "lxml")
    articles = soup.select(".news-item, .article-item, .news_list li, a[href*='news']")

    for art in articles[:20]:
        if art.name == "a":
            title = art.get_text(strip=True)
            href = art.get("href", "")
        else:
            link_el = art.select_one("a")
            if not link_el:
                continue
            title = link_el.get_text(strip=True)
            href = link_el.get("href", "")

        pharma_kw = ["BD", "融资", "并购", "授权", "许可", "license", "合作", "IPO",
                     "收购", "交易", "deal"]
        if not any(kw in title for kw in pharma_kw):
            continue

        date_str = datetime.now().strftime("%Y-%m-%d")
        company = ""
        company_patterns = [
            r"([\u4e00-\u9fff]{2,6}(?:医药|生物|制药|药业|健华|神州|博泰|生科))",
            r"([A-Z][a-zA-Z]+\s+(?:Therapeutics|Pharma|Bio|Sciences?))",
        ]
        for pat in company_patterns:
            cm = re.search(pat, title)
            if cm:
                company = cm.group(1)
                break
        if not company:
            company = title[:10]

        full_url = href
        if href and not href.startswith("http"):
            full_url = f"https://bydrug.pharmcube.com{href}"

        deals.append({
            "date": date_str,
            "type": classify_deal(title),
            "company": company,
            "partner": "—",
            "event": title[:80],
            "area": classify_area(title),
            "amount": parse_amount(title),
            "stage": "—",
            "region": "中国",
            "geography": "中国",
            "source": "医药魔方 PharmCube",
            "sourceUrl": full_url,
        })

    print(f"  Found {len(deals)} potential deals from 医药魔方")
    return deals


# =========================================================
# SOURCE 3: 上交所公告 (SSE Disclosures)
# =========================================================
def scrape_sse():
    """Scrape pharma-related disclosures from SSE."""
    print("\n[SOURCE] 上海证券交易所...")
    deals = []
    # SSE disclosure API
    url = "https://query.sse.com.cn/security/stock/queryCompanyBulletin.do"
    params = {
        "jsonCallBack": "callback",
        "isPagination": "true",
        "pageHelp.pageSize": "25",
        "pageHelp.pageNo": "1",
        "pageHelp.beginPage": "1",
        "pageHelp.endPage": "1",
        "security_Code": "",
        "categoryId": "",
        "keyword": "医药 合作",
        "_": str(int(datetime.now().timestamp() * 1000)),
    }
    sse_headers = {**HEADERS, "Referer": "https://www.sse.com.cn/"}
    try:
        resp = requests.get(url, params=params, headers=sse_headers, timeout=TIMEOUT)
        resp.raise_for_status()
    except Exception as e:
        print(f"  [WARN] SSE fetch failed: {e}")
        return deals

    try:
        text = resp.text
        # Remove JSONP wrapper
        json_str = re.search(r"callback\((.*)\)", text)
        if json_str:
            data = json.loads(json_str.group(1))
            results = data.get("result", [])
            for item in results[:15]:
                title = item.get("title", "")
                pharma_kw = ["药", "医", "生物", "融资", "授权", "合作", "许可"]
                if not any(kw in title for kw in pharma_kw):
                    continue
                date_str = item.get("SSEDate", datetime.now().strftime("%Y-%m-%d"))
                company = item.get("security_Name", title[:10])

                deals.append({
                    "date": date_str[:10],
                    "type": classify_deal(title),
                    "company": company,
                    "partner": "—",
                    "event": title[:80],
                    "area": classify_area(title),
                    "amount": parse_amount(title),
                    "stage": "—",
                    "region": "中国",
                    "geography": "中国",
                    "source": "上海证券交易所",
                    "sourceUrl": "https://www.sse.com.cn/",
                })
    except Exception as e:
        print(f"  [WARN] SSE parse error: {e}")

    print(f"  Found {len(deals)} potential deals from SSE")
    return deals


# =========================================================
# SOURCE 4: 披露易 HKEXnews
# =========================================================
def scrape_hkex():
    """Scrape pharma-related disclosures from HKEX."""
    print("\n[SOURCE] 披露易 HKEXnews...")
    deals = []
    today = datetime.now()
    date_from = (today - timedelta(days=3)).strftime("%Y%m%d")
    date_to = today.strftime("%Y%m%d")

    url = "https://www1.hkexnews.hk/search/titlesearch.xhtml"
    params = {
        "lang": "ZH",
        "category": "0",
        "market": "SEHK",
        "searchType": "0",
        "t1code": "40000",  # Healthcare
        "t2Gcode": "-2",
        "stockId": "-1",
        "from": date_from,
        "to": date_to,
        "title": "",
        "sortDir": "desc",
        "sortByDate": "on",
    }
    resp = safe_get(url, params=params)
    if not resp:
        return deals

    resp.encoding = "utf-8"
    soup = BeautifulSoup(resp.text, "lxml")
    rows = soup.select("tr.row0, tr.row1, .table-row, tbody tr")

    for row in rows[:20]:
        cells = row.select("td")
        if len(cells) < 3:
            continue
        date_text = cells[0].get_text(strip=True)
        company = cells[1].get_text(strip=True)
        title = cells[-1].get_text(strip=True) if len(cells) > 2 else ""

        pharma_kw = ["药", "医", "生物", "license", "合作", "授权", "BD",
                     "收购", "融资", "配售"]
        if not any(kw in (title + company) for kw in pharma_kw):
            continue

        m = re.search(r"(\d{4})[/\-](\d{1,2})[/\-](\d{1,2})", date_text)
        date_str = f"{m.group(1)}-{m.group(2).zfill(2)}-{m.group(3).zfill(2)}" if m else today.strftime("%Y-%m-%d")

        link_el = row.select_one("a[href]")
        href = link_el.get("href", "") if link_el else ""
        if href and not href.startswith("http"):
            href = f"https://www1.hkexnews.hk{href}"

        deals.append({
            "date": date_str,
            "type": classify_deal(title + company),
            "company": company[:20],
            "partner": "—",
            "event": title[:80] if title else f"{company}公告",
            "area": classify_area(title + company),
            "amount": parse_amount(title),
            "stage": "—",
            "region": "中国",
            "geography": "中国、香港",
            "source": "披露易 HKEXnews",
            "sourceUrl": href or "https://www.hkexnews.hk/index_c.htm",
        })

    print(f"  Found {len(deals)} potential deals from HKEX")
    return deals


# =========================================================
# SOURCE 5: 丁香通 BioMart
# =========================================================
def scrape_biomart():
    """Scrape news from 丁香通."""
    print("\n[SOURCE] 丁香通 BioMart...")
    deals = []
    urls = [
        "https://www.biomart.cn/infosupply/newslistbytype.htm?type=1",
        "https://www.biomart.cn/news/allnews.htm",
    ]
    for url in urls:
        resp = safe_get(url)
        if not resp:
            continue
        resp.encoding = "utf-8"
        soup = BeautifulSoup(resp.text, "lxml")
        articles = soup.select(".news-list li, .newsListContent li, .article_list li, a[href*='news']")

        for art in articles[:15]:
            if art.name == "a":
                title = art.get_text(strip=True)
                href = art.get("href", "")
            else:
                link_el = art.select_one("a")
                if not link_el:
                    continue
                title = link_el.get_text(strip=True)
                href = link_el.get("href", "")

            pharma_kw = ["融资", "并购", "授权", "许可", "合作", "收购", "BD", "IPO",
                         "交易", "license"]
            if not any(kw in title for kw in pharma_kw):
                continue

            date_str = datetime.now().strftime("%Y-%m-%d")
            full_url = href
            if href and not href.startswith("http"):
                full_url = f"https://www.biomart.cn{href}"

            company = ""
            cm = re.search(r"([\u4e00-\u9fff]{2,8}(?:医药|生物|制药|药业|健华|神州|生科))", title)
            if cm:
                company = cm.group(1)
            else:
                company = title[:10]

            deals.append({
                "date": date_str,
                "type": classify_deal(title),
                "company": company,
                "partner": "—",
                "event": title[:80],
                "area": classify_area(title),
                "amount": parse_amount(title),
                "stage": "—",
                "region": "中国",
                "geography": "中国",
                "source": "丁香通 BioMart",
                "sourceUrl": full_url,
            })

    print(f"  Found {len(deals)} potential deals from 丁香通")
    return deals


# =========================================================
# SOURCE 6: Google News RSS 聚合 (免费，无需API)
# =========================================================
def scrape_search_aggregation():
    """Search for pharma deals via Google News RSS feeds."""
    print("\n[SOURCE] 公开搜索聚合 (Google News RSS)...")
    deals = []
    queries = [
        "医药+融资+2026",
        "biotech+licensing+deal+2026",
        "医药+BD+授权+交易",
        "pharma+acquisition+2026",
        "生物医药+IPO",
    ]
    for query in queries:
        url = f"https://news.google.com/rss/search?q={query}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
        resp = safe_get(url)
        if not resp:
            continue

        try:
            soup = BeautifulSoup(resp.content, "lxml-xml")
            items = soup.find_all("item")
        except Exception:
            soup = BeautifulSoup(resp.text, "lxml")
            items = soup.find_all("item")

        for item in items[:10]:
            title = item.find("title")
            link = item.find("link")
            pub_date = item.find("pubDate")
            source_el = item.find("source")

            if not title:
                continue
            title_text = title.get_text(strip=True)
            href = link.get_text(strip=True) if link else ""
            src_name = source_el.get_text(strip=True) if source_el else "公开新闻"

            # Filter for pharma deal keywords
            pharma_kw = ["医药", "药", "生物", "biotech", "pharma"]
            deal_kw = ["融资", "并购", "授权", "许可", "license", "IPO", "BD",
                       "deal", "收购", "acquisition", "合作", "collaboration",
                       "series", "round", "raise", "funding", "交易"]
            if not (any(kw.lower() in title_text.lower() for kw in pharma_kw) and
                    any(kw.lower() in title_text.lower() for kw in deal_kw)):
                continue

            # Parse date
            date_str = datetime.now().strftime("%Y-%m-%d")
            if pub_date:
                date_text = pub_date.get_text(strip=True)
                try:
                    from email.utils import parsedate_to_datetime
                    dt = parsedate_to_datetime(date_text)
                    date_str = dt.strftime("%Y-%m-%d")
                except Exception:
                    dm = re.search(r"(\d{1,2})\s+(\w+)\s+(\d{4})", date_text)
                    if dm:
                        months = {"Jan":"01","Feb":"02","Mar":"03","Apr":"04","May":"05","Jun":"06",
                                  "Jul":"07","Aug":"08","Sep":"09","Oct":"10","Nov":"11","Dec":"12"}
                        mon = months.get(dm.group(2)[:3], "01")
                        date_str = f"{dm.group(3)}-{mon}-{dm.group(1).zfill(2)}"

            # Extract company
            company = ""
            for pat in [
                r"([\u4e00-\u9fff]{2,8}(?:医药|生物|制药|药业|健华|神州|博泰|生科|药明|百济|恒瑞|信达|科伦|生命))",
                r"([A-Z][a-zA-Z]+\s+(?:Therapeutics|Pharma|Bio|Sciences?|Oncology))",
            ]:
                cm = re.search(pat, title_text)
                if cm:
                    company = cm.group(1)
                    break
            if not company:
                company = title_text[:12]

            deals.append({
                "date": date_str,
                "type": classify_deal(title_text),
                "company": company,
                "partner": "—",
                "event": title_text[:80],
                "area": classify_area(title_text),
                "amount": parse_amount(title_text),
                "stage": "—",
                "region": "全球",
                "geography": "全球",
                "source": src_name,
                "sourceUrl": href,
            })

    print(f"  Found {len(deals)} potential deals from RSS search")
    return deals


# =========================================================
# MAIN
# =========================================================
def main():
    print("=" * 50)
    print("PharmaDeal Intelligence — Data Update")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 50)

    existing = load_existing()
    print(f"Existing deals: {len(existing)}")

    # Collect from all sources
    all_new = []
    sources = [
        scrape_10jqka,
        scrape_pharmcube,
        scrape_sse,
        scrape_hkex,
        scrape_biomart,
        scrape_search_aggregation,
    ]

    for src_fn in sources:
        try:
            new_deals = src_fn()
            all_new.extend(new_deals)
        except Exception as e:
            print(f"  [ERROR] {src_fn.__name__} failed: {e}")

    print(f"\nTotal new candidates: {len(all_new)}")

    # Deduplicate and merge
    merged, added = deduplicate(existing, all_new)
    print(f"New deals added: {added}")

    # Save
    save_deals(merged)
    print(f"\nDone! Total deals: {len(merged)}")

    # Set output for GitHub Actions
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a") as f:
            f.write(f"deals_added={added}\n")
            f.write(f"total_deals={len(merged)}\n")


if __name__ == "__main__":
    main()
