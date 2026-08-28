#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Парсер тендеров с донорским финансированием — Таджикистан.
Собирает тендеры из 11 источников, категоризирует, переводит заголовки
(best-effort) и генерирует Excel/CSV + HTML-каталог + HTML-дашборд.
"""
import asyncio, re, json, time, hashlib
from datetime import datetime, date, timedelta
from pathlib import Path
import requests
from bs4 import BeautifulSoup
import pandas as pd
from dateutil import parser as dateparser

try:
    from playwright.async_api import async_playwright
    HAS_PW = True
except Exception:
    HAS_PW = False

OUT = Path("out"); OUT.mkdir(exist_ok=True)
NOW = datetime.now()
CUTOFF = NOW - timedelta(days=90)
TODAY = date.today()
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36"
H = {"User-Agent": UA, "Accept": "text/html,*/*", "Accept-Language": "en-US,en;q=0.9,ru;q=0.8"}

# Сколько раз повторять сетевой запрос при ошибке, и с какой паузой (backoff)
RETRIES = 3
RETRY_BACKOFF = 2.0

# Сводка по источникам: сколько записей собрано / были ли ошибки — печатается в конце
SOURCE_SUMMARY = {}

# ---------------------------------------------------------------------------
# СЛОВАРИ ПЕРЕВОДОВ
# ---------------------------------------------------------------------------

CAT_RU = {
    'Software / IT Development': 'IT-разработка',
    'IT Equipment Supply': 'Поставка IT-оборудования',
    'Telecom / Network': 'Телеком/Сеть',
    'Geo-spatial / Digital Agriculture': 'Геоданные/Цифровое с/х',
    'Fintech / Digital Payments': 'Финтех/Цифровые платежи',
    'E-Government / E-Procurement': 'Электронное правительство',
    'Lab Equipment': 'Лабораторное оборудование',
    'Power / Electrical': 'Электрооборудование',
    'Machinery / Vehicles': 'Техника/Спецтранспорт',
    'Vehicles': 'Транспорт',
    'Furniture': 'Мебель',
    'Consulting': 'Консалтинг',
    'Training / TA': 'Обучение/Техпомощь',
    'Studies / Audit': 'Исследования/Аудит',
    'Construction / Civil': 'Строительство',
    'Infrastructure / Roads': 'Инфраструктура/Дороги',
    'Healthcare / Medical': 'Здравоохранение',
    'Other Services': 'Прочие услуги',
}
DONOR_RU = {
    'World Bank (IDA/IBRD)': 'Всемирный банк',
    'World Bank (SRASP)': 'Всемирный банк (SRASP)',
    'Агрегатор госзакупок РТ': 'Госзакупки РТ',
    'Госкоминвест РТ': 'Госкоминвест РТ',
    'UNDP': 'ПРООН',
}
WB_PRJ = {
    'Tajikistan Digital Foundations Project': 'Цифровые основы Таджикистана',
    'Public Finance Management Modernization Project 2': 'Модернизация госфинансов',
    'Social Protection Modernization and Economic Inclusion Project': 'Модернизация соцзащиты',
    'Strengthening Resilience of the Agriculture Sector Project': 'Укрепление с/х (SRASP)',
    'Tajikistan Water Supply and Sanitation Investment Project': 'Водоснабжение и канализация',
    'Tajikistan Millati Solim Project': 'Здоровая нация',
    'Tajikistan Strengthening Water and Irrigation Management Project': 'Управление водой',
    'Tajikistan Preparedness and Resilience to Disasters Project': 'Готовность к ЧС',
    'Early Childhood Development': 'Раннее развитие детей',
    'Modernizing the National Statistical System in Tajikistan': 'Модернизация статистики',
    'Rural Electrification Project': 'Электрификация сёл',
    'Financial and Private Sector Development Project': 'Развитие финансового сектора',
}

# Ключевые слова для автокатегоризации (ищутся в title+description, регистронезависимо)
# Порядок важен: первое совпадение побеждает, поэтому более специфичные категории — выше.
CATEGORY_RULES = [
    ('Fintech / Digital Payments', ['bank id', 'p2g', 'b2g', 'fintech', 'digital payment', 'e-payment', 'платеж']),
    ('Geo-spatial / Digital Agriculture', ['digital soil', 'amis remote', 'remote sensing', 'geo-spatial', 'geospatial', 'gis ', 'digital agriculture', 'цифровое сельское']),
    ('E-Government / E-Procurement', ['e-procurement', 'e-government', 'e-gov', 'edms', 'electronic document management', 'электронное правительство']),
    ('Telecom / Network', ['telecom', 'network cabling', 'fiber', 'broadband', 'wi-fi', 'wifi', 'структурированн', 'локальн', 'lan ', 'wan ']),
    ('Software / IT Development', ['software', 'application development', 'platform', 'portal', 'database', 'веб-сайт', 'веб сайт', 'информационн', 'программн', 'разработка сайта', 'разработка приложения']),
    ('IT Equipment Supply', ['computer', 'server', 'laptop', 'monoblock', 'моноблок', 'printer', 'scanner', 'ноутбук', 'сервер', 'принтер', 'сканер', 'компьютер', 'ит-оборудование', 'it equipment']),
    ('Lab Equipment', ['lab equipment', 'laboratory', 'лабораторн']),
    ('Power / Electrical', ['electrical', 'power supply', 'generator', 'solar', 'электрооборудование', 'генератор', 'солнечн']),
    ('Machinery / Vehicles', ['machinery', 'excavator', 'tractor', 'спецтехника', 'экскаватор', 'трактор']),
    ('Vehicles', ['vehicle', 'automobile', 'car ', 'truck', 'автомобиль', 'транспортн средств']),
    ('Furniture', ['furniture', 'мебель']),
    ('Training / TA', ['training', 'technical assistance', 'capacity building', 'обучение', 'техническая помощь', 'повышение квалификации']),
    ('Studies / Audit', ['study', 'audit', 'assessment', 'исследован', 'аудит', 'оценка']),
    ('Consulting', ['consulting', 'consultant', 'консалтинг', 'консультант', 'услуги консульт']),
    ('Construction / Civil', ['construction', 'civil works', 'строительств', 'строительно-монтажн']),
    ('Infrastructure / Roads', ['road', 'infrastructure', 'дорог', 'инфраструктур']),
    ('Healthcare / Medical', ['medical', 'health', 'hospital', 'медицинск', 'здравоохран', 'больниц']),
]

def categorize(title, description=""):
    """Определяет категорию тендера по ключевым словам в названии+описании."""
    text = f"{title or ''} {description or ''}".lower()
    for cat, keywords in CATEGORY_RULES:
        for kw in keywords:
            if kw in text:
                return cat
    return 'Other Services'

# ---------------------------------------------------------------------------
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ---------------------------------------------------------------------------

def parse_d(s):
    """Парсит дату из строки в разных форматах -> 'YYYY-MM-DD' (или исходную строку, если не смогли)."""
    if not s:
        return ""
    s = str(s).strip()
    if not s:
        return ""
    for f in ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%d-%b-%Y", "%d.%m.%Y", "%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M"]:
        try:
            return datetime.strptime(s, f).strftime("%Y-%m-%d")
        except Exception:
            pass
    try:
        # dayfirst=True — важно для форматов dd.mm.yyyy, характерных для РТ-источников,
        # чтобы pandas/dateutil не перепутали день и месяц
        return dateparser.parse(s, dayfirst=True).strftime("%Y-%m-%d")
    except Exception:
        return s

def in_win(s):
    if not s:
        return True
    try:
        return datetime.strptime(parse_d(s), "%Y-%m-%d") >= CUTOFF
    except Exception:
        return True

def title_ru(row):
    """Выбирает лучший доступный заголовок на русском; если нет — берёт EN/оригинал."""
    for k, v in WB_PRJ.items():
        if pd.notna(row.get('title_en', '')) and k in str(row.get('title_en', '')):
            return v
    for col in ['title_tj', 'title_ru', 'title_en', 'title_original']:
        v = row.get(col, '')
        if pd.notna(v) and str(v).strip() and str(v).strip() != 'nan':
            return str(v).strip()
    return '(без названия)'

def make_stable_id(*parts):
    """Стабильный короткий хэш из набора строк — используется как tender_id,
    когда источник не даёт естественный уникальный идентификатор
    (иначе разные записи схлопнутся в дедупе по одинаковому ключу)."""
    raw = "|".join(str(p or "").strip() for p in parts)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]

def norm(raw):
    base = {k: raw.get(k, "") for k in [
        "source", "tender_id", "title_en", "title_ru", "title_tj", "title_original",
        "donor", "funding_type", "country", "region", "organization", "category",
        "publication_date", "submission_deadline", "procurement_method", "eligibility",
        "description", "documents_url", "contact_name", "contact_email", "contact_phone",
        "source_url", "language",
    ]}
    if not base.get("category"):
        base["category"] = categorize(base.get("title_en") or base.get("title_original") or base.get("title_ru") or base.get("title_tj"), base.get("description"))
    base["scraped_at"] = NOW.isoformat(timespec="seconds")
    return base

_TRANSLATE_CACHE = {}

def translate_en_ru(text, timeout=6):
    """Best-effort перевод короткого EN-текста на RU через бесплатный MyMemory API.
    Не критично для работы парсера: при любой ошибке/недоступности сети просто
    возвращает None, и вызывающий код использует оригинал. Кэшируется в рамках
    одного запуска, чтобы не дублировать запросы для одинаковых заголовков."""
    if not text or not text.strip():
        return None
    text = text.strip()[:480]  # у бесплатного API есть лимит на длину строки
    if text in _TRANSLATE_CACHE:
        return _TRANSLATE_CACHE[text]
    try:
        r = requests.get(
            "https://api.mymemory.translated.net/get",
            params={"q": text, "langpair": "en|ru"},
            timeout=timeout,
        )
        r.raise_for_status()
        data = r.json()
        translated = (data.get("responseData") or {}).get("translatedText")
        if translated and translated.strip() and translated.strip().lower() != text.lower():
            _TRANSLATE_CACHE[text] = translated.strip()
            return translated.strip()
    except Exception:
        pass
    _TRANSLATE_CACHE[text] = None
    return None

def http_get(url, **kwargs):
    """requests.get с ретраями и экспоненциальным backoff, форсирует utf-8."""
    kwargs.setdefault("headers", H)
    kwargs.setdefault("timeout", 30)
    last_exc = None
    for attempt in range(1, RETRIES + 1):
        try:
            r = requests.get(url, **kwargs)
            r.raise_for_status()
            if not r.encoding or r.encoding.lower() == "iso-8859-1":
                r.encoding = r.apparent_encoding or "utf-8"
            return r
        except Exception as e:
            last_exc = e
            if attempt < RETRIES:
                time.sleep(RETRY_BACKOFF * attempt)
    raise last_exc

# Месяцы на русском и английском — нужны, чтобы вытаскивать даты из текста
# на сайтах, где нет структурированной разметки дат (energyprojects.tj, EU Delegation, UN).
_MONTHS = {
    "january": "01", "february": "02", "march": "03", "april": "04", "may": "05", "june": "06",
    "july": "07", "august": "08", "september": "09", "october": "10", "november": "11", "december": "12",
    "января": "01", "февраля": "02", "марта": "03", "апреля": "04", "мая": "05", "июня": "06",
    "июля": "07", "августа": "08", "сентября": "09", "октября": "10", "ноября": "11", "декабря": "12",
    "январь": "01", "февраль": "02", "март": "03", "апрель": "04", "май": "05", "июнь": "06",
    "июль": "07", "август": "08", "сентябрь": "09", "октябрь": "10", "ноябрь": "11", "декабрь": "12",
}
_MONTH_DATE_RE = re.compile(
    r"(\d{1,2})\s+(" + "|".join(_MONTHS.keys()) + r")\s+(\d{4})|"
    r"(" + "|".join(_MONTHS.keys()) + r")\s+(\d{1,2}),?\s+(\d{4})",
    re.IGNORECASE,
)

def extract_date_from_text(text):
    """Ищет дату вида '16 October 2024' / 'October 16, 2024' / '11.02.2026' в свободном тексте."""
    if not text:
        return ""
    m = _MONTH_DATE_RE.search(text)
    if m:
        if m.group(2):  # "DD Month YYYY"
            d, mon, y = m.group(1), m.group(2).lower(), m.group(3)
        else:  # "Month DD, YYYY"
            mon, d, y = m.group(4).lower(), m.group(5), m.group(6)
        mm = _MONTHS.get(mon)
        if mm:
            return f"{y}-{mm}-{int(d):02d}"
    m2 = re.search(r"(\d{1,2})[./](\d{1,2})[./](\d{4})", text)
    if m2:
        return parse_d(m2.group(0))
    m3 = re.search(r"(\d{1,2})-(\d{1,2})-(\d{4})", text)  # mintrans.tj: "08-07-2026" (DD-MM-YYYY)
    if m3:
        d, mo, y = m3.groups()
        return f"{y}-{mo.zfill(2)}-{d.zfill(2)}"
    return ""

# ---------------------------------------------------------------------------
# ИСТОЧНИКИ
# ---------------------------------------------------------------------------

def _report(name, count, ok=True):
    SOURCE_SUMMARY[name] = {"count": count, "ok": ok}
    print(f"  {name}: {count}" + ("" if ok else "  ⚠ ЗАВЕРШЁН С ОШИБКОЙ"))

def _diag(name, r=None, soup=None, note=""):
    """Диагностика при 0 записей: код ответа, размер, начало текста/тайтла страницы."""
    try:
        if r is not None:
            print(f"  [DIAG {name}] status={r.status_code} len={len(r.text)} url={r.url}")
            print(f"  [DIAG {name}] head300={r.text[:300]!r}")
        if soup is not None:
            t = soup.find("title")
            print(f"  [DIAG {name}] title={t.get_text(strip=True) if t else None!r}")
        if note:
            print(f"  [DIAG {name}] note={note}")
    except Exception as e:
        print(f"  [DIAG {name}] diag_err={e}")

def f_wb():
    print("[1/11] World Bank...")
    out = []; seen = set(); off = 0
    ok = True
    for _ in range(50):
        try:
            r = http_get(
                "https://search.worldbank.org/api/v2/procnotices",
                params={"format": "json", "qterm": "Tajikistan", "rows": 100, "os": off},
                timeout=30,
            )
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
                if not in_win(parse_d(n.get("noticedate", ""))):
                    continue
                notice_type = str(n.get("notice_type_name", "") or "")
                if "award" in notice_type.lower():
                    continue
                    title_en = n.get("project_name", "")
                out.append(norm({
                    "source": "World Bank", "tender_id": nid, "title_en": title_en,
                    "title_original": title_en, "donor": "World Bank (IDA/IBRD)",
                    "funding_type": "Loan/Credit/Grant", "country": "Tajikistan",
                    "organization": n.get("contact_organization", ""),
                    "category": "",  # категория WB API (procurement_group) слишком «сырая» —
                                      # доверяем автокатегоризации по названию/описанию (см. norm())
                    "submission_deadline": (str(n.get("submission_deadline_date", ""))[:10] + " " + str(n.get("submission_deadline_time", ""))).strip(),
                    "publication_date": parse_d(n.get("noticedate", "")),
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
            time.sleep(0.3)
        except Exception as e:
            print(f"  err: {e}")
            ok = False
            break
    _report("World Bank", len(out), ok)
    return out

def f_undp():
    print("[2/11] UNDP...")
    out = []; ok = True
    try:
        r = http_get(
            "https://procurement-notices.undp.org/search.cfm",
            params={"displayed_record": 1000, "start": 0}, timeout=60,
        )
        soup = BeautifulSoup(r.text, "lxml")
        for row in soup.find_all("a", class_=re.compile(r"vacanciesTable.*row", re.I)):
            text = row.get_text(" ", strip=True)
            if "TAJIK" not in text.upper():
                continue
            href = row.get("href", "")
            if href and not href.startswith("http"):
                href = f"https://procurement-notices.undp.org/{href}"
            tm = re.search(r"Title\s*(.+?)\s*Ref No", text); title = tm.group(1).strip() if tm else ""
            rm = re.search(r"Ref No\s*(\S+)", text); ref = rm.group(1) if rm else ""
            pm = re.search(r"Posted\s*(\d{1,2}-\w+-\d+)", text); posted = parse_d(pm.group(1)) if pm else ""
            dm = re.search(r"Deadline\s*(.+?)(?:\s*Posted|$)", text); dl = dm.group(1).strip() if dm else ""
            tym = re.search(r"Procurement Method\s*(.+?)(?:\s*UNDP|$)", text) or re.search(r"Type\s*(.+?)(?:\s*UNDP|$)", text)
            ptype = tym.group(1).strip() if tym else ""
            # ref может отсутствовать/повторяться — на всякий случай подстрахуемся хэшем от href+title
            tender_id = ref or make_stable_id(href, title)
            out.append(norm({
                "source": "UNDP", "tender_id": tender_id, "title_en": title, "title_original": title,
                "donor": "UNDP", "funding_type": "Grant", "country": "Tajikistan",
                "organization": "UNDP Tajikistan", "publication_date": posted,
                "submission_deadline": dl, "procurement_method": ptype,
                "source_url": href or f"https://procurement-notices.undp.org/#{tender_id}",
                "description": text[:1500], "language": "English",
            }))
    except Exception as e:
        print(f"  err: {e}"); ok = False
    if len(out) == 0:
        _diag("UNDP", r=locals().get("r"), soup=locals().get("soup"))
    _report("UNDP", len(out), ok)
    return out

def f_inv():
    print("[3/11] investcom.tj...")
    out = []; ok = True
    try:
        r = http_get("https://investcom.tj/tenders.html", timeout=30)
        soup = BeautifulSoup(r.text, "lxml")
        table = soup.find("table")
        if not table:
            _report("investcom.tj", 0, True)
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
            if doc_url and not doc_url.startswith("http"):
                doc_url = f"https://investcom.tj/{doc_url.lstrip('/')}"
            if not in_win(dl_raw):
                continue
            # ФИКС P0: раньше source_url был одинаковым (страница списка) для ВСЕХ строк,
            # из-за чего дедуп по (source, tender_id, source_url) схлопывал 16-18 разных
            # тендеров в 1. Теперь используем ссылку на документ, если она есть, а если нет —
            # стабильный хэш от содержимого строки, чтобы каждая запись была уникальной.
            tender_id = make_stable_id(org, proj, subj, dl_raw)
            source_url = doc_url or f"https://investcom.tj/tenders.html#{tender_id}"
            out.append(norm({
                "source": "investcom.tj", "tender_id": tender_id,
                "title_tj": subj, "title_ru": subj, "title_original": subj,
                "donor": "Госкоминвест РТ", "funding_type": "Grant/State",
                "country": "Tajikistan", "organization": org, "description": proj,
                "submission_deadline": dl_raw, "documents_url": doc_url,
                "source_url": source_url, "language": "Tajik/Russian",
            }))
    except Exception as e:
        print(f"  err: {e}"); ok = False
    _report("investcom.tj", len(out), ok)
    return out

def f_aed():
    print("[4/11] aedpmu.tj...")
    out = []; ok = True
    try:
        for pg in range(1, 6):
            url = "https://aedpmu.tj/en/category/obyavlenie/tendery/" + (f"page/{pg}/" if pg > 1 else "")
            try:
                r = http_get(url, timeout=30)
            except Exception:
                break
            soup = BeautifulSoup(r.text, "lxml")
            arts = soup.find_all("article")
            if not arts:
                break
            for art in arts:
                te = art.find("time"); pd_str = te.get("datetime", "") if te else ""
                if not pd_str:
                    dm = re.search(r"(\d{1,2}\s+\w+\s+\d{4})", art.get_text())
                    pd_str = dm.group(1) if dm else ""
                if not in_win(pd_str):
                    continue
                te2 = art.find(["h2", "h3", "h1"])
                if not te2:
                    continue
                lnk = te2.find("a", href=True) or art.find("a", href=True)
                if not lnk:
                    continue
                title = te2.get_text(strip=True); url_full = lnk["href"]
                m = re.search(r"/(\d+)/?$", url_full)
                tender_id = m.group(1) if m else make_stable_id(url_full, title)
                out.append(norm({
                    "source": "aedpmu.tj", "tender_id": tender_id, "title_en": title,
                    "title_original": title, "donor": "World Bank (SRASP)", "funding_type": "Grant",
                    "country": "Tajikistan", "organization": "AED PMU",
                    "category": "",  # прежде было жёстко "IT/Agriculture" — теперь категоризируем по тексту
                    "publication_date": parse_d(pd_str), "source_url": url_full,
                    "language": "English", "eligibility": "World Bank Procurement Regulations",
                }))
    except Exception as e:
        print(f"  err: {e}"); ok = False
    _report("aedpmu.tj", len(out), ok)
    return out

def f_tj():
    print("[5/11] tenders.tj...")
    out = []; ok = True
    base = "https://www.tenders.tj"
    try:
        for pg in range(1, 4):
            try:
                r = http_get(f"{base}/index.php?do=poisk&page={pg}", timeout=30)
            except Exception:
                break
            soup = BeautifulSoup(r.text, "lxml")
            for it in soup.find_all("a", href=re.compile(r"/procurement/\d+\.html")):
                href = it.get("href", ""); title = it.get_text(" ", strip=True)
                if not title or len(title) < 5:
                    continue
                if not href.startswith("http"):
                    href = f"{base}{href}"
                m = re.search(r"/procurement/(\d+)\.html", href)
                out.append(norm({
                    "source": "tenders.tj", "tender_id": m.group(1) if m else make_stable_id(href, title),
                    "title_ru": title, "title_tj": title, "title_original": title,
                    "donor": "Агрегатор госзакупок РТ", "funding_type": "State/Donor",
                    "country": "Tajikistan", "source_url": href, "language": "Russian/Tajik",
                }))
            time.sleep(0.3)
        for rec in out[:30]:
            try:
                d = http_get(rec["source_url"], timeout=15)
                s = BeautifulSoup(d.text, "lxml"); txt = s.get_text("\n", strip=True)
                m1 = re.search(r"Дата публикации:\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})", txt)
                if m1: rec["publication_date"] = parse_d(m1.group(1))
                m2 = re.search(r"Крайний срок / Deadline:\s*(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})", txt)
                if m2: rec["submission_deadline"] = m2.group(1)
                m3 = re.search(r"Организатор:\s*([^\n]+)", txt)
                if m3: rec["organization"] = m3.group(1).strip()
                m4 = re.search(r"Отрасль:\s*([^\n]+)", txt)
                if m4: rec["category_raw_tj"] = m4.group(1).strip()
                m5 = re.search(r"Контактный E-mail:\s*([^\n]+)", txt)
                if m5: rec["contact_email"] = m5.group(1).strip()
                m6 = re.search(r"Контактный телефон:\s*([^\n]+)", txt)
                if m6: rec["contact_phone"] = m6.group(1).strip()
                m7 = re.search(r"Область реализации проекта:\s*([^\n]+)", txt)
                if m7: rec["region"] = m7.group(1).strip()
                m8 = re.search(r"Описание:\s*([^\n]+)", txt)
                if m8: rec["description"] = m8.group(1).strip()
                # если категория ещё не определена осмысленно — пересчитываем с учётом деталей страницы
                if rec.get("category") == "Other Services":
                    rec["category"] = categorize(rec.get("title_ru", ""), rec.get("description", ""))
            except Exception:
                pass
            time.sleep(0.2)
    except Exception as e:
        print(f"  err: {e}"); ok = False
    _report("tenders.tj", len(out), ok)
    return out

async def f_eproc():
    print("[6/11] eprocurement.gov.tj...")
    out = []; ok = True
    if not HAS_PW:
        _report("eprocurement.gov.tj", 0, False)
        return out
    try:
        async with async_playwright() as p:
            b = await p.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
            ctx = await b.new_context(user_agent=UA)
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
            html = await page.content(); soup = BeautifulSoup(html, "lxml")
            for t in soup.find_all("table"):
                rows = t.find_all("tr")
                if len(rows) < 5:
                    continue
                header_cells = rows[0].find_all(["th", "td"])
                hdr_texts = [c.get_text(" ", strip=True).lower() for c in header_cells]
                hdr_joined = " ".join(hdr_texts)
                if "объявления" not in hdr_joined and "название" not in hdr_joined:
                    continue

                # ФИКС: раньше индексы колонок (org=1, title=3, deadline=7) были жёстко зашиты.
                # Теперь пытаемся определить их по заголовку таблицы; если не получилось —
                # откатываемся на старые индексы как fallback, но это уже осознанный запасной путь.
                def find_col(keywords, default):
                    for i, htxt in enumerate(hdr_texts):
                        if any(k in htxt for k in keywords):
                            return i
                    return default

                idx_org = find_col(["заказчик", "организатор"], 1)
                idx_title = find_col(["название", "наименование", "предмет"], 3)
                idx_deadline = find_col(["окончания", "дата окончания", "срок"], 7)

                for row in rows[1:]:
                    cells = row.find_all("td")
                    if len(cells) < 3:
                        continue
                    link = row.find("a", href=True); href = link.get("href", "") if link else ""
                    org = cells[idx_org].get_text(strip=True) if idx_org < len(cells) else ""
                    title = cells[idx_title].get_text(strip=True) if idx_title < len(cells) else ""
                    de = cells[idx_deadline].get_text(strip=True) if idx_deadline < len(cells) else ""
                    if href and not href.startswith("http"):
                        href = f"https://eprocurement.gov.tj{href}"
                    # ФИКС P0 (вторично): если ссылки нет, раньше все такие записи получали
                    # одинаковый source_url (адрес поиска) и схлопывались в дедупе.
                    tender_id = make_stable_id(org, title, de)
                    source_url = href or f"https://eprocurement.gov.tj/ru/searchanno#{tender_id}"
                    out.append(norm({
                        "source": "eprocurement.gov.tj", "tender_id": tender_id,
                        "title_ru": title, "title_tj": title, "title_original": title,
                        "donor": "Госзакупки РТ", "funding_type": "State Budget",
                        "country": "Tajikistan", "organization": org,
                        "submission_deadline": de, "source_url": source_url,
                        "language": "Russian/Tajik",
                    }))
                break
            await b.close()
    except Exception as e:
        print(f"  err: {e}"); ok = False
    if len(out) == 0:
        print(f"  [DIAG eprocurement.gov.tj] HAS_PW={HAS_PW}")
        _diag("eprocurement.gov.tj", soup=locals().get("soup"))
    _report("eprocurement.gov.tj", len(out), ok)
    return out

# ---------------------------------------------------------------------------
# НОВЫЕ ИСТОЧНИКИ (добавлены по запросу пользователя, на основе присланного
# списка мониторинга + собственного анализа рынка). Каждый — best-effort:
# структура страниц угадана по snapshot'ам без возможности живого теста,
# поэтому селекторы устойчивые (несколько fallback-стратегий), а не жёсткие.
# ---------------------------------------------------------------------------

def f_energyprojects():
    """energyprojects.tj — Группа реализации энергетических проектов при Президенте РТ
    (Рогунская ГЭС). Финансируется Всемирным банком/AIIB/IsDB, есть тендеры на IT/оборудование.
    ВАЖНО: у сайта две версии — старая Joomla (index.php/ru/tendery) и текущая Next.js
    (/en/procurement, /ru/procurement). RU-версия отдаёт заметно меньше карточек, чем EN —
    используем EN как основной источник и переводим заголовки через translate_en_ru()."""
    print("[7/11] energyprojects.tj...")
    out = []; ok = True
    base = "https://energyprojects.tj"
    try:
        r = http_get(f"{base}/en/procurement", timeout=30)
        soup = BeautifulSoup(r.text, "lxml")
        seen_href = set()
        for a in soup.find_all("a", href=re.compile(r"/procurement/[a-z0-9\-]+-\d+/?$", re.I)):
            href = a.get("href", "")
            if href in seen_href:
                continue
            seen_href.add(href)
            full_url = href if href.startswith("http") else f"{base}{href}"
            title = a.get_text(strip=True)
            container = a.find_parent(["div", "article", "li"]) or a.parent
            if not title:
                h = container.find(["h2", "h3"]) if container else None
                title = h.get_text(strip=True) if h else ""
            if not title or len(title) < 5:
                continue
            block_text = container.get_text(" ", strip=True) if container else ""
            pub_date = extract_date_from_text(block_text)
            if not in_win(pub_date):
                continue
            m = re.search(r"-(\d+)/?$", href)
            tender_id = m.group(1) if m else make_stable_id(href, title)
            out.append(norm({
                "source": "energyprojects.tj", "tender_id": tender_id,
                "title_en": title, "title_original": title,
                "donor": "World Bank (IDA/IBRD)", "funding_type": "Loan/Credit/Grant",
                "country": "Tajikistan", "organization": "Rogun HPP PMG",
                "publication_date": pub_date, "source_url": full_url,
                "description": block_text[:1000], "language": "English",
            }))
    except Exception as e:
        print(f"  err: {e}"); ok = False
    if len(out) == 0:
        _diag("energyprojects.tj", r=locals().get("r"), soup=locals().get("soup"))
    _report("energyprojects.tj", len(out), ok)
    return out

def f_mewr():
    """mewr.tj — Министерство энергетики и водных ресурсов РТ. WordPress,
    категория 'Объявления и Вакансии' (cat=1) смешивает тендеры и вакансии.
    ВАЖНО: не полагаемся на конкретную тему/структуру <article> — ищем напрямую
    ссылки вида ?p=NNNN (стабильный паттерн WordPress-пермалинков независимо от темы)."""
    print("[8/11] mewr.tj...")
    out = []; ok = True
    base = "https://www.mewr.tj"
    try:
        for page in range(1, 4):
            url = f"{base}/?cat=1" + (f"&paged={page}" if page > 1 else "")
            try:
                r = http_get(url, timeout=30)
            except Exception:
                break
            soup = BeautifulSoup(r.text, "lxml")
            links = soup.find_all("a", href=re.compile(r"[?&]p=\d+"))
            if not links:
                break
            seen_ids = set()
            found_any = False
            for lnk in links:
                url_full = lnk.get("href", "")
                m = re.search(r"[?&]p=(\d+)", url_full)
                if not m:
                    continue
                post_id = m.group(1)
                if post_id in seen_ids:
                    continue
                title = lnk.get_text(strip=True)
                if not title or len(title) < 5:
                    continue
                seen_ids.add(post_id)
                found_any = True
                low = title.lower()
                if not any(k in low for k in ["закуп", "тендер", "заявлен", "конкурс", "заинтересован", "предложен"]):
                    continue
                container = lnk.find_parent(["article", "div", "li"]) or lnk
                te = container.find("time")
                pub_date = ""
                if te and te.get("datetime"):
                    pub_date = parse_d(te["datetime"])
                if not pub_date:
                    pub_date = extract_date_from_text(container.get_text(" ", strip=True))
                if not in_win(pub_date):
                    continue
                out.append(norm({
                    "source": "mewr.tj", "tender_id": post_id,
                    "title_ru": title, "title_original": title,
                    "donor": "World Bank (IDA/IBRD)", "funding_type": "Loan/Credit/Grant",
                    "country": "Tajikistan", "organization": "Министерство энергетики и водных ресурсов РТ",
                    "publication_date": pub_date, "source_url": url_full, "language": "Russian",
                }))
            if not found_any:
                break
            time.sleep(0.3)
    except Exception as e:
        print(f"  err: {e}"); ok = False
    if len(out) == 0:
        _diag("mewr.tj", r=locals().get("r"), soup=locals().get("soup"))
    _report("mewr.tj", len(out), ok)
    return out

def f_mintrans():
    """mintrans.tj/tender-page — Министерство транспорта РТ. Одна длинная страница
    (Laravel), записи в формате 'Дата: DD-MM-YYYY' + заголовок + ссылка на PDF."""
    print("[9/11] mintrans.tj...")
    out = []; ok = True
    try:
        r = http_get("https://www.mintrans.tj/tender-page", timeout=30)
        soup = BeautifulSoup(r.text, "lxml")
        text_blocks = soup.get_text("\n", strip=True).split("\n")
        # Ищем ссылки на PDF (каждая запись оканчивается ссылкой "Показать" на файл в /storage/tender/)
        pdf_links = soup.find_all("a", href=re.compile(r"/storage/tender/.*\.pdf$", re.I))
        for lnk in pdf_links:
            container = lnk.find_parent(["div", "article"]) or lnk
            # Поднимаемся на пару уровней, чтобы захватить дату+заголовок+описание, которые
            # на этом сайте лежат рядом с картинкой-ссылкой, а не внутри одного маленького блока
            block = container
            for _ in range(3):
                if block.parent:
                    block = block.parent
            block_text = block.get_text(" ", strip=True)
            dm = re.search(r"Дата:\s*(\d{2}-\d{2}-\d{4})", block_text)
            pub_date = ""
            if dm:
                d, mo, y = dm.group(1).split("-")
                pub_date = f"{y}-{mo}-{d}"
            if not in_win(pub_date):
                continue
            h = block.find(["h3", "h4", "h5"])
            title = h.get_text(strip=True) if h else ""
            if not title or len(title) < 5:
                continue
            file_url = lnk["href"]
            if not file_url.startswith("http"):
                file_url = f"https://www.mintrans.tj{file_url}"
            tender_id = make_stable_id(title, pub_date, file_url)
            out.append(norm({
                "source": "mintrans.tj", "tender_id": tender_id,
                "title_ru": title, "title_original": title,
                "donor": "Различные (WB/ADB/AIIB/IsDB)", "funding_type": "Loan/Credit/Grant",
                "country": "Tajikistan", "organization": "Министерство транспорта РТ",
                "publication_date": pub_date, "documents_url": file_url,
                "source_url": "https://www.mintrans.tj/tender-page#" + tender_id,
                "language": "Russian",
            }))
    except Exception as e:
        print(f"  err: {e}"); ok = False
    _report("mintrans.tj", len(out), ok)
    return out

def f_un_tj():
    """tajikistan.un.org — общая лента вакансий+тендеров агентств ООН и партнёрских
    НКО (IFRC, Mission East, AKDN и др.) в Таджикистане. Фильтруем по ключевым словам."""
    print("[10/11] tajikistan.un.org...")
    out = []; ok = True
    base = "https://tajikistan.un.org"
    # Осознанно НЕ включаем голое "procurement" — оно ложно совпадает с вакансиями
    # вида "Procurement Specialist", "Procurement Officer" и т.п.
    TENDER_KEYWORDS = [
        "tender", "rfp", "rfq", " eoi", "expression of interest",
        "invitation to bid", "invitation for bid", "request for proposal",
        "request for quotation", "call for tenders", "supply of",
        "procurement notice", "procurement of", "invitation to tender",
    ]
    try:
        for page in range(0, 4):
            url = f"{base}/en/jobs" + (f"?page={page}" if page > 0 else "")
            try:
                r = http_get(url, timeout=30)
            except Exception:
                break
            soup = BeautifulSoup(r.text, "lxml")
            found_any = False
            for a in soup.find_all("a", href=re.compile(r"^/en/\d+-[a-z0-9-]+", re.I)):
                title = a.get_text(strip=True)
                if not title or len(title) < 8:
                    continue
                found_any = True
                low = title.lower()
                if not any(k in low for k in TENDER_KEYWORDS):
                    continue
                href = a["href"]
                url_full = href if href.startswith("http") else f"{base}{href}"
                m = re.search(r"^/en/(\d+)-", href)
                tender_id = m.group(1) if m else make_stable_id(url_full, title)
                out.append(norm({
                    "source": "tajikistan.un.org", "tender_id": tender_id,
                    "title_en": title, "title_original": title,
                    "donor": "UN Agencies / Partners", "funding_type": "Grant",
                    "country": "Tajikistan", "source_url": url_full,
                    "language": "English",
                }))
            if not found_any:
                break
            time.sleep(0.3)
    except Exception as e:
        print(f"  err: {e}"); ok = False
    if len(out) == 0:
        _diag("tajikistan.un.org", r=locals().get("r"), soup=locals().get("soup"))
    _report("tajikistan.un.org", len(out), ok)
    return out

def f_eeas():
    """eeas.europa.eu — Представительство Евросоюза в Таджикистане.
    ВАЖНО: тендеры лежат НЕ на общей странице делегации, а в отдельном разделе
    /eeas/tenders_en с фильтром по стране (tender_site=Tajikistan) — там уже
    предотфильтрованный список, доп. keyword-фильтр не нужен."""
    print("[11/11] EU Delegation Tajikistan...")
    out = []; ok = True
    base = "https://www.eeas.europa.eu"
    try:
        for page in range(0, 3):
            url = f"{base}/eeas/tenders_en?f%5B0%5D=tender_site%3ATajikistan" + (f"&page={page}" if page > 0 else "")
            try:
                r = http_get(url, timeout=30)
            except Exception:
                break
            soup = BeautifulSoup(r.text, "lxml")
            links = soup.find_all("a", href=re.compile(r"/delegations/tajikistan/[a-z0-9\-%]+_en", re.I))
            if not links:
                break
            found_any = False
            for a in links:
                title = a.get_text(strip=True)
                if not title or len(title) < 8:
                    continue
                found_any = True
                href = a["href"]
                url_full = href if href.startswith("http") else f"{base}{href}"
                container = a.find_parent(["div", "article", "li"]) or a
                block_text = container.get_text(" ", strip=True)
                pub_date = extract_date_from_text(block_text)
                if not in_win(pub_date):
                    continue
                tender_id = make_stable_id(url_full, title)
                out.append(norm({
                    "source": "eeas.europa.eu", "tender_id": tender_id,
                    "title_en": title, "title_original": title,
                    "donor": "European Union", "funding_type": "Grant",
                    "country": "Tajikistan", "organization": "EU Delegation to Tajikistan",
                    "publication_date": pub_date, "source_url": url_full,
                    "description": block_text[:500], "language": "English",
                }))
            if not found_any:
                break
            time.sleep(0.3)
    except Exception as e:
        print(f"  err: {e}"); ok = False
    if len(out) == 0:
        _diag("eeas.europa.eu", r=locals().get("r"), soup=locals().get("soup"))
    _report("eeas.europa.eu", len(out), ok)
    return out

# ---------------------------------------------------------------------------
# СБОРКА ВЫХОДНЫХ ФАЙЛОВ
# ---------------------------------------------------------------------------

def safe_json_embed(obj):
    """json.dumps + экранирование '<' — иначе если в спарсенном тексте случайно
    встретится подстрока '</script>', она преждевременно оборвёт тег <script>
    в HTML-шаблоне и сломает страницу."""
    return json.dumps(obj, ensure_ascii=False, default=str).replace("<", "\\u003c")

def b_excel(records):
    print("Excel...")
    df = pd.DataFrame(records)
    cols = ["source", "tender_id", "title_en", "title_ru", "title_tj", "title_original",
            "donor", "funding_type", "country", "region", "organization", "category",
            "publication_date", "submission_deadline", "procurement_method", "eligibility",
            "description", "documents_url", "contact_name", "contact_email", "contact_phone",
            "source_url", "language", "scraped_at"]
    df = df[[c for c in cols if c in df.columns]]
    df.to_csv(OUT / f"tenders_tj_{TODAY.isoformat()}.csv", index=False, encoding="utf-8-sig")
    df.to_excel(OUT / f"tenders_tj_{TODAY.isoformat()}.xlsx", index=False, engine="openpyxl")
    print(f"  -> {len(df)}")
    return df

def b_cat(df):
    print("Catalog...")
    df = df.copy()

    # Best-effort перевод заголовков, которые остались только на английском
    # (после title_ru() ниже используется как fallback-источник текста).
    def translate_row_title(row):
        best = title_ru(row)
        # Если результат состоит преимущественно из латиницы — пробуем перевести
        if best and best != '(без названия)' and not re.search(r'[а-яё]', best, re.I):
            tr = translate_en_ru(best)
            if tr:
                return tr
        return best

    df['title_main'] = df.apply(translate_row_title, axis=1)
    df['cat_ru'] = df['category'].map(CAT_RU).fillna(df['category'])
    df['donor_ru'] = df['donor'].map(DONOR_RU).fillna(df['donor'])

    # ФИКС: раньше даты в submission_deadline (разные форматы от разных источников)
    # шли напрямую в pd.to_datetime с автоопределением формата — риск перепутать
    # день/месяц. Теперь сначала нормализуем через parse_d() (dayfirst-aware).
    df['dl_norm'] = df['submission_deadline'].apply(parse_d)
    df['dl_dt'] = pd.to_datetime(df['dl_norm'], errors='coerce')

    def st(row):
        dl = row['dl_dt']; src = row['source']
        if pd.isna(dl):
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
    df['status'] = df.apply(st, axis=1)

    def pr(row):
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
    df['priority'] = df.apply(pr, axis=1)
    df = df.sort_values(['priority', 'publication_date'], ascending=[True, False])

    recs = []
    for _, r in df.iterrows():
        recs.append({
            'priority': int(r['priority']), 'status': str(r['status']), 'source': str(r['source']),
            'donor': str(r['donor_ru']), 'category': str(r['cat_ru']), 'title': str(r['title_main'])[:300],
            'title_en': str(r.get('title_en', ''))[:300] if pd.notna(r.get('title_en', '')) else '',
            'method': str(r.get('procurement_method', '')) if pd.notna(r.get('procurement_method', '')) else '—',
            'organization': str(r.get('organization', '')) if pd.notna(r.get('organization', '')) else '—',
            'publication_date': str(r['publication_date']) if pd.notna(r['publication_date']) else '—',
            'submission_deadline': str(r['submission_deadline']) if pd.notna(r['submission_deadline']) else '—',
            'description': str(r.get('description', '')) if pd.notna(r.get('description', '')) else '',
            'source_url': str(r.get('source_url', '')) if pd.notna(r.get('source_url', '')) else '#',
        })
    dj = safe_json_embed(recs)
    cat_html_out = CAT_HTML.replace("__DATA__", dj)
    with open(OUT / f"catalog_{TODAY.isoformat()}.html", "w", encoding="utf-8") as f:
        f.write(cat_html_out)
    # Стабильная копия без даты в имени — "последняя версия", чтобы на неё можно
    # было дать одну постоянную ссылку, не меняющуюся при каждом запуске.
    with open(OUT / "catalog.html", "w", encoding="utf-8") as f:
        f.write(cat_html_out)
    print(f"  -> catalog_{TODAY.isoformat()}.html (+ catalog.html)")

def b_dash(df):
    print("Dashboard...")
    df = df.copy()
    df['pub_m'] = pd.to_datetime(df['publication_date'], errors='coerce').dt.to_period('M').astype(str)
    df.loc[df['pub_m'] == 'NaT', 'pub_m'] = '—'
    s = {
        'total': int(len(df)),
        'by_source': df.groupby('source').size().to_dict(),
        'by_category': df.groupby('category').size().to_dict(),
        'by_donor': df.groupby('donor').size().to_dict(),
        'by_pub_month': df[df['pub_m'] != '—'].groupby('pub_m').size().to_dict(),
    }
    dj = safe_json_embed(s)
    dash_html_out = DASH_HTML.replace("__DATA__", dj)
    with open(OUT / f"dashboard_{TODAY.isoformat()}.html", "w", encoding="utf-8") as f:
        f.write(dash_html_out)
    # Стабильная копия без даты — "последняя версия"
    with open(OUT / "dashboard.html", "w", encoding="utf-8") as f:
        f.write(dash_html_out)
    print(f"  -> dashboard_{TODAY.isoformat()}.html (+ dashboard.html)")

async def main():
    print(f"\n=== Run: {NOW.isoformat()} ===\n")
    all_r = []
    all_r.extend(f_wb())
    all_r.extend(f_undp())
    all_r.extend(f_inv())
    all_r.extend(f_aed())
    all_r.extend(f_tj())
    all_r.extend(await f_eproc())
    all_r.extend(f_energyprojects())
    all_r.extend(f_mewr())
    all_r.extend(f_mintrans())
    all_r.extend(f_un_tj())
    all_r.extend(f_eeas())

    seen = set(); uniq = []
    for r in all_r:
        k = (r["source"], r.get("tender_id", ""), r.get("source_url", ""))
        if k in seen:
            continue
        seen.add(k); uniq.append(r)
    print(f"\n=== Unique: {len(uniq)} (из {len(all_r)} собранных) ===\n")

    if not uniq:
        print("⚠ ВНИМАНИЕ: не собрано ни одной записи ни из одного источника. Файлы не создаются.")
        return

    df = b_excel(uniq)
    b_cat(df)
    b_dash(df)

    print("\n=== Сводка по источникам ===")
    for name, info in SOURCE_SUMMARY.items():
        flag = "OK" if info["ok"] else "ОШИБКА"
        warn = "  <-- источник вернул 0 записей, возможно сломалась вёрстка/доступ" if info["count"] == 0 else ""
        print(f"  {name}: {info['count']} [{flag}]{warn}")

    print(f"\nDone! Files: {OUT}/")

# ---------------------------------------------------------------------------
# HTML-ШАБЛОНЫ
# ---------------------------------------------------------------------------

CAT_HTML = '''<!DOCTYPE html><html lang="ru"><head><meta charset="UTF-8"><title>Каталог IT-тендеров</title>
<style>*{box-sizing:border-box;margin:0;padding:0}body{font-family:sans-serif;background:#0f1419;color:#e6edf3;padding:20px}
.h{text-align:center;margin-bottom:20px}.h h1{font-size:26px;background:linear-gradient(135deg,#58a6ff,#a371f7);-webkit-background-clip:text;-webkit-text-fill-color:transparent;display:inline-block}
.s{color:#8b949e;font-size:13px;margin-top:6px}
.ks{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:12px;max-width:1600px;margin:0 auto 20px}
.k{background:#161b22;border:1px solid #30363d;border-radius:10px;padding:14px 16px}.k .n{font-size:28px;font-weight:800}.k .l{font-size:11px;color:#8b949e;text-transform:uppercase}.k .n.h{color:#f85149}
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
em{text-align:center;padding:60px 20px;color:#8b949e;display:block}
</style></head><body>
<div class="h"><h1>Каталог IT-тендеров — Таджикистан</h1><div class="s">Автообновление каждые 6 часов</div></div>
<div class="ks">
  <div class="k"><div class="l">Срочно</div><div class="n h" id="k1">0</div></div>
  <div class="k"><div class="l">Скоро</div><div class="n" id="k2">0</div></div>
  <div class="k"><div class="l">IT-разработка</div><div class="n" id="k3">0</div></div>
  <div class="k"><div class="l">Оборудование</div><div class="n" id="k4">0</div></div>
  <div class="k"><div class="l">Всего</div><div class="n" id="k5">0</div></div>
</div>
<div class="fs">
  <div><label>Поиск</label><input type="text" id="q"></div>
  <div><label>Категория</label><select id="fc"><option value="">Все</option></select></div>
  <div><label>Донор</label><select id="fd"><option value="">Все</option></select></div>
  <div><label>Актуальность</label><select id="fs2"><option value="">Все</option><option value="Срочно">Срочно</option><option value="Скоро">Скоро</option><option value="Активен">Активен</option></select></div>
  <div><label>Сортировка</label><select id="fr"><option value="priority">По приоритету</option><option value="deadline">По дедлайну</option><option value="date">По дате</option></select></div>
</div>
<div class="rb">Показано: <strong id="sh">0</strong> из <strong id="tot">0</strong></div>
<div class="cs" id="cs"></div>
<script>
const data = __DATA__;
const uniq = a => [...new Set(a)].sort();
const fill = (id, vs) => { const s = document.getElementById(id); vs.forEach(v => { if (v) { const o = document.createElement('option'); o.value = v; o.textContent = v; s.appendChild(o); } }); };
fill('fc', uniq(data.map(d => d.category)));
fill('fd', uniq(data.map(d => d.donor)));
const cls = s => { if (s.includes('Срочно')) return 'st-1'; if (s.includes('Скоро')) return 'st-2'; if (s.includes('Активен')) return 'st-3'; if (s.includes('Долгосрочный')) return 'st-4'; if (s.includes('Истёк')) return 'st-5'; return 'st-0'; };
const esc = s => String(s || '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function render() {
  const q = document.getElementById('q').value.toLowerCase();
  const cat = document.getElementById('fc').value;
  const don = document.getElementById('fd').value;
  const st = document.getElementById('fs2').value;
  const sr = document.getElementById('fr').value;
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
    return '<div class="c p' + (d.priority || 3) + '"><div class="bd"><div style="flex:1;min-width:0;"><div class="bg" style="margin-bottom:6px;"><span class="b s">' + esc(d.source) + '</span><span class="b d">' + esc(d.donor) + '</span><span class="b c">' + esc(d.category) + '</span></div></div><span class="st ' + cls(d.status) + '">' + esc(d.status) + '</span></div><div class="t">' + esc(d.title) + '</div>' + (tEn ? '<div class="te">EN: ' + esc(tEn) + '</div>' : '') + '<div class="o">' + esc(d.organization) + '</div><div class="m"><div class="mi"><span class="ml">Метод</span><span class="mv">' + esc(d.method) + '</span></div><div class="mi"><span class="ml">Опубликован</span><span class="mv">' + esc(d.publication_date) + '</span></div><div class="mi"><span class="ml">Дедлайн</span><span class="mv">' + esc(d.submission_deadline) + '</span></div></div><div class="a"><a class="ac" href="' + esc(d.source_url) + '" target="_blank">Открыть</a></div></div>';
  }).join('') : '<em>Ничего не найдено</em>';
  document.getElementById('sh').textContent = f.length;
}
function kpis() {
  document.getElementById('k1').textContent = data.filter(d => (d.status || '').includes('Срочно')).length;
  document.getElementById('k2').textContent = data.filter(d => (d.status || '').includes('Скоро')).length;
  const it = ['IT-разработка', 'Поставка IT-оборудования', 'Телеком/Сеть', 'Финтех/Цифровые платежи', 'Электронное правительство', 'Геоданные/Цифровое с/х'];
  const eq = ['Поставка IT-оборудования', 'Лабораторное оборудование', 'Электрооборудование', 'Техника/Спецтранспорт', 'Транспорт', 'Мебель', 'Телеком/Сеть'];
  document.getElementById('k3').textContent = data.filter(d => it.includes(d.category)).length;
  document.getElementById('k4').textContent = data.filter(d => eq.includes(d.category)).length;
  document.getElementById('k5').textContent = data.length;
  document.getElementById('tot').textContent = data.length;
}
['q', 'fc', 'fd', 'fs2', 'fr'].forEach(id => { const e = document.getElementById(id); e.addEventListener('input', render); e.addEventListener('change', render); });
kpis(); render();
</script></body></html>'''

DASH_HTML = '''<!DOCTYPE html><html lang="ru"><head><meta charset="UTF-8"><title>Дашборд</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>*{box-sizing:border-box;margin:0;padding:0}body{font-family:sans-serif;background:#0f1419;color:#e6edf3;padding:24px}
.h{text-align:center;margin-bottom:32px}.h h1{font-size:28px;background:linear-gradient(135deg,#58a6ff,#a371f7);-webkit-background-clip:text;-webkit-text-fill-color:transparent;display:inline-block}
.ks{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:16px;max-width:1400px;margin:0 auto 32px}
.k{background:#161b22;border:1px solid #30363d;border-radius:12px;padding:20px;position:relative;overflow:hidden}
.k::before{content:'';position:absolute;top:0;left:0;right:0;height:3px;background:linear-gradient(90deg,#58a6ff,#a371f7)}
.k .n{font-size:36px;font-weight:800;margin:4px 0}.k .l{font-size:13px;color:#8b949e;text-transform:uppercase}
.g{display:grid;grid-template-columns:repeat(auto-fit,minmax(420px,1fr));gap:20px;max-width:1400px;margin:0 auto}
.c{background:#161b22;border:1px solid #30363d;border-radius:12px;padding:20px}
.c h2{font-size:15px;font-weight:600;color:#c9d1d9;margin-bottom:16px;display:flex;align-items:center;gap:8px}
.c h2::before{content:'';width:4px;height:16px;background:#58a6ff;border-radius:2px}
.w{position:relative;height:280px}.w.t{height:360px}
</style></head><body>
<div class="h"><h1>Дашборд IT-тендеры — Таджикистан</h1></div>
<div class="ks">
  <div class="k"><div class="l">Всего</div><div class="n" id="k1">0</div></div>
  <div class="k"><div class="l">IT-разработка</div><div class="n" id="k2">0</div></div>
  <div class="k"><div class="l">Оборудование</div><div class="n" id="k3">0</div></div>
  <div class="k"><div class="l">Консалтинг</div><div class="n" id="k4">0</div></div>
</div>
<div class="g">
  <div class="c"><h2>По источникам</h2><div class="w"><canvas id="c1"></canvas></div></div>
  <div class="c"><h2>По донорам</h2><div class="w"><canvas id="c2"></canvas></div></div>
  <div class="c"><h2>По категориям</h2><div class="w t"><canvas id="c3"></canvas></div></div>
  <div class="c"><h2>Публикации по месяцам</h2><div class="w"><canvas id="c4"></canvas></div></div>
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
const itC = ['Software / IT Development','IT Equipment Supply','Telecom / Network','Fintech / Digital Payments','E-Government / E-Procurement','Geo-spatial / Digital Agriculture'];
const eqC = ['IT Equipment Supply','Lab Equipment','Power / Electrical','Machinery / Vehicles','Vehicles','Furniture','Telecom / Network'];
const csC = ['Consulting','Training / TA','Studies / Audit'];
document.getElementById('k2').textContent = Object.entries(data.by_category).filter(([k])=>itC.includes(k)).reduce((s,[,v])=>s+v,0);
document.getElementById('k3').textContent = Object.entries(data.by_category).filter(([k])=>eqC.includes(k)).reduce((s,[,v])=>s+v,0);
document.getElementById('k4').textContent = Object.entries(data.by_category).filter(([k])=>csC.includes(k)).reduce((s,[,v])=>s+v,0);
</script></body></html>'''

if __name__ == "__main__":
    asyncio.run(main())
