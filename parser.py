#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Парсер тендеров с донорским финансированием — Таджикистан"""
import asyncio
import re
import json
import time
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

OUT_DIR = Path("out")
OUT_DIR.mkdir(exist_ok=True)
DAYS_BACK = 90
NOW = datetime.now()
CUTOFF = NOW - timedelta(days=DAYS_BACK)
TODAY = date.today()
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
H = {"User-Agent": UA, "Accept": "text/html,*/*", "Accept-Language": "en-US,en;q=0.9,ru;q=0.8"}

CAT_RU = {
    'Software / IT Development': 'IT-разработка (софт, системы)',
    'IT Equipment Supply': 'Поставка IT-оборудования',
    'Telecom / Network': 'Телеком/Сетевое оборудование',
    'Geo-spatial / Digital Agriculture': 'Геоданные/Цифровое с/х',
    'Fintech / Digital Payments': 'Финтех/Цифровые платежи',
    'E-Government / E-Procurement': 'Электронное правительство/закупки',
    'Lab Equipment': 'Лабораторное оборудование',
    'Power / Electrical': 'Электрооборудование/Питание',
    'Machinery / Vehicles': 'Техника/Спецтранспорт',
    'Vehicles': 'Транспорт',
    'Furniture': 'Мебель',
    'Consulting': 'Консалтинг',
    'Training / TA': 'Обучение/Техпомощь',
    'Studies / Audit': 'Исследования/Аудит',
    'Construction / Civil': 'Строительство/СМР',
    'Infrastructure / Roads': 'Инфраструктура/Дороги',
    'Healthcare / Medical': 'Здравоохранение/Медицина',
    'Other Services': 'Прочие услуги'
}
DONOR_RU = {
    'World Bank (IDA/IBRD)': 'Всемирный банк (IDA/IBRD)',
    'World Bank (SRASP)': 'Всемирный банк (проект SRASP, с/х)',
    'Агрегатор госзакупок РТ': 'Госзакупки РТ (tenders.tj)',
    'Госкоминвест РТ': 'Госкоминвест РТ',
    'UNDP': 'ПРООН (ООН)'
}
SOURCE_RU = {
    'World Bank': 'World Bank API',
    'aedpmu.tj': 'AED PMU (с/х)',
    'tenders.tj': 'tenders.tj (РТ)',
    'UNDP': 'UNDP (ПРООН)',
    'investcom.tj': 'Госкоминвест РТ'
}
METHOD_RU = {
    'Request for Bids': 'RFB (конкурс)',
    'Request for Quotations': 'RFQ (котировки)',
    'Request for Proposals': 'RFP (предложения)',
    'Expression of Interest': 'EOI',
    'Individual Consultant Selection': 'IC (консультант)',
    'Direct Selection': 'Прямой отбор',
    'Тендер': 'Тендер',
    'Запрос ценовых котировок': 'ЗЦК',
    'Прямая закупка': 'Прямая закупка',
    'Аукцион': 'Аукцион'
}
WB_PROJECT_RU = {
    'Tajikistan Digital Foundations Project': 'Проект «Цифровые основы Таджикистана»',
    'Public Finance Management Modernization Project 2': 'Модернизация госфинансов — 2',
    'Social Protection Modernization and Economic Inclusion Project': 'Модернизация соцзащиты',
    'Strengthening Resilience of the Agriculture Sector Project': 'Укрепление с/х (SRASP)',
    'Tajikistan Water Supply and Sanitation Investment Project': 'Водоснабжение и канализация',
    'Tajikistan Millati Solim Project': 'Здоровая нация (Millati Solim)',
    'Tajikistan Strengthening Water and Irrigation Management Project': 'Управление водой и ирригацией',
    'Tajikistan Preparedness and Resilience to Disasters Project': 'Готовность к ЧС',
    'Early Childhood Development': 'Раннее развитие детей',
    'Technical Assistance for Financing Framework for Rogun Hydropower Project': 'ТП для Рогунской ГЭС',
    'Modernizing the National Statistical System in Tajikistan': 'Модернизация статистики',
    'Rural Electrification Project': 'Электрификация сёл',
    'Rural Water Supply and Sanitation Project': 'Сельское водоснабжение',
    'Financial and Private Sector Development Project': 'Развитие финансового сектора'
}


def parse_d(s):
    if not s:
        return ""
    try:
        s = str(s).strip()
        for fmt in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d-%b-%Y", "%d.%m.%Y %H:%M", "%d.%m.%Y"]:
            try:
                return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
            except ValueError:
                pass
        return dateparser.parse(s).strftime("%Y-%m-%d")
    except Exception:
        return str(s)


def in_window(s):
    if not s:
        return True
    try:
        return datetime.strptime(parse_d(s), "%Y-%m-%d") >= CUTOFF
    except Exception:
        return True


def title_ru(row):
    for k, v in WB_PROJECT_RU.items():
        if pd.notna(row.get('title_en', '')) and k in str(row.get('title_en', '')):
            return v
    for col in ['title_tj', 'title_ru', 'title_en', 'title_original']:
        v = row.get(col, '')
        if pd.notna(v) and str(v).strip() and str(v).strip() != 'nan':
            return str(v).strip()
    return '(без названия)'


def normalize(raw):
    base = {k: raw.get(k, "") for k in [
        "source", "tender_id", "title_en", "title_ru", "title_tj", "title_original",
        "donor", "funding_type", "country", "region", "organization", "category",
        "publication_date", "submission_deadline", "procurement_method", "eligibility",
        "description", "documents_url", "contact_name", "contact_email", "contact_phone",
        "source_url", "language"
    ]}
    base["scraped_at"] = NOW.isoformat(timespec="seconds")
    return base


def fetch_worldbank():
    print("[1/6] World Bank API...")
    out = []
    seen = set()
    off = 0
    for _ in range(50):
        try:
            r = requests.get(
                "https://search.worldbank.org/api/v2/procnotices",
                params={"format": "json", "qterm": "Tajikistan", "rows": 100, "os": off},
                headers=H, timeout=30
            )
            r.raise_for_status()
            ns = r.json().get("procnotices", [])
            if not ns:
                break
            for n in ns:
                if n.get("project_ctry_name") != "Tajikistan":
                    continue
                nid = n.get("id", "")
                if nid in seen:
                    continue
                seen.add(nid)
                pub = parse_d(n.get("noticedate", ""))
                if not in_window(pub):
                    continue
                out.append(normalize({
                    "source": "World Bank",
                    "tender_id": nid,
                    "title_en": n.get("project_name", ""),
                    "title_original": n.get("project_name", ""),
                    "donor": "World Bank (IDA/IBRD)",
                    "funding_type": "Loan/Credit/Grant",
                    "country": "Tajikistan",
                    "organization": n.get("contact_organization", ""),
                    "category": n.get("procurement_group", ""),
                    "submission_deadline": f"{n.get('submission_deadline_date', '')[:10]} {n.get('submission_deadline_time', '')}".strip(),
                    "publication_date": pub,
                    "procurement_method": n.get("procurement_method_name", ""),
                    "description": n.get("bid_description", ""),
                    "documents_url": f"https://projects.worldbank.org/en/projects-operations/procurement-detail/{nid}",
                    "contact_name": n.get("contact_name", ""),
                    "contact_email": n.get("contact_email", ""),
                    "contact_phone": n.get("contact_phone_no", ""),
                    "source_url": f"https://projects.worldbank.org/en/projects-operations/procurement-detail/{nid}",
                    "language": n.get("notice_lang_name", "English"),
                    "eligibility": "World Bank Procurement Regulations",
                }))
            off += 100
            if len(ns) < 100:
                break
            time.sleep(0.3)
        except Exception as e:
            print(f"  err: {e}")
            break
    print(f"  WB: {len(out)}")
    return out


def fetch_undp():
    print("[2/6] UNDP...")
    out = []
    try:
        r = requests.get(
            "https://procurement-notices.undp.org/search.cfm",
            params={"displayed_record": 1000, "start": 0},
            headers=H, timeout=60
        )
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")
        for row in soup.find_all("a", class_=re.compile(r"vacanciesTable.*row", re.I)):
            text = row.get_text(" ", strip=True)
            if "TAJIK" not in text.upper():
                continue
            href = row.get("href", "")
            if href and not href.startswith("http"):
                href = f"https://procurement-notices.undp.org/{href}"
            tm = re.search(r"Title\s*(.+?)\s*Ref No", text)
            title = tm.group(1).strip() if tm else ""
            rm = re.search(r"Ref No\s*(\S+)", text)
            ref = rm.group(1) if rm else ""
            pm = re.search(r"Posted\s*(\d{1,2}-\w+-\d+)", text)
            posted = parse_d(pm.group(1)) if pm else ""
            dm = re.search(r"Deadline\s*(.+?)(?:\s*Posted|$)", text)
            dl = dm.group(1).strip() if dm else ""
            tym = re.search(r"Procurement Method\s*(.+?)(?:\s*UNDP|$)", text) or \
                  re.search(r"Type\s*(.+?)(?:\s*UNDP|$)", text)
            ptype = tym.group(1).strip() if tym else ""
            out.append(normalize({
                "source": "UNDP",
                "tender_id": ref,
                "title_en": title,
                "title_original": title,
                "donor": "UNDP",
                "funding_type": "Grant",
                "country": "Tajikistan",
                "organization": "UNDP Tajikistan",
                "publication_date": posted,
                "procurement_method": ptype,
                "source_url": href,
                "description": text[:1500],
                "language": "English",
            }))
    except Exception as e:
        print(f"  err: {e}")
    print(f"  UNDP: {len(out)}")
    return out


def fetch_investcom():
    print("[3/6] investcom.tj...")
    out = []
    try:
        r = requests.get("https://investcom.tj/tenders.html", headers=H, timeout=30)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")
        table = soup.find("table")
        if not table:
            return out
        for row in table.find_all("tr")[1:]:
            cells = row.find_all("td")
            if len(cells) < 5:
                continue
            org = cells[0].get_text(strip=True)
            proj = cells[1].get_text(strip=True)
            subj = cells[2].get_text(strip=True)
            dl_raw = cells[3].get_text(strip=True)
            doc = cells[4].find("a", href=True)
            doc_url = doc["href"] if doc else ""
            if not in_window(dl_raw):
                continue
            out.append(normalize({
                "source": "investcom.tj",
                "title_tj": subj,
                "title_ru": subj,
                "title_original": subj,
                "donor": "Госкоминвест РТ",
                "funding_type": "Grant/State",
                "country": "Tajikistan",
                "organization": org,
                "description": proj,
                "submission_deadline": dl_raw,
                "documents_url": doc_url,
                "source_url": "https://investcom.tj/tenders.html",
                "language": "Tajik/Russian",
            }))
    except Exception as e:
        print(f"  err: {e}")
    print(f"  inv: {len(out)}")
    return out


def fetch_aedpmu():
    print("[4/6] aedpmu.tj...")
    out = []
    try:
        for pg in range(1, 6):
            url = "https://aedpmu.tj/en/category/obyavlenie/tendery/" + (f"page/{pg}/" if pg > 1 else "")
            r = requests.get(url, headers=H, timeout=30)
            if r.status_code != 200:
                break
            soup = BeautifulSoup(r.text, "lxml")
            arts = soup.find_all("article")
            if not arts:
                break
            for art in arts:
                te = art.find("time")
                pd_str = te.get("datetime", "") if te else ""
                if not pd_str:
                    txt = art.get_text()
                    dm = re.search(r"(\d{1,2}\s+\w+\s+\d{4})", txt)
                    pd_str = dm.group(1) if dm else ""
                if not in_window(pd_str):
                    continue
                te2 = art.find(["h2", "h3", "h1"])
                if not te2:
                    continue
                lnk = te2.find("a", href=True) or art.find("a", href=True)
                if not lnk:
                    continue
                title = te2.get_text(strip=True)
                url_full = lnk["href"]
                m = re.search(r"/(\d+)/?$", url_full)
                out.append(normalize({
                    "source": "aedpmu.tj",
                    "tender_id": m.group(1) if m else "",
                    "title_en": title,
                    "title_original": title,
                    "donor": "World Bank (SRASP)",
                    "funding_type": "Grant",
                    "country": "Tajikistan",
                    "organization": "AED PMU / Ministry of Agriculture",
                    "category": "IT/Agriculture",
                    "publication_date": parse_d(pd_str),
                    "source_url": url_full,
                    "language": "English",
                    "eligibility": "World Bank Procurement Regulations",
                }))
    except Exception as e:
        print(f"  err: {e}")
    print(f"  aed: {len(out)}")
    return out


def fetch_tenders_tj():
    print("[5/6] tenders.tj...")
    out = []
    base = "https://www.tenders.tj"
    try:
        for pg in range(1, 4):
            r = requests.get(f"{base}/index.php?do=poisk&page={pg}", headers=H, timeout=30)
            if r.status_code != 200:
                break
            soup = BeautifulSoup(r.text, "lxml")
            for it in soup.find_all("a", href=re.compile(r"/procurement/\d+\.html")):
                href = it.get("href", "")
                title = it.get_text(" ", strip=True)
                if not title or len(title) < 5:
                    continue
                if not href.startswith("http"):
                    href = f"{base}{href}"
                m = re.search(r"/procurement/(\d+)\.html", href)
                out.append(normalize({
                    "source": "tenders.tj",
                    "tender_id": m.group(1) if m else "",
                    "title_ru": title,
                    "title_tj": title,
                    "title_original": title,
                    "donor": "Агрегатор госзакупок РТ",
                    "funding_type": "State/Donor",
                    "country": "Tajikistan",
                    "source_url": href,
                    "language": "Russian/Tajik",
                }))
            time.sleep(0.3)
        print(f"  Fetching details for {min(30, len(out))}...")
        for r in out[:30]:
            try:
                d = requests.get(r["source_url"], headers=H, timeout=15)
                if d.status_code == 200:
                    s = BeautifulSoup(d.text, "lxml")
                    txt = s.get_text("\n", strip=True)
                    m1 = re.search(r"Дата публикации:\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})", txt)
                    if m1:
                        r["publication_date"] = parse_d(m1.group(1))
                    m2 = re.search(r"Крайний срок / Deadline:\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})", txt)
                    if m2:
                        r["submission_deadline"] = m2.group(1)
                    m3 = re.search(r"Организатор:\s*([^\n]+)", txt)
                    if m3:
                        r["organization"] = m3.group(1).strip()
                    m4 = re.search(r"Отрасль:\s*([^\n]+)", txt)
                    if m4:
                        r["category"] = m4.group(1).strip()
                    m5 = re.search(r"Тип объявления\s*([^\n]+)", txt)
                    if m5:
                        r["procurement_method"] = m5.group(1).strip()
                    m6 = re.search(r"Область реализации проекта:\s*([^\n]+)", txt)
                    if m6:
                        r["region"] = m6.group(1).strip()
                    m7 = re.search(r"Контактный E-mail:\s*([^\n]+)", txt)
                    if m7:
                        r["contact_email"] = m7.group(1).strip()
                    m8 = re.search(r"Контактный телефон:\s*([^\n]+)", txt)
                    if m8:
                        r["contact_phone"] = m8.group(1).strip()
                    m9 = re.search(r"Описание:\s*(.+?)(?:Требования|$)", txt, re.DOTALL)
                    if m9:
                        r["description"] = m9.group(1).strip()[:2000]
                    if not in_window(r.get("publication_date", "")):
                        r["__skip__"] = True
            except Exception:
                pass
            time.sleep(0.2)
        out = [r for r in out if not r.get("__skip__")]
    except Exception as e:
        print(f"  err: {e}")
    print(f"  tenders: {len(out)}")
    return out


async def fetch_eproc():
    print("[6/6] eprocurement.gov.tj...")
    out = []
    if not HAS_PLAYWRIGHT:
        return out
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
                if sb:
                    await sb[0].click()
                    await page.wait_for_timeout(5000)
            except Exception:
                pass
            html = await page.content()
            soup = BeautifulSoup(html, "lxml")
            for t in soup.find_all("table"):
                rows = t.find_all("tr")
                if len(rows) < 5:
                    continue
                if not any(k in rows[0].get_text(" ", strip=True).lower() for k in ["объявления", "организатор", "название"]):
                    continue
                for row in rows[1:]:
                    cells = row.find_all("td")
                    if len(cells) < 5:
                        continue
                    link = row.find("a", href=True)
                    href = link.get("href", "") if link else ""
                    nm = re.search(r"anno/(\d+)", href) or re.search(r"view/(\d+)", href)
                    nid = nm.group(1) if nm else cells[0].get_text(strip=True)
                    org = cells[1].get_text(strip=True) if len(cells) > 1 else ""
                    title = cells[3].get_text(strip=True) if len(cells) > 3 else ""
                    method = cells[4].get_text(strip=True) if len(cells) > 4 else ""
                    ds = cells[6].get_text(strip=True) if len(cells) > 6 else ""
                    de = cells[7].get_text(strip=True) if len(cells) > 7 else ""
                    if ds and not in_window(ds):
                        continue
                    if href and not href.startswith("http"):
                        href = f"https://eprocurement.gov.tj{href}"
                    out.append(normalize({
                        "source": "eprocurement.gov.tj",
                        "tender_id": str(nid),
                        "title_ru": title,
                        "title_tj": title,
                        "title_original": title,
                        "donor": "Госзакупки РТ",
                        "funding_type": "State Budget",
                        "country": "Tajikistan",
                        "organization": org,
                        "procurement_method": method,
                        "publication_date": ds,
                        "submission_deadline": de,
                        "source_url": href or "https://eprocurement.gov.tj/ru/searchanno",
                        "language": "Russian/Tajik",
                    }))
                break
            await browser.close()
    except Exception as e:
        print(f"  err: {e}")
    print(f"  eproc: {len(out)}")
    return out


def build_excel(records):
    print("Building Excel...")
    df = pd.DataFrame(records)
    cols = [
        "source", "tender_id", "title_en", "title_ru", "title_tj", "title_original",
        "donor", "funding_type", "country", "region", "organization", "category",
        "publication_date", "submission_deadline", "procurement_method", "eligibility",
        "description", "documents_url", "contact_name", "contact_email", "contact_phone",
        "source_url", "language", "scraped_at"
    ]
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
    df['source_ru'] = df['source'].map(SOURCE_RU).fillna(df['source'])
    df['method_ru'] = df['procurement_method'].map(METHOD_RU).fillna(df['procurement_method'].fillna('—'))
    df['dl_dt'] = pd.to_datetime(df['submission_deadline'], errors='coerce')

    def status(row):
        dl = row['dl_dt']
        src = row['source']
        if pd.isna(dl):
            if src == 'World Bank':
                return 'Активен (ВБ)'
            if src == 'aedpmu.tj':
                return 'Активен (aedpmu)'
            return 'Без дедлайна'
        days = (dl - pd.Timestamp(NOW)).days
        if days < 0:
            return f'Истёк ({abs(days)} дн.)'
        if days <= 3:
            return f'Срочно — {days} дн.'
        if days <= 7:
            return f'Скоро — {days} дн.'
        if days <= 30:
            return f'Активен — {days} дн.'
        return f'Долгосрочный — {days} дн.'

    df['status'] = df.apply(status, axis=1)

    def prio(row):
        s = row['status']
        if 'Срочно' in s or 'Скоро' in s:
            return 1
        if 'Истёк' in s:
            return 5
        if 'Долгосрочный' in s:
            return 4
        if 'Активен' in s:
            return 2
        return 3

    df['priority'] = df.apply(prio, axis=1)
    df = df.sort_values(['priority', 'publication_date'], ascending=[True, False])

    records = []
    for _, r in df.iterrows():
        records.append({
            'priority': int(r['priority']),
            'status': str(r['status']),
            'source': str(r['source_ru']),
            'donor': str(r['donor_ru']),
            'category': str(r['cat_ru']),
            'title': str(r['title_ru_main'])[:300],
            'title_en': str(r.get('title_en', ''))[:300] if pd.notna(r.get('title_en', '')) else '',
            'method': str(r['method_ru']),
            'organization': str(r.get('organization', '')) if pd.notna(r.get('organization', '')) else '—',
            'publication_date': str(r['publication_date']) if pd.notna(r['publication_date']) else '—',
            'submission_deadline': str(r['submission_deadline']) if pd.notna(r['submission_deadline']) else '—',
            'description': str(r.get('description', '')) if pd.notna(r.get('description', '')) else '',
            'contact_email': str(r.get('contact_email', '')) if pd.notna(r.get('contact_email', '')) else '',
            'source_url': str(r.get('source_url', '')) if pd.notna(r.get('source_url', '')) else '#',
        })

    data_js = json.dumps(records, ensure_ascii=False)
    html = CATALOG_HTML.replace("__DATA__", data_js)
    with open(OUT_DIR / f"catalog_{TODAY.isoformat()}.html", "w") as f:
        f.write(html)
    print(f"  -> catalog_{TODAY.isoformat()}.html")


def build_dashboard(df):
    print("Building dashboard...")
    df = df.copy()
    df['pub_month'] = pd.to_datetime(df['publication_date'], errors='coerce').dt.to_period('M').astype(str)
    df['pub_month'] = df['pub_month'].fillna('—')
    summary = {
        'total': int(len(df)),
        'by_source': df.groupby('source').size().to_dict(),
        'by_category': df.groupby('category').size().to_dict(),
        'by_donor': df.groupby('donor').size().to_dict(),
        'by_pub_month': df[df['pub_month'] != '—'].groupby('pub_month').size().to_dict(),
    }
    data_js = json.dumps(summary, default=str, ensure_ascii=False)
    html = DASHBOARD_HTML.replace("__DATA__", data_js)
    with open(OUT_DIR / f"dashboard_{TODAY.isoformat()}.html", "w") as f:
        f.write(html)
    print(f"  -> dashboard_{TODAY.isoformat()}.html")


async def main():
    print(f"\n=== Tenders parser: Tajikistan | {NOW.isoformat()} ===\n")
    all_records = []
    all_records.extend(fetch_worldbank())
    all_records.extend(fetch_undp())
    all_records.extend(fetch_investcom())
    all_records.extend(fetch_aedpmu())
    all_records.extend(fetch_tenders_tj())
    all_records.extend(await fetch_eproc())

    seen = set()
    uniq = []
    for r in all_records:
        k = (r["source"], r.get("tender_id", ""), r.get("source_url", ""))
        if k in seen:
            continue
        seen.add(k)
        uniq.append(r)
    print(f"\n=== Total: {len(uniq)} unique ===\n")
    df = build_excel(uniq)
    build_catalog(df)
    build_dashboard(df)
    print(f"\nDone! Files in {OUT_DIR}/")


CATALOG_HTML = '''<!DOCTYPE html>
<html lang="ru"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>Каталог IT-тендеров — Таджикистан</title>
<style>*{box-sizing:border-box;margin:0;padding:0}body{font-family:-apple-system,BlinkMacSystemFont,sans-serif;background:#0f1419;color:#e6edf3;padding:20px;line-height:1.5}
.header{max-width:1600px;margin:0 auto 24px}.header h1{font-size:26px;font-weight:700;margin-bottom:6px;background:linear-gradient(135deg,#58a6ff,#a371f7);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.header .sub{color:#8b949e;font-size:13px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;max-width:1600px;margin:0 auto 20px}
.kpi{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:14px 16px}.kpi .num{font-size:28px;font-weight:800;color:#e6edf3}.kpi .label{font-size:11px;color:#8b949e;text-transform:uppercase;letter-spacing:.5px}
.kpi.hot .num{color:#f85149}.kpi.it .num{color:#3fb950}
.filters{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:16px;margin:0 auto 20px;max-width:1600px;display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}
.filter label{display:block;font-size:11px;color:#8b949e;text-transform:uppercase;margin-bottom:4px;letter-spacing:.5px}
.filter select,.filter input{width:100%;padding:8px 10px;border:1px solid #30363d;border-radius:6px;background:#0d1117;color:#e6edf3;font-size:13px}
.reset-btn{align-self:end;padding:8px 16px;border:1px solid #f85149;background:transparent;color:#f85149;border-radius:6px;cursor:pointer;font-size:13px;font-weight:600}
.results-bar{max-width:1600px;margin:0 auto 12px;color:#8b949e;font-size:13px}
.cards{max-width:1600px;margin:0 auto;display:flex;flex-direction:column;gap:12px}
.card{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:16px}.card:hover{border-color:#58a6ff}
.card.priority-1{border-left:4px solid #f85149}.card.priority-2{border-left:4px solid #3fb950}.card.priority-4{border-left:4px solid #58a6ff}
.card-top{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;margin-bottom:8px}
.card-badges{display:flex;gap:6px;flex-wrap:wrap;flex-shrink:0}
.badge{font-size:10px;padding:3px 8px;border-radius:10px;font-weight:600;white-space:nowrap}
.badge.source{background:#21262d;color:#c9d1d9}.badge.donor{background:#1F6FEB22;color:#58a6ff;border:1px solid #58a6ff44}.badge.cat{background:#A371F722;color:#a371f7;border:1px solid #a371f744}
.status{font-size:11px;padding:4px 10px;border-radius:10px;font-weight:700}
.status-Срочно, .status-Срочно \u2014 {background:#f85149;color:#fff}
.status-Скоро {background:#d29922;color:#fff}
.status-Активен {background:#3fb950;color:#fff}
.status-Долгосрочный {background:#58a6ff;color:#fff}
.status-Истёк {background:#6e7681;color:#fff}
.status-Без {background:#21262d;color:#8b949e}
.title{font-size:15px;font-weight:600;color:#e6edf3;margin-bottom:4px;line-height:1.4}
.title-en{font-size:11px;color:#6e7681;font-style:italic;margin-bottom:6px}
.org{font-size:12px;color:#8b949e;margin-bottom:6px}
.meta{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:6px;font-size:12px;margin-bottom:8px}
.meta-item{display:flex;flex-direction:column}.meta-label{font-size:10px;color:#6e7681;text-transform:uppercase;letter-spacing:.5px}.meta-val{color:#c9d1d9}
.actions{display:flex;gap:8px;flex-wrap:wrap}
.action{padding:6px 12px;border-radius:6px;font-size:12px;text-decoration:none;font-weight:600;background:#21262d;color:#c9d1d9;border:1px solid #30363d}
.action.primary{background:#1F6FEB;color:#fff;border-color:#1F6FEB}
.empty{text-align:center;padding:60px 20px;color:#8b949e;font-size:14px}
</style></head><body>
<div class="header"><h1>Каталог IT-тендеров и оборудования — Таджикистан</h1>
<div class="sub">Автообновление каждые 6 часов • 90 дней • Все поля на русском</div></div>
<div class="kpis">
  <div class="kpi hot"><div class="label">Срочно</div><div class="num" id="kpi-hot">0</div></div>
  <div class="kpi"><div class="label">Скоро</div><div class="num" id="kpi-soon">0</div></div>
  <div class="kpi it"><div class="label">IT-разработка</div><div class="num" id="kpi-it">0</div></div>
  <div class="kpi"><div class="label">Оборудование</div><div class="num" id="kpi-equip">0</div></div>
  <div class="kpi"><div class="label">Всего</div><div class="num" id="kpi-total">0</div></div>
</div>
<div class="filters">
  <div class="filter"><label>Поиск</label><input type="text" id="f-search" placeholder="digital, IT, AMIS..."></div>
  <div class="filter"><label>Категория</label><select id="f-category"><option value="">Все</option></select></div>
  <div class="filter"><label>Донор</label><select id="f-donor"><option value="">Все</option></select></div>
  <div class="filter"><label>Актуальность</label><select id="f-status"><option value="">Все</option><option value="hot">Срочно</option><option value="soon">Скоро</option><option value="active">Активен</option></select></div>
  <div class="filter"><label>Сортировка</label><select id="f-sort"><option value="priority">По приоритету</option><option value="deadline">По дедлайну</option><option value="date-desc">По дате</option></select></div>
  <div class="filter"><label>&nbsp;</label><button class="reset-btn" onclick="resetFilters()">Сбросить</button></div>
</div>
<div class="results-bar">Показано: <strong id="shown">0</strong> из <strong id="total">0</strong></div>
<div class="cards" id="cards"></div>
<script>
const data = __DATA__;
const uniq = arr => [...new Set(arr)].sort();
const fillSelect = (id, vals) => { const s = document.getElementById(id); vals.forEach(v => { if (v) { const o = document.createElement('option'); o.value = v; o.textContent = v; s.appendChild(o); } }); };
fillSelect('f-category', uniq(data.map(d => d.category)));
fillSelect('f-donor', uniq(data.map(d => d.donor)));
function statusClass(s) {
  if (s.includes('Срочно')) return 'status-Срочно';
  if (s.includes('Скоро')) return 'status-Скоро';
  if (s.includes('Активен')) return 'status-Активен';
  if (s.includes('Долгосрочный')) return 'status-Долгосрочный';
  if (s.includes('Истёк')) return 'status-Истёк';
  return 'status-Без';
}
function escapeHtml(s) { return String(s || '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function render() {
  const q = document.getElementById('f-search').value.toLowerCase();
  const cat = document.getElementById('f-category').value;
  const donor = document.getElementById('f-donor').value;
  const status = document.getElementById('f-status').value;
  const sort = document.getElementById('f-sort').value;
  let f = data.filter(d => {
    if (q && ![d.title, d.title_en, d.description, d.organization, d.category].join(' ').toLowerCase().includes(q)) return false;
    if (cat && d.category !== cat) return false;
    if (donor && d.donor !== donor) return false;
    if (status === 'hot' && !d.status.includes('Срочно')) return false;
    if (status === 'soon' && !d.status.includes('Скоро') && !d.status.includes('Срочно')) return false;
    if (status === 'active' && !d.status.includes('Активен')) return false;
    return true;
  });
  if (sort === 'priority') f.sort((a, b) => (a.priority || 99) - (b.priority || 99));
  if (sort === 'deadline') f.sort((a, b) => new Date(a.submission_deadline || '9999') - new Date(b.submission_deadline || '9999'));
  if (sort === 'date-desc') f.sort((a, b) => new Date(b.publication_date || 0) - new Date(a.publication_date || 0));
  document.getElementById('cards').innerHTML = f.length ? f.map(d => {
    const tEn = (d.title_en && d.title_en !== d.title) ? d.title_en : '';
    return '<div class="card priority-' + (d.priority || 3) + '"><div class="card-top"><div style="flex:1;min-width:0;"><div class="card-badges" style="margin-bottom:6px;"><span class="badge source">' + escapeHtml(d.source) + '</span><span class="badge donor">' + escapeHtml(d.donor) + '</span><span class="badge cat">' + escapeHtml(d.category) + '</span></div></div><span class="status ' + statusClass(d.status) + '">' + escapeHtml(d.status) + '</span></div><div class="title">' + escapeHtml(d.title) + '</div>' + (tEn ? '<div class="title-en">EN: ' + escapeHtml(tEn) + '</div>' : '') + '<div class="org">' + escapeHtml(d.organization) + '</div>' + (d.description ? '<div class="org" style="margin-bottom:8px;">' + escapeHtml(d.description.slice(0, 300)) + (d.description.length > 300 ? '...' : '') + '</div>' : '') + '<div class="meta"><div class="meta-item"><span class="meta-label">Метод</span><span class="meta-val">' + escapeHtml(d.method) + '</span></div><div class="meta-item"><span class="meta-label">Опубликован</span><span class="meta-val">' + escapeHtml(d.publication_date) + '</span></div><div class="meta-item"><span class="meta-label">Дедлайн</span><span class="meta-val">' + escapeHtml(d.submission_deadline) + '</span></div>' + (d.contact_email ? '<div class="meta-item"><span class="meta-label">Email</span><span class="meta-val">' + escapeHtml(d.contact_email) + '</span></div>' : '') + '</div><div class="actions"><a class="action primary" href="' + escapeHtml(d.source_url) + '" target="_blank">Открыть</a></div></div>';
  }).join('') : '<div class="empty"><h3>Ничего не найдено</h3></div>';
  document.getElementById('shown').textContent = f.length;
}
function updateKPIs() {
  document.getElementById('kpi-hot').textContent = data.filter(d => (d.status || '').includes('Срочно')).length;
  document.getElementById('kpi-soon').textContent = data.filter(d => (d.status || '').includes('Скоро')).length;
  const itCats = ['IT-разработка (софт, системы)', 'Поставка IT-оборудования', 'Телеком/Сетевое оборудование', 'Финтех/Цифровые платежи', 'Электронное правительство/закупки', 'Геоданные/Цифровое с/х'];
  const eqCats = ['Поставка IT-оборудования', 'Лабораторное оборудование', 'Электрооборудование/Питание', 'Техника/Спецтранспорт', 'Транспорт', 'Мебель', 'Телеком/Сетевое оборудование'];
  document.getElementById('kpi-it').textContent = data.filter(d => itCats.includes(d.category)).length;
  document.getElementById('kpi-equip').textContent = data.filter(d => eqCats.includes(d.category)).length;
  document.getElementById('kpi-total').textContent = data.length;
  document.getElementById('total').textContent = data.length;
}
function resetFilters() { ['f-search', 'f-category', 'f-donor', 'f-status', 'f-sort'].forEach(id => document.getElementById(id).value = id === 'f-sort' ? 'priority' : ''); render(); }
['f-search', 'f-category', 'f-donor', 'f-status', 'f-sort'].forEach(id => { document.getElementById(id).addEventListener('input', render); document.getElementById(id).addEventListener('change', render); });
updateKPIs(); render();
</script></body></html>'''

DASHBOARD_HTML = '''<!DOCTYPE html>
<html lang="ru"><head><meta charset="UTF-8"><title>Дашборд IT-тендеры — Таджикистан</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>*{box-sizing:border-box;margin:0;padding:0}body{font-family:-apple-system,BlinkMacSystemFont,sans-serif;background:#0f1419;color:#e6edf3;padding:24px}
.header{max-width:1400px;margin:0 auto 32px}.header h1{font-size:28px;font-weight:700;margin-bottom:8px;background:linear-gradient(135deg,#58a6ff,#a371f7);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.header .sub{color:#8b949e;font-size:14px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:16px;max-width:1400px;margin:0 auto 32px}
.kpi{background:#161b22;border:1px solid #30363d;border-radius:12px;padding:20px;position:relative;overflow:hidden}
.kpi::before{content:'';position:absolute;top:0;left:0;right:0;height:3px;background:linear-gradient(90deg,#58a6ff,#a371f7)}
.kpi .num{font-size:36px;font-weight:800;margin:4px 0}.kpi .label{font-size:13px;color:#8b949e;text-transform:uppercase;letter-spacing:.5px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(420px,1fr));gap:20px;max-width:1400px;margin:0 auto}
.card{background:#161b22;border:1px solid #30363d;border-radius:12px;padding:20px}
.card h2{font-size:15px;font-weight:600;color:#c9d1d9;margin-bottom:16px;display:flex;align-items:center;gap:8px}
.card h2 .ico{width:4px;height:16px;background:#58a6ff;border-radius:2px}
.chart-wrap{position:relative;height:280px}.chart-wrap.tall{height:360px}
</style></head><body>
<div class="header"><h1>Дашборд: IT-тендеры и оборудование — Таджикистан</h1>
<div class="sub">Автообновление каждые 6 часов • Период: 90 дней</div></div>
<div class="kpis">
  <div class="kpi"><div class="label">Всего тендеров</div><div class="num" id="kpi-total">0</div></div>
  <div class="kpi"><div class="label">IT-разработка</div><div class="num" id="kpi-it">0</div></div>
  <div class="kpi"><div class="label">Оборудование</div><div class="num" id="kpi-equip">0</div></div>
  <div class="kpi"><div class="label">Консалтинг / TA</div><div class="num" id="kpi-consult">0</div></div>
</div>
<div class="grid">
  <div class="card"><h2><span class="ico"></span>По источникам</h2><div class="chart-wrap"><canvas id="c1"></canvas></div></div>
  <div class="card"><h2><span class="ico"></span>По донорам</h2><div class="chart-wrap"><canvas id="c2"></canvas></div></div>
  <div class="card"><h2><span class="ico"></span>По категориям</h2><div class="chart-wrap tall"><canvas id="c3"></canvas></div></div>
  <div class="card"><h2><span class="ico"></span>Публикации по месяцам</h2><div class="chart-wrap"><canvas id="c4"></canvas></div></div>
</div>
<script>
const data = __DATA__;
const colors = ['#58a6ff','#a371f7','#3fb950','#ff8c42','#f85149','#d29922','#d2a8ff','#39c5cf','#7ee787','#ffa657','#ff7b72','#79c0ff','#e3b341','#bc8cff'];
const opt = {responsive:true,maintainAspectRatio:false,plugins:{legend:{labels:{color:'#c9d1d9'}},tooltip:{backgroundColor:'#161b22',titleColor:'#e6edf3',bodyColor:'#c9d1d9',borderColor:'#30363d',borderWidth:1}}};
new Chart(document.getElementById('c1'),{type:'doughnut',data:{labels:Object.keys(data.by_source),datasets:[{data:Object.values(data.by_source),backgroundColor:colors,borderColor:'#0f1419',borderWidth:2}]},options:{...opt,cutout:'55%'}});
new Chart(document.getElementById('c2'),{type:'bar',data:{labels:Object.keys(data.by_donor).map(s=>s.length>35?s.slice(0,35)+'...':s),datasets:[{data:Object.values(data.by_donor),backgroundColor:colors[0],borderRadius:4}]},options:{...opt,indexAxis:'y',plugins:{...opt.plugins,legend:{display:false}},scales:{x:{ticks:{color:'#8b949e'},grid:{color:'#21262d'}},y:{ticks:{color:'#c9d1d9'},grid:{display:false}}}}}),
new Chart(document.getElementById('c3'),{type:'bar',data:{labels:Object.keys(data.by_category),datasets:[{data:Object.values(data.by_category),backgroundColor:colors,borderRadius:4}]},options:{...opt,indexAxis:'y',plugins:{...opt.plugins,legend:{display:false}},scales:{x:{ticks:{color:'#8b949e'},grid:{color:'#21262d'}},y:{ticks:{color:'#c9d1d9',font:{size:10}},grid:{display:false}}}}}),
new Chart(document.getElementById('c4'),{type:'line',data:{labels:Object.keys(data.by_pub_month),datasets:[{data:Object.values(data.by_pub_month),borderColor:colors[0],backgroundColor:'rgba(88,166,255,.15)',fill:true,tension:.3,borderWidth:2.5,pointBackgroundColor:colors[0],pointRadius:4}]},options:{...opt,plugins:{...opt.plugins,legend:{display:false}},scales:{x:{ticks:{color:'#c9d1d9'},grid:{color:'#21262d'}},y:{ticks:{color:'#8b949e'},grid:{color:'#21262d'},beginAtZero:true}}}});
document.getElementById('kpi-total').textContent = data.total;
const itCats = ['Software / IT Development','IT Equipment Supply','Telecom / Network','Fintech / Digital Payments','E-Government / E-Procurement','Geo-spatial / Digital Agriculture'];
const eqCats = ['IT Equipment Supply','Lab Equipment','Power / Electrical','Machinery / Vehicles','Vehicles','Furniture','Telecom / Network'];
const csCats = ['Consulting','Training / TA','Studies / Audit'];
document.getElementById('kpi-it').textContent = Object.entries(data.by_category).filter(([k])=>itCats.includes(k)).reduce((s,[,v])=>s+v,0);
document.getElementById('kpi-equip').textContent = Object.entries(data.by_category).filter(([k])=>eqCats.includes(k)).reduce((s,[,v])=>s+v,0);
document.getElementById('kpi-consult').textContent = Object.entries(data.by_category).filter(([k])=>csCats.includes(k)).reduce((s,[,v])=>s+v,0);
</script></body></html>'''


if __name__ == "__main__":
    asyncio.run(main())
