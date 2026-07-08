#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import asyncio, re, json, time
from datetime import datetime, date, timedelta
from pathlib import Path
import requests
from bs4 import BeautifulSoup
import pandas as pd
from dateutil import parser as dateparser

try:
    from playwright.async_api import async_playwright
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False

OUT_DIR = Path("out"); OUT_DIR.mkdir(exist_ok=True)
NOW = datetime.now()
CUTOFF = NOW - timedelta(days=90)
TODAY = date.today()
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
H = {"User-Agent": UA, "Accept": "text/html,*/*", "Accept-Language": "en-US,en;q=0.9,ru;q=0.8"}

CAT_RU = {'Software / IT Development': 'IT-разработка', 'IT Equipment Supply': 'Поставка IT-оборудования', 'Telecom / Network': 'Телеком/Сеть', 'Geo-spatial / Digital Agriculture': 'Геоданные/Цифровое с/х', 'Fintech / Digital Payments': 'Финтех/Цифровые платежи', 'E-Government / E-Procurement': 'Электронное правительство', 'Lab Equipment': 'Лабораторное оборудование', 'Power / Electrical': 'Электрооборудование', 'Machinery / Vehicles': 'Техника/Спецтранспорт', 'Vehicles': 'Транспорт', 'Furniture': 'Мебель', 'Consulting': 'Консалтинг', 'Training / TA': 'Обучение/Техпомощь', 'Studies / Audit': 'Исследования/Аудит', 'Construction / Civil': 'Строительство/СМР', 'Infrastructure / Roads': 'Инфраструктура/Дороги', 'Healthcare / Medical': 'Здравоохранение', 'Other Services': 'Прочие услуги'}
DONOR_RU = {'World Bank (IDA/IBRD)': 'Всемирный банк', 'World Bank (SRASP)': 'Всемирный банк (SRASP)', 'Агрегатор госзакупок РТ': 'Госзакупки РТ', 'Госкоминвест РТ': 'Госкоминвест РТ', 'UNDP': 'ПРООН (ООН)'}
SOURCE_RU = {'World Bank': 'World Bank API', 'aedpmu.tj': 'AED PMU', 'tenders.tj': 'tenders.tj', 'UNDP': 'UNDP', 'investcom.tj': 'Госкоминвест РТ'}
METHOD_RU = {'Request for Bids': 'RFB', 'Request for Quotations': 'RFQ', 'Request for Proposals': 'RFP', 'Expression of Interest': 'EOI', 'Individual Consultant Selection': 'IC', 'Тендер': 'Тендер', 'Запрос ценовых котировок': 'ЗЦК', 'Прямая закупка': 'ПЗ', 'Аукцион': 'Аукцион'}
WB_PROJECT_RU = {'Tajikistan Digital Foundations Project': 'Цифровые основы Таджикистана', 'Public Finance Management Modernization Project 2': 'Модернизация госфинансов', 'Social Protection Modernization and Economic Inclusion Project': 'Модернизация соцзащиты', 'Strengthening Resilience of the Agriculture Sector Project': 'Укрепление с/х (SRASP)', 'Tajikistan Water Supply and Sanitation Investment Project': 'Водоснабжение и канализация', 'Tajikistan Millati Solim Project': 'Здоровая нация', 'Tajikistan Strengthening Water and Irrigation Management Project': 'Управление водой', 'Tajikistan Preparedness and Resilience to Disasters Project': 'Готовность к ЧС', 'Early Childhood Development': 'Раннее развитие детей', 'Modernizing the National Statistical System in Tajikistan': 'Модернизация статистики', 'Rural Electrification Project': 'Электрификация сёл', 'Rural Water Supply and Sanitation Project': 'Сельское водоснабжение', 'Financial and Private Sector Development Project': 'Развитие финансового сектора'}

def parse_d(s):
    if not s: return ""
    for f in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d-%b-%Y", "%d.%m.%Y"]:
        try: return datetime.strptime(str(s).strip(), f).strftime("%Y-%m-%d")
        except: pass
    try: return dateparser.parse(str(s)).strftime("%Y-%m-%d")
    except: return str(s)

def in_window(s):
    if not s: return True
    try: return datetime.strptime(parse_d(s), "%Y-%m-%d") >= CUTOFF
    except: return True

def title_ru(row):
    for k, v in WB_PROJECT_RU.items():
        if pd.notna(row.get('title_en', '')) and k in str(row.get('title_en', '')): return v
    for col in ['title_tj', 'title_ru', 'title_en', 'title_original']:
        v = row.get(col, '')
        if pd.notna(v) and str(v).strip() and str(v).strip() != 'nan': return str(v).strip()
    return '(без названия)'

def normalize(raw):
    base = {k: raw.get(k, "") for k in ["source", "tender_id", "title_en", "title_ru", "title_tj", "title_original", "donor", "funding_type", "country", "region", "organization", "category", "publication_date", "submission_deadline", "procurement_method", "eligibility", "description", "documents_url", "contact_name", "contact_email", "contact_phone", "source_url", "language"]}
    base["scraped_at"] = NOW.isoformat(timespec="seconds")
    return base

def fetch_worldbank():
    print("[1/6] World Bank..."); out = []; seen = set(); off = 0
    for _ in range(50):
        try:
            r = requests.get("https://search.worldbank.org/api/v2/procnotices", params={"format": "json", "qterm": "Tajikistan", "rows": 100, "os": off}, headers=H, timeout=30)
            r.raise_for_status()
            for n in r.json().get("procnotices", []):
                if n.get("project_ctry_name") != "Tajikistan": continue
                nid = n.get("id", "")
                if nid in seen: continue
                seen.add(nid)
                if not in_window(parse_d(n.get("noticedate", ""))): continue
                out.append(normalize({"source": "World Bank", "tender_id": nid, "title_en": n.get("project_name", ""), "title_original": n.get("project_name", ""), "donor": "World Bank (IDA/IBRD)", "funding_type": "Loan/Credit/Grant", "country": "Tajikistan", "organization": n.get("contact_organization", ""), "category": n.get("procurement_group", ""), "submission_deadline": f"{n.get('submission_deadline_date', '')[:10]} {n.get('submission_deadline_time', '')}".strip(), "publication_date": parse_d(n.get("noticedate", "")), "procurement_method": n.get("procurement_method_name", ""), "description": n.get("bid_description", ""), "documents_url": f"https://projects.worldbank.org/en/projects-operations/procurement-detail/{nid}", "contact_name": n.get("contact_name", ""), "contact_email": n.get("contact_email", ""), "contact_phone": n.get("contact_phone_no", ""), "source_url": f"https://projects.worldbank.org/en/projects-operations/procurement-detail/{nid}", "language": n.get("notice_lang_name", "English"), "eligibility": "World Bank Procurement Regulations"}))
            off += 100
            time.sleep(0.3)
        except Exception as e: print(f"  err: {e}"); break
    print(f"  WB: {len(out)}"); return out

def fetch_undp():
    print("[2/6] UNDP..."); out = []
    try:
        r = requests.get("https://procurement-notices.undp.org/search.cfm", params={"displayed_record": 1000, "start": 0}, headers=H, timeout=60)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")
        for row in soup.find_all("a", class_=re.compile(r"vacanciesTable.*row", re.I)):
            text = row.get_text(" ", strip=True)
            if "TAJIK" not in text.upper(): continue
            href = row.get("href", "")
            if href and not href.startswith("http"): href = f"https://procurement-notices.undp.org/{href}"
            tm = re.search(r"Title\s*(.+?)\s*Ref No", text); title = tm.group(1).strip() if tm else ""
            rm = re.search(r"Ref No\s*(\S+)", text); ref = rm.group(1) if rm else ""
            pm = re.search(r"Posted\s*(\d{1,2}-\w+-\d+)", text); posted = parse_d(pm.group(1)) if pm else ""
            dm = re.search(r"Deadline\s*(.+?)(?:\s*Posted|$)", text); dl = dm.group(1).strip() if dm else ""
            tym = re.search(r"Procurement Method\s*(.+?)(?:\s*UNDP|$)", text) or re.search(r"Type\s*(.+?)(?:\s*UNDP|$)", text)
            ptype = tym.group(1).strip() if tym else ""
            out.append(normalize({"source": "UNDP", "tender_id": ref, "title_en": title, "title_original": title, "donor": "UNDP", "funding_type": "Grant", "country": "Tajikistan", "organization": "UNDP Tajikistan", "publication_date": posted, "procurement_method": ptype, "source_url": href, "description": text[:1500], "language": "English"}))
    except Exception as e: print(f"  err: {e}")
    print(f"  UNDP: {len(out)}"); return out

def fetch_investcom():
    print("[3/6] investcom.tj..."); out = []
    try:
        r = requests.get("https://investcom.tj/tenders.html", headers=H, timeout=30)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")
        table = soup.find("table")
        if not table: return out
        for row in table.find_all("tr")[1:]:
            cells = row.find_all("td")
            if len(cells) < 5: continue
            org, proj, subj = cells[0].get_text(strip=True), cells[1].get_text(strip=True), cells[2].get_text(strip=True)
            dl_raw = cells[3].get_text(strip=True)
            doc = cells[4].find("a", href=True); doc_url = doc["href"] if doc else ""
            if not in_window(dl_raw): continue
            out.append(normalize({"source": "investcom.tj", "title_tj": subj, "title_ru": subj, "title_original": subj, "donor": "Госкоминвест РТ", "funding_type": "Grant/State", "country": "Tajikistan", "organization": org, "description": proj, "submission_deadline": dl_raw, "documents_url": doc_url, "source_url": "https://investcom.tj/tenders.html", "language": "Tajik/Russian"}))
    except Exception as e: print(f"  err: {e}")
    print(f"  inv: {len(out)}"); return out

def fetch_aedpmu():
    print("[4/6] aedpmu.tj..."); out = []
    try:
        for pg in range(1, 6):
            url = "https://aedpmu.tj/en/category/obyavlenie/tendery/" + (f"page/{pg}/" if pg > 1 else "")
            r = requests.get(url, headers=H, timeout=30)
            if r.status_code != 200: break
            soup = BeautifulSoup(r.text, "lxml")
            arts = soup.find_all("article")
            if not arts: break
            for art in arts:
                te = art.find("time"); pd_str = te.get("datetime", "") if te else ""
                if not pd_str:
                    txt = art.get_text(); dm = re.search(r"(\d{1,2}\s+\w+\s+\d{4})", txt)
                    pd_str = dm.group(1) if dm else ""
                if not in_window(pd_str): continue
                te2 = art.find(["h2", "h3", "h1"])
                if not te2: continue
                lnk = te2.find("a", href=True) or art.find("a", href=True)
                if not lnk: continue
                title = te2.get_text(strip=True); url_full = lnk["href"]
                m = re.search(r"/(\d+)/?$", url_full)
                out.append(normalize({"source": "aedpmu.tj", "tender_id": m.group(1) if m else "", "title_en": title, "title_original": title, "donor": "World Bank (SRASP)", "funding_type": "Grant", "country": "Tajikistan", "organization": "AED PMU", "category": "IT/Agriculture", "publication_date": parse_d(pd_str), "source_url": url_full, "language": "English", "eligibility": "World Bank Procurement Regulations"}))
    except Exception as e: print(f"  err: {e}")
    print(f"  aed: {len(out)}"); return out

def fetch_tenders_tj():
    print("[5/6] tenders.tj..."); out = []
    base = "https://www.tenders.tj"
    try:
        for pg in range(1, 4):
            r = requests.get(f"{base}/index.php?do=poisk&page={pg}", headers=H, timeout=30)
            if r.status_code != 200: break
            soup = BeautifulSoup(r.text, "lxml")
            for it in soup.find_all("a", href=re.compile(r"/procurement/\d+\.html")):
                href = it.get("href", ""); title = it.get_text(" ", strip=True)
                if not title or len(title) < 5: continue
                if not href.startswith("http"): href = f"{base}{href}"
                m = re.search(r"/procurement/(\d+)\.html", href)
                out.append(normalize({"source": "tenders.tj", "tender_id": m.group(1) if m else "", "title_ru": title, "title_tj": title, "title_original": title, "donor": "Агрегатор госзакупок РТ", "funding_type": "State/Donor", "country": "Tajikistan", "source_url": href, "language": "Russian/Tajik"}))
            time.sleep(0.3)
        for r in out[:30]:
            try:
                d = requests.get(r["source_url"], headers=H, timeout=15)
                if d.status_code == 200:
                    s = BeautifulSoup(d.text, "lxml"); txt = s.get_text("\n", strip=True)
                    m1 = re.search(r"Дата публикации:\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})", txt)
                    if m1: r["publication_date"] = parse_d(m1.group(1))
                    m2 = re.search(r"Крайний срок / Deadline:\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})", txt)
                    if m2: r["submission_deadline"] = m2.group(1)
                    m3 = re.search(r"Организатор:\s*([^\n]+)", txt)
                    if m3: r["organization"] = m3.group(1).strip()
                    m4 = re.search(r"Отрасль:\s*([^\n]+)", txt)
                    if m4: r["category"] = m4.group(1).strip()
                    m5 = re.search(r"Контактный E-mail:\s*([^\n]+)", txt)
                    if m5: r["contact_email"] = m5.group(1).strip()
            except: pass
            time.sleep(0.2)
    except Exception as e: print(f"  err: {e}")
    print(f"  tenders: {len(out)}"); return out

async def fetch_eproc():
    print("[6/6] eprocurement.gov.tj..."); out = []
    if not HAS_PLAYWRIGHT: return out
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=['--no-sandbox'])
            ctx = await browser.new_context(user_agent=UA)
            page = await ctx.new_page()
            await page.goto("https://eprocurement.gov.tj/ru/searchanno", wait_until="domcontentloaded", timeout=90000)
            await page.wait_for_timeout(5000)
            try:
                await page.fill('input[name="date_start"]', CUTOFF.strftime("%Y-%m-%d %H:%M:%S"))
                await page.fill('input[name="date_end"]', NOW.strftime("%Y-%m-%d %H:%M:%S"))
                sb = await page.query_selector_all('button[type="submit"], input[type="submit"]')
                if sb: await sb[0].click(); await page.wait_for_timeout(5000)
            except: pass
            html = await page.content(); soup = BeautifulSoup(html, "lxml")
            for t in soup.find_all("table"):
                rows = t.find_all("tr")
                if len(rows) < 5: continue
                if "объявления" not in rows[0].get_text(" ", strip=True).lower() and "название" not in rows[0].get_text(" ", strip=True).lower(): continue
                for row in rows[1:]:
                    cells = row.find_all("td")
                    if len(cells) < 5: continue
                    link = row.find("a", href=True); href = link.get("href", "") if link else ""
                    org = cells[1].get_text(strip=True)
                    title = cells[3].get_text(strip=True) if len(cells) > 3 else ""
                    de = cells[7].get_text(strip=True) if len(cells) > 7 else ""
                    if href and not href.startswith("http"): href = f"https://eprocurement.gov.tj{href}"
                    out.append(normalize({"source": "eprocurement.gov.tj", "title_ru": title, "title_tj": title, "title_original": title, "donor": "Госзакупки РТ", "funding_type": "State Budget", "country": "Tajikistan", "organization": org, "submission_deadline": de, "source_url": href or "https://eprocurement.gov.tj/ru/searchanno", "language": "Russian/Tajik"}))
                break
            await browser.close()
    except Exception as e: print(f"  err: {e}")
    print(f"  eproc: {len(out)}"); return out

def build_excel(records):
    print("Building Excel...")
    df = pd.DataFrame(records)
    cols = ["source", "tender_id", "title_en", "title_ru", "title_tj", "title_original", "donor", "funding_type", "country", "region", "organization", "category", "publication_date", "submission_deadline", "procurement_method", "eligibility", "description", "documents_url", "contact_name", "contact_email", "contact_phone", "source_url", "language", "scraped_at"]
    df = df[[c for c in cols if c in df.columns]]
    df.to_csv(OUT_DIR / f"tenders_tj_{TODAY.isoformat()}.csv", index=False, encoding="utf-8-sig")
    df.to_excel(OUT_DIR / f"tenders_tj_{TODAY.isoformat()}.xlsx", index=False, engine="openpyxl")
    print(f"  -> {len(df)} rows")
    return df

def build_catalog(df):
    print("Building catalog...")
    df = df.copy()
    df['title_ru_main'] = df.apply(title_ru, axis=1)
    df['cat_ru'] = df['category'].map(CAT_RU).fillna(df['category'])
    df['donor_ru'] = df['donor'].map(DONOR_RU).fillna(df['donor'])
    df['method_ru'] = df['procurement_method'].map(METHOD_RU).fillna(df['procurement_method'].fillna('—'))
    df['dl_dt'] = pd.to_datetime(df['submission_deadline'], errors='coerce')
    def status(row):
        dl = row['dl_dt']; src = row['source']
        if pd.isna(dl):
            if src == 'World Bank': return 'Активен (ВБ)'
            if src == 'aedpmu.tj': return 'Активен (aedpmu)'
            return 'Без дедлайна'
        days = (dl - pd.Timestamp(NOW)).days
        if days < 0: return f'Истёк ({abs(days)} дн.)'
        if days <= 3: return f'Срочно — {days} дн.'
        if days <= 7: return f'Скоро — {days} дн.'
        if days <= 30: return f'Активен — {days} дн.'
        return f'Долгосрочный — {days} дн.'
    df['status'] = df.apply(status, axis=1)
    def prio(row):
        s = row['status']
        if 'Срочно' in s or 'Скоро' in s: return 1
        if 'Истёк' in s: return 5
        if 'Долгосрочный' in s: return 4
        if 'Активен' in s: return 2
        return 3
    df['priority'] = df.apply(prio, axis=1)
    df = df.sort_values(['priority', 'publication_date'], ascending=[True, False])
    records = []
    for _, r in df.iterrows():
        records.append({'priority': int(r['priority']), 'status': str(r['status']), 'source': str(r['source']), 'donor': str(r['donor_ru']), 'category': str(r['cat_ru']), 'title': str(r['title_ru_main'])[:300], 'title_en': str(r.get('title_en', ''))[:300] if pd.notna(r.get('title_en', '')) else '', 'method': str(r['method_ru']), 'organization': str(r.get('organization', '')) if pd.notna(r.get('organization', '')) else '—', 'publication_date': str(r['publication_date']) if pd.notna(r['publication_date']) else '—', 'submission_deadline': str(r['submission_deadline']) if pd.notna(r['submission_deadline']) else '—', 'description': str(r.get('description', '')) if pd.notna(r.get('description', '')) else '', 'source_url': str(r.get('source_url', '')) if pd.notna(r.get('source_url', '')) else '#'})
    data_js = json.dumps(records, ensure_ascii=False)
    with open(OUT_DIR / f"catalog_{TODAY.isoformat()}.html", "w") as f: f.write(CATALOG_HTML.replace("__DATA__", data_js))
    print(f"  -> catalog_{TODAY.isoformat()}.html")

def build_dashboard(df):
    print("Building dashboard...")
    df = df.copy()
    df['pub_month'] = pd.to_datetime(df['publication_date'], errors='coerce').dt.to_period('M').astype(str).fillna('—')
    summary = {'total': int(len(df)), 'by_source': df.groupby('source').size().to_dict(), 'by_category': df.groupby('category').size().to_dict(), 'by_donor': df.groupby('donor').size().to_dict(), 'by_pub_month': df[df['pub_month']!='—'].groupby('pub_month').size().to_dict()}
    data_js = json.dumps(summary, default=str, ensure_ascii=False)
    with open(OUT_DIR / f"dashboard_{TODAY.isoformat()}.html", "w") as f: f.write(DASHBOARD_HTML.replace("__DATA__", data_js))
    print(f"  -> dashboard_{TODAY.isoformat()}.html")

async def main():
    print(f"\n=== Parser run: {NOW.isoformat()} ===\n")
    all_records = []
    all_records.extend(fetch_worldbank())
    all_records.extend(fetch_undp())
    all_records.extend(fetch_investcom())
    all_records.extend(fetch_aedpmu())
    all_records.extend(fetch_tenders_tj())
    all_records.extend(await fetch_eproc())
    seen = set(); uniq = []
    for r in all_records:
        k = (r["source"], r.get("tender_id", ""), r.get("source_url", ""))
        if k in seen: continue
        seen.add(k); uniq.append(r)
    print(f"\n=== Total unique: {len(uniq)} ===\n")
    df = build_excel(uniq)
    build_catalog(df)
    build_dashboard(df)
    print(f"\nDone! Files in {OUT_DIR}/")

CATALOG_HTML = '''<!DOCTYPE html><html lang="ru"><head><meta charset="UTF-8"><title>Каталог IT-тендеров — Таджикистан</title>
<style>*{box-sizing:border-box;margin:0;padding:0}body{font-family:sans-serif;background:#0f1419;color:#e6edf3;padding:20px}
.h{text-align:center;margin-bottom:20px}.h h1{font-size:26px;background:linear-gradient(135deg,#58a6ff,#a371f7);-webkit-background-clip:text;-webkit-text-fill-color:transparent;display:inline-block}
.h .s{color:#8b949e;font-size:13px;margin-top:6px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;max-width:1600px;margin:0 auto 20px}
.kpi{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:14px 16px}.kpi .n{font-size:28px;font-weight:800}.kpi .l{font-size:11px;color:#8b949e;text-transform:uppercase}.kpi .n.hot{color:#f85149}
.fs{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:16px;margin:0 auto 20px;max-width:1600px;display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}
.fs label{display:block;font-size:11px;color:#8b949e;text-transform:uppercase;margin-bottom:4px}
.fs select,.fs input{width:100%;padding:8px;border:1px solid #30363d;border-radius:6px;background:#0d1117;color:#e6edf3}
.rb{text-align:center;color:#8b949e;margin-bottom:12px}
.cs{max-width:1600px;margin:0 auto;display:flex;flex-direction:column;gap:12px}
.c{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:16px}.c.p1{border-left:4px solid #f85149}.c.p2{border-left:4px solid #3fb950}.c.p4{border-left:4px solid #58a6ff}
.bd{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;margin-bottom:8px}
.bg{display:flex;gap:6px;flex-wrap:wrap}
.b{font-size:10px;padding:3px 8px;border-radius:10px;font-weight:600}
.b.s{background:#21262d;color:#c9d1d9}.b.d{background:#1F6FEB22;color:#58a6ff;border:1px solid #58a6ff44}.b.c{background:#A371F722;color:#a371f7;border:1px solid #a371f744}
.st{font-size:11px;padding:4px 10px;border-radius:10px;font-weight:700;color:#fff}
.st-1{background:#f85149}.st-2{background:#d29922}.st-3{background:#3fb950}.st-4{background:#58a6ff}.st-5{background:#6e7681}.st-0{background:#21262d;color:#8b949e}
.t{font-size:15px;font-weight:600;margin:4px 0}.te{font-size:11px;color:#6e7681;font-style:italic}
.o{font-size:12px;color:#8b949e;margin-bottom:6px}
.m{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:6px;font-size:12px;margin-bottom:8px}
.mi{display:flex;flex-direction:column}.ml{font-size:10px;color:#6e7681;text-transform:uppercase}.mv{color:#c9d1d9}
.a{display:flex;gap:8px;flex-wrap:wrap}
.ac{padding:6px 12px;border-radius:6px;font-size:12px;text-decoration:none;font-weight:600;background:#1F6FEB;color:#fff}
em{text-align:center;padding:60px 20px;color:#8b949e}
</style></head><body>
<div class="h"><h1>Каталог IT-тендеров — Таджикистан</h1><div class="s">Автообновление каждые 6 часов</div></div>
<div class="kpis">
  <div class="kpi"><div class="l">Срочно</div><div class="n hot" id="k1">0</div></div>
  <div class="kpi"><div class="l">Скоро</div><div class="n" id="k2">0</div></div>
  <div class="kpi"><div class="l">IT-разработка</div><div class="n" id="k3">0</div></div>
  <div class="kpi"><div class="l">Оборудование</div><div class="n" id="k4">0</div></div>
  <div class="kpi"><div class="l">Всего</div><div class="n" id="k5">0</div></div>
</div>
<div class="fs">
  <div><label>Поиск</label><input type="text" id="q"></div>
  <div><label>Категория</label><select id="fcat"><option value="">Все</option></select></div>
  <div><label>Донор</label><select id="fdon"><option value="">Все</option></select></div>
  <div><label>Актуальность</label><select id="fst"><option value="">Все</option><option value="Срочно">Срочно</option><option value="Скоро">Скоро</option><option value="Активен">Активен</option></select></div>
  <div><label>Сортировка</label><select id="fsr"><option value="priority">По приоритету</option><option value="deadline">По дедлайну</option><option value="date">По дате</option></select></div>
</div>
<div class="rb">Показано: <strong id="sh">0</strong> из <strong id="tot">0</strong></div>
<div class="cs" id="cs"></div>
<script>
const data = __DATA__;
const uniq = a => [...new Set(a)].sort();
const fill = (id, vs) => { const s = document.getElementById(id); vs.forEach(v => { if (v) { const o = document.createElement('option'); o.value = v; o.textContent = v; s.appendChild(o); } }); };
fill('fcat', uniq(data.map(d => d.category)));
fill('fdon', uniq(data.map(d => d.donor)));
const stCls = s => { if (s.includes('Срочно')) return 'st-1'; if (s.includes('Скоро')) return 'st-2'; if (s.includes('Активен')) return 'st-3'; if (s.includes('Долгосрочный')) return 'st-4'; if (s.includes('Истёк')) return 'st-5'; return 'st-0'; };
const esc = s => String(s || '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function render() {
  const q = document.getElementById('q').value.toLowerCase();
  const cat = document.getElementById('fcat').value;
  const don = document.getElementById('fdon').value;
  const st = document.getElementById('fst').value;
  const sr = document.getElementById('fsr').value;
  let f = data.filter(d => {
    if (q && ![d.title, d.title_en, d.description, d.organization, d.category].join(' ').toLowerCase().includes(q)) return false;
    if (cat && d.category !== cat) return false;
    if (don && d.donor !== don) return false;
    if (st === 'Срочно' && !d.status.includes('Срочно')) return false;
    if (st === 'Скоро' && !d.status.includes('Скоро') && !d.status.includes('Срочно')) return false;
    if (st === 'Активен' && !d.status.includes('Активен')) return false;
    return true;
  });
  if (sr === 'priority') f.sort((a, b) => (a.priority || 99) - (b.priority || 99));
  if (sr === 'deadline') f.sort((a, b) => new Date(a.submission_deadline || '9999') - new Date(b.submission_deadline || '9999'));
  if (sr === 'date') f.sort((a, b) => new Date(b.publication_date || 0) - new Date(a.publication_date || 0));
  document.getElementById('cs').innerHTML = f.length ? f.map(d => {
    const tEn = (d.title_en && d.title_en !== d.title) ? d.title_en : '';
    return '<div class="c p' + (d.priority || 3) + '"><div class="bd"><div style="flex:1;min-width:0;"><div class="bg" style="margin-bottom:6px;"><span class="b s">' + esc(d.source) + '</span><span class="b d">' + esc(d.donor) + '</span><span class="b c">' + esc(d.category) + '</span></div></div><span class="st ' + stCls(d.status) + '">' + esc(d.status) + '</span></div><div class="t">' + esc(d.title) + '</div>' + (tEn ? '<div class="te">EN: ' + esc(tEn) + '</div>' : '') + '<div class="o">' + esc(d.organization) + '</div><div class="m"><div class="mi"><span class="ml">Метод</span><span class="mv">' + esc(d.method) + '</span></div><div class="mi"><span class="ml">Опубликован</span><span class="mv">' + esc(d.publication_date) + '</span></div><div class="mi"><span class="ml">Дедлайн</span><span class="mv">' + esc(d.submission_deadline) + '</span></div></div><div class="a"><a class="ac" href="' + esc(d.source_url) + '" target="_blank">Открыть</a></div></div>';
  }).join('') : '<em>Ничего не найдено</em>';
  document.getElementById('sh').textContent = f.length;
}
function kpis() {
  document.getElementById('k1').textContent = data.filter(d => (d.status || '').includes('Срочно')).length;
  document.getElementById('k2').textContent = data.filter(d => (d.status || '').includes('Скоро')).length;
 const itCats = ['IT-разработка', 'IT-разработка (софт, системы)', 'Поставка IT-оборудования', 'IT Equipment Supply', 'Телеком/Сеть', 'Telecom / Network', 'Финтех/Цифровые платежи', 'Fintech / Digital Payments', 'Электронное правительство', 'E-Government / E-Procurement', 'Геоданные/Цифровое с/х', 'Geo-spatial / Digital Agriculture', 'Software / IT Development'];
 const eqCats = ['Поставка IT-оборудования', 'IT Equipment Supply', 'Лабораторное оборудование', 'Lab Equipment', 'Электрооборудование', 'Power / Electrical', 'Техника/Спецтранспорт', 'Machinery / Vehicles', 'Транспорт', 'Vehicles', 'Мебель', 'Furniture', 'Телеком/Сеть', 'Telecom / Network'];
  document.getElementById('k3').textContent = data.filter(d => itCats.includes(d.category)).length;
  document.getElementById('k4').textContent = data.filter(d => eqCats.includes(d.category)).length;
  document.getElementById('k5').textContent = data.length;
  document.getElementById('tot').textContent = data.length;
}
['q', 'fcat', 'fdon', 'fst', 'fsr'].forEach(id => { const e = document.getElementById(id); e.addEventListener('input', render); e.addEventListener('change', render); });
kpis(); render();
</script></body></html>'''

DASHBOARD_HTML = '''<!DOCTYPE html><html lang="ru"><head><meta charset="UTF-8"><title>Дашборд IT-тендеры</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>*{box-sizing:border-box;margin:0;padding:0}body{font-family:sans-serif;background:#0f1419;color:#e6edf3;padding:24px}
.h{text-align:center;margin-bottom:32px}.h h1{font-size:28px;background:linear-gradient(135deg,#58a6ff,#a371f7);-webkit-background-clip:text;-webkit-text-fill-color:transparent;display:inline-block}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:16px;max-width:1400px;margin:0 auto 32px}
.kpi{background:#161b22;border:1px solid #30363d;border-radius:12px;padding:20px;position:relative;overflow:hidden}
.kpi::before{content:'';position:absolute;top:0;left:0;right:0;height:3px;background:linear-gradient(90deg,#58a6ff,#a371f7)}
.kpi .n{font-size:36px;font-weight:800;margin:4px 0}.kpi .l{font-size:13px;color:#8b949e;text-transform:uppercase}
.g{display:grid;grid-template-columns:repeat(auto-fit,minmax(420px,1fr));gap:20px;max-width:1400px;margin:0 auto}
.c{background:#161b22;border:1px solid #30363d;border-radius:12px;padding:20px}
.c h2{font-size:15px;font-weight:600;color:#c9d1d9;margin-bottom:16px;display:flex;align-items:center;gap:8px}
.c h2::before{content:'';width:4px;height:16px;background:#58a6ff;border-radius:2px}
.cw{position:relative;height:280px}.cw.t{height:360px}
</style></head><body>
<div class="h"><h1>Дашборд IT-тендеры — Таджикистан</h1></div>
<div class="kpis">
  <div class="kpi"><div class="l">Всего тендеров</div><div class="n" id="k1">0</div></div>
  <div class="kpi"><div class="l">IT-разработка</div><div class="n" id="k2">0</div></div>
  <div class="kpi"><div class="l">Оборудование</div><div class="n" id="k3">0</div></div>
  <div class="kpi"><div class="l">Консалтинг</div><div class="n" id="k4">0</div></div>
</div>
<div class="g">
  <div class="c"><h2>По источникам</h2><div class="cw"><canvas id="c1"></canvas></div></div>
  <div class="c"><h2>По донорам</h2><div class="cw"><canvas id="c2"></canvas></div></div>
  <div class="c"><h2>По категориям</h2><div class="cw t"><canvas id="c3"></canvas></div></div>
  <div class="c"><h2>Публикации по месяцам</h2><div class="cw"><canvas id="c4"></canvas></div></div>
</div>
<script>
const data = __DATA__;
const colors = ['#58a6ff','#a371f7','#3fb950','#ff8c42','#f85149','#d29922','#d2a8ff','#39c5cf'];
const opt = {responsive:true,maintainAspectRatio:false,plugins:{legend:{labels:{color:'#c9d1d9'}}}};
new Chart(document.getElementById('c1'),{type:'doughnut',data:{labels:Object.keys(data.by_source),datasets:[{data:Object.values(data.by_source),backgroundColor:colors,borderColor:'#0f1419',borderWidth:2}]},options:{...opt,cutout:'55%'}});
new Chart(document.getElementById('c2'),{type:'bar',data:{labels:Object.keys(data.by_donor),datasets:[{data:Object.values(data.by_donor),backgroundColor:colors[0]}]},options:{...opt,indexAxis:'y',plugins:{...opt.plugins,legend:{display:false}},scales:{x:{ticks:{color:'#8b949e'},grid:{color:'#21262d'}},y:{ticks:{color:'#c9d1d9'},grid:{display:false}}}}}),
new Chart(document.getElementById('c3'),{type:'bar',data:{labels:Object.keys(data.by_category),datasets:[{data:Object.values(data.by_category),backgroundColor:colors}]},options:{...opt,indexAxis:'y',plugins:{...opt.plugins,legend:{display:false}},scales:{x:{ticks:{color:'#8b949e'},grid:{color:'#21262d'}},y:{ticks:{color:'#c9d1d9'},grid:{display:false}}}}}),
new Chart(document.getElementById('c4'),{type:'line',data:{labels:Object.keys(data.by_pub_month),datasets:[{data:Object.values(data.by_pub_month),borderColor:colors[0],backgroundColor:'rgba(88,166,255,.15)',fill:true,tension:.3}]},options:{...opt,plugins:{...opt.plugins,legend:{display:false}},scales:{x:{ticks:{color:'#c9d1d9'},grid:{color:'#21262d'}},y:{ticks:{color:'#8b949e'},grid:{color:'#21262d'},beginAtZero:true}}}});
document.getElementById('k1').textContent = data.total;
const itCats = ['Software / IT Development','IT Equipment Supply','Telecom / Network','Fintech / Digital Payments','E-Government / E-Procurement','Geo-spatial / Digital Agriculture'];
const eqCats = ['IT Equipment Supply','Lab Equipment','Power / Electrical','Machinery / Vehicles','Vehicles','Furniture','Telecom / Network'];
const csCats = ['Consulting','Training / TA','Studies / Audit'];
document.getElementById('k2').textContent = Object.entries(data.by_category).filter(([k])=>itCats.includes(k)).reduce((s,[,v])=>s+v,0);
document.getElementById('k3').textContent = Object.entries(data.by_category).filter(([k])=>eqCats.includes(k)).reduce((s,[,v])=>s+v,0);
document.getElementById('k4').textContent = Object.entries(data.by_category).filter(([k])=>csCats.includes(k)).reduce((s,[,v])=>s+v,0);
</script></body></html>'''

if __name__ == "__main__":
    asyncio.run(main())
