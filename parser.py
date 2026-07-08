#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Парсер тендеров с донорским финансированием — Таджикистан"""
import asyncio, os, re, json, time
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
DAYS_BACK = 90; NOW = datetime.now()
CUTOFF = NOW - timedelta(days=DAYS_BACK); TODAY = date.today()
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
H = {"User-Agent": UA, "Accept": "text/html,*/*", "Accept-Language": "en-US,en;q=0.9,ru;q=0.8"}

CAT_RU = {'Software / IT Development': 'IT-разработка (софт, системы)', 'IT Equipment Supply': 'Поставка IT-оборудования', 'Telecom / Network': 'Телеком/Сетевое оборудование', 'Geo-spatial / Digital Agriculture': 'Геоданные/Цифровое с/х', 'Fintech / Digital Payments': 'Финтех/Цифровые платежи', 'E-Government / E-Procurement': 'Электронное правительство/закупки', 'Lab Equipment': 'Лабораторное оборудование', 'Power / Electrical': 'Электрооборудование/Питание', 'Machinery / Vehicles': 'Техника/Спецтранспорт', 'Vehicles': 'Транспорт', 'Furniture': 'Мебель', 'Consulting': 'Консалтинг', 'Training / TA': 'Обучение/Техпомощь', 'Studies / Audit': 'Исследования/Аудит', 'Construction / Civil': 'Строительство/СМР', 'Infrastructure / Roads': 'Инфраструктура/Дороги', 'Healthcare / Medical': 'Здравоохранение/Медицина', 'Other Services': 'Прочие услуги'}
DONOR_RU = {'World Bank (IDA/IBRD)': 'Всемирный банк (IDA/IBRD)', 'World Bank (SRASP)': 'Всемирный банк (проект SRASP, с/х)', 'Агрегатор госзакупок РТ': 'Госзакупки РТ (tenders.tj)', 'Госкоминвест РТ': 'Госкоминвест РТ', 'UNDP': 'ПРООН (ООН)'}
SOURCE_RU = {'World Bank': 'World Bank API', 'aedpmu.tj': 'AED PMU (с/х)', 'tenders.tj': 'tenders.tj (РТ)', 'UNDP': 'UNDP (ПРООН)', 'investcom.tj': 'Госкоминвест РТ'}
METHOD_RU = {'Request for Bids': 'RFB (конкурс)', 'Request for Quotations': 'RFQ (котировки)', 'Request for Proposals': 'RFP (предложения)', 'Expression of Interest': 'EOI', 'Individual Consultant Selection': 'IC (консультант)', 'Direct Selection': 'Прямой отбор', 'Тендер': 'Тендер', 'Запрос ценовых котировок': 'ЗЦК', 'Прямая закупка': 'Прямая закупка', 'Аукцион': 'Аукцион'}
WB_PROJECT_RU = {'Tajikistan Digital Foundations Project': 'Проект «Цифровые основы Таджикистана»', 'Public Finance Management Modernization Project 2': 'Модернизация госфинансов — 2', 'Social Protection Modernization and Economic Inclusion Project': 'Модернизация соцзащиты', 'Strengthening Resilience of the Agriculture Sector Project': 'Укрепление с/х (SRASP)', 'Tajikistan Water Supply and Sanitation Investment Project': 'Водоснабжение и канализация', 'Tajikistan Millati Solim Project': 'Здоровая нация (Millati Solim)', 'Tajikistan Strengthening Water and Irrigation Management Project': 'Управление водой и ирригацией', 'Tajikistan Preparedness and Resilience to Disasters Project': 'Готовность к ЧС', 'Early Childhood Development': 'Раннее развитие детей', 'Technical Assistance for Financing Framework for Rogun Hydropower Project': 'ТП для Рогунской ГЭС', 'Modernizing the National Statistical System in Tajikistan': 'Модернизация статистики', 'Rural Electrification Project': 'Электрификация сёл', 'Rural Water Supply and Sanitation Project': 'Сельское водоснабжение', 'Financial and Private Sector Development Project': 'Развитие финансового сектора'}

def parse_d(s):
    if not s: return ""
    try:
        for f in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d-%b-%Y", "%d.%m.%Y %H:%M", "%d.%m.%Y"]:
            try: return datetime.strptime(str(s).strip(), f).strftime("%Y-%m-%d")
            except: pass
        return dateparser.parse(str(s)).strftime("%Y-%m-%d")
    except: return str(s)

def in_window(s):
    if not s: return True
    try: return datetime.strptime(parse_d(s), "%Y-%m-%d") >= CUTOFF
    except: return True

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
    return {k: raw.get(k, "") for k in ["source", "tender_id", "title_en", "title_ru", "title_tj", "title_original", "donor", "funding_type", "country", "region", "organization", "category", "publication_date", "submission_deadline", "procurement_method", "eligibility", "description", "documents_url", "contact_name", "contact_email", "contact_phone", "source_url", "language"]} | {"scraped_at": NOW.isoformat(timespec="seconds")}

print("Файл создан. Жди часть 2 из 3.")

def fetch_undp():
    print("[2/6] UNDP..."); out = []
    try:
        r = requests.get("https://procurement-notices.undp.org/search.cfm", params={"displayed_record": 1000, "start": 0}, headers=H, timeout=60)
        r.raise_for_status(); soup = BeautifulSoup(r.text, "lxml")
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
        r.raise_for_status(); soup = BeautifulSoup(r.text, "lxml")
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
                out.append(normalize({"source": "aedpmu.tj", "tender_id": m.group(1) if m else "", "title_en": title, "title_original": title, "donor": "World Bank (SRASP)", "funding_type": "Grant", "country": "Tajikistan", "organization": "AED PMU / Ministry of Agriculture", "category": "IT/Agriculture", "publication_date": parse_d(pd_str), "source_url": url_full, "language": "English", "eligibility": "World Bank Procurement Regulations"}))
    except Exception as e: print(f"  err: {e}")
    print(f"  aed: {len(out)}"); return out

