import html
import re
import unicodedata
import xml.etree.ElementTree as ET
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

import server


def _norm(value):
    value = unicodedata.normalize("NFD", str(value or "").lower().strip())
    return "".join(ch for ch in value if unicodedata.category(ch) != "Mn")


def _fetch_text(url, timeout=7):
    req = Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/124 Mobile Safari/537.36",
            "Accept": "application/rss+xml,application/xml,text/xml,text/html;q=0.8,*/*;q=0.5",
            "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.6",
        },
    )
    with urlopen(req, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def _location_parts(city):
    text = server._location_query(city)
    parts = []
    for part in re.split(r",|\s+-\s+", text):
        part = part.strip()
        if part and _norm(part) not in {"rj", "sp", "mg", "es", "br", "brasil"}:
            parts.append(part)
    return parts


def _clean_title(title, city):
    value = re.sub(r"\s+", " ", html.unescape(str(title or ""))).strip()
    if not value:
        return ""

    # Remove sufixos comuns de resultados de busca e diretórios.
    for sep in (" | ", " — ", " - "):
        if sep in value:
            left, right = value.split(sep, 1)
            right_norm = _norm(right)
            if any(word in right_norm for word in (
                "telefone", "endereco", "avaliacoes", "menu", "precos", "instagram",
                "facebook", "ifood", "tripadvisor", "guia", "vila", "rio de janeiro",
            )):
                value = left.strip()
                break

    # Remove cidade/bairro quando o título termina com a localização.
    changed = True
    while changed:
        changed = False
        for part in sorted(_location_parts(city), key=len, reverse=True):
            pattern = re.compile(r"(?:\s*[-,|·]?\s*)" + re.escape(part) + r"\s*$", re.I)
            new_value = pattern.sub("", value).strip(" -|,·")
            if new_value != value and len(new_value) >= 3:
                value = new_value
                changed = True

    return value[:120]


def _niche_terms(niche):
    key = _norm(niche)
    special = {
        "pizzaria": ["pizzaria", "pizza"],
        "pizza": ["pizzaria", "pizza"],
        "barbearia": ["barbearia", "barber", "barbershop"],
        "barbeiro": ["barbearia", "barber", "barbershop"],
        "salao": ["salao", "beleza", "beauty"],
        "salao de beleza": ["salao", "beleza", "beauty"],
        "oficina": ["oficina", "mecanica", "car repair"],
        "pet shop": ["pet shop", "petshop"],
        "academia": ["academia", "fitness", "gym"],
    }
    return special.get(key, [word for word in key.split() if len(word) >= 4] or [key])


def _score_result(title, description, url, niche, city):
    haystack = _norm(f"{title} {description} {url}")
    score = 0
    for term in _niche_terms(niche):
        if _norm(term) in haystack:
            score += 3
            break

    for part in _location_parts(city):
        p = _norm(part)
        if len(p) >= 4 and p in haystack:
            score += 2

    host = _norm(urlparse(url).netloc)
    if any(domain in host for domain in (
        "instagram.com", "facebook.com", "ifood.com.br", "deliverydireto.com.br",
        "apontador.com.br", "telelistas.net", "guiatelefone.com", "benditoguia.com.br",
        "restaurantguru.com.br", "suapizzaria.com", "pizzariaweb.com.br",
    )):
        score += 1
    return score


def _rss_results(query):
    params = urlencode({
        "q": query,
        "format": "rss",
        "setlang": "pt-br",
        "cc": "br",
    })
    xml_text = _fetch_text(f"https://www.bing.com/search?{params}", timeout=7)
    root = ET.fromstring(xml_text)
    items = []
    for item in root.findall(".//item"):
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        description = re.sub(r"<[^>]+>", " ", item.findtext("description") or "")
        description = re.sub(r"\s+", " ", html.unescape(description)).strip()
        if title and link:
            items.append((title, link, description))
    return items


def robust_web_search(niche, city, count, segment, seen):
    location = server._location_query(city)
    parts = _location_parts(city)
    locality = parts[0] if parts else location

    queries = [
        f'{niche} "{location}"',
        f'{niche} "{locality}"',
    ]

    candidates = []
    seen_urls = set()
    for query in queries:
        try:
            rows = _rss_results(query)
        except Exception:
            rows = []

        for raw_title, url, description in rows:
            if url in seen_urls:
                continue
            seen_urls.add(url)

            name = _clean_title(raw_title, city)
            normalized = _norm(name)
            if not server._useful_name(name, niche, segment) or normalized in seen:
                continue

            host = _norm(urlparse(url).netloc)
            if not host or any(blocked in host for blocked in (
                "bing.com", "youtube.com", "wikipedia.org", "gov.br",
            )):
                continue

            score = _score_result(raw_title, description, url, niche, city)
            if score < 3:
                continue

            candidates.append((score, name, url, host))

        if len(candidates) >= count * 2:
            break

    candidates.sort(key=lambda row: row[0], reverse=True)
    leads = []
    for _, name, url, host in candidates:
        normalized = _norm(name)
        if normalized in seen:
            continue
        seen.add(normalized)

        leads.append({
            "name": name,
            "segment": segment,
            "city": city,
            "public_url": url,
            "public_phone": "",
            "public_instagram": url if "instagram.com" in host else "",
            "address": "",
            "signal": "Negócio encontrado em resultado público da web. Confirme endereço, perfil e contato antes da abordagem.",
            "opportunity": "Analisar a presença digital pública e oferecer um pacote curto de conteúdo comercial.",
            "offer": "Pacote Comercial Express: bio/posicionamento, 5 conteúdos, 5 legendas e mensagens de WhatsApp personalizadas.",
            "first_message": f"Olá! Encontrei o {name} pesquisando negócios de {city}. Trabalho com materiais digitais simples para pequenos negócios. Posso te mandar uma ideia de divulgação sem compromisso?",
            "source": "Busca web pública/Bing RSS",
        })
        if len(leads) >= count:
            break

    # Se o RSS não trouxer o suficiente, reaproveita o fallback DuckDuckGo já existente.
    if len(leads) < count:
        try:
            extra = _ORIGINAL_DDG(niche, city, count - len(leads), segment, seen)
        except Exception:
            extra = []
        leads.extend(extra)

    return leads[:count]


_ORIGINAL_DDG = server._web_search_fallback
server._web_search_fallback = robust_web_search
