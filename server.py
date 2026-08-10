import html
import json
import re
import unicodedata
from html.parser import HTMLParser
from urllib.parse import parse_qs, unquote, urlencode, urlparse
from urllib.request import Request, urlopen

from flask import jsonify, request

import app as base

app = base.app

OVERPASS_ENDPOINTS = [
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass-api.de/api/interpreter",
]

GENERIC_NAMES = {
    "barbearia", "barber shop", "barbershop", "barbeiro",
    "salao", "salao de beleza", "salon", "hairdresser",
    "pizzaria", "pizza", "restaurante", "restaurant",
    "lanchonete", "cafeteria", "cafe", "padaria", "bakery",
    "oficina", "oficina mecanica", "pet shop", "petshop",
    "academia", "dentista", "clinica", "farmacia",
    "loja de roupas", "roupas", "eletricista", "encanador", "pintor",
}

BAD_TITLE_PREFIXES = (
    "melhores ", "os melhores ", "onde comer", "lista de ", "guia de ",
    "pizzaria em ", "pizzarias em ", "barbearia em ", "barbearias em ",
    "restaurantes em ", "salões em ", "saloes em ", "encontre ",
)

PLATFORM_WORDS = (
    "instagram", "facebook", "ifood", "tripadvisor", "foursquare",
    "guiamais", "telelistas", "apontador", "waze", "google maps",
)


class _LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links = []
        self._href = None
        self._classes = ""
        self._text = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() != "a":
            return
        data = dict(attrs)
        self._href = data.get("href")
        self._classes = data.get("class", "")
        self._text = []

    def handle_data(self, data):
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == "a" and self._href is not None:
            text = html.unescape("".join(self._text)).strip()
            if text:
                self.links.append((self._href, text, self._classes))
            self._href = None
            self._classes = ""
            self._text = []


def _norm(value):
    value = unicodedata.normalize("NFD", str(value or "").lower().strip())
    return "".join(ch for ch in value if unicodedata.category(ch) != "Mn")


def _useful_name(name, niche, segment):
    normalized = _norm(name)
    if not normalized or len(normalized) < 3:
        return False
    blocked = GENERIC_NAMES | {_norm(niche), _norm(segment)}
    if normalized in blocked:
        return False
    return not any(normalized.startswith(prefix) for prefix in BAD_TITLE_PREFIXES)


def _http_json(url, data=None, timeout=6):
    headers = {
        "User-Agent": base.OSM_USER_AGENT,
        "Accept": "application/json",
    }
    if data is None:
        req = Request(url, headers=headers)
    else:
        body = urlencode(data).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        req = Request(url, data=body, headers=headers, method="POST")

    with urlopen(req, timeout=timeout) as response:
        raw = response.read().decode("utf-8")
    return json.loads(raw)


def _http_text(url, data=None, timeout=7):
    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 Chrome/124 Mobile Safari/537.36",
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.7",
    }
    if data is None:
        req = Request(url, headers=headers)
    else:
        body = urlencode(data).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        req = Request(url, data=body, headers=headers, method="POST")
    with urlopen(req, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def _location_query(city):
    text = str(city or "").strip()
    if " - " in text:
        main_city, locality = [part.strip() for part in text.split(" - ", 1)]
        if main_city and locality:
            return f"{locality}, {main_city}"
    return text


def _geocode_city(city):
    query_text = _location_query(city)
    key = _norm(query_text)
    if key in base.CITY_CACHE:
        return base.CITY_CACHE[key]

    params = urlencode({
        "q": query_text,
        "format": "jsonv2",
        "limit": 1,
        "countrycodes": "br",
    })
    try:
        rows = _http_json(f"{base.NOMINATIM_URL}/search?{params}", timeout=6)
    except Exception as exc:
        raise RuntimeError("Não consegui localizar a cidade ou bairro agora. Tente novamente em alguns segundos.") from exc

    if not rows:
        raise RuntimeError("Local não encontrado. Tente: Vila Valqueire, Rio de Janeiro, RJ.")

    coords = (float(rows[0]["lat"]), float(rows[0]["lon"]))
    base.CITY_CACHE[key] = coords
    return coords


def _overpass_once(query):
    for endpoint in OVERPASS_ENDPOINTS:
        try:
            payload = _http_json(endpoint, {"data": query}, timeout=6)
            if isinstance(payload, dict):
                return payload.get("elements", [])
        except Exception:
            continue
    return []


def _lead(name, segment, city, tags, osm_type="node", osm_id="", source="OpenStreetMap"):
    website = str(tags.get("contact:website") or tags.get("website") or "").strip()
    phone = str(tags.get("contact:phone") or tags.get("phone") or "").strip()
    instagram = str(tags.get("contact:instagram") or tags.get("instagram") or "").strip()
    facebook = str(tags.get("contact:facebook") or tags.get("facebook") or "").strip()
    street = str(tags.get("addr:street") or "").strip()
    house = str(tags.get("addr:housenumber") or "").strip()
    address = " ".join(x for x in (street, house) if x).strip()
    osm_url = f"https://www.openstreetmap.org/{osm_type}/{osm_id}" if osm_id else "https://www.openstreetmap.org"

    channels = [
        label for value, label in (
            (website, "site"),
            (instagram, "Instagram"),
            (facebook, "Facebook"),
            (phone, "telefone"),
        ) if value
    ]

    if channels:
        signal = "O cadastro público informa: " + ", ".join(channels) + "."
        opportunity = "Oferecer uma revisão da comunicação e um pacote curto de conteúdo comercial."
    else:
        signal = "Negócio localizado em cadastro público; os canais digitais precisam ser confirmados antes da abordagem."
        opportunity = "Oferecer uma análise rápida da presença digital e um pequeno pacote de conteúdo comercial."

    return {
        "name": name,
        "segment": segment,
        "city": city,
        "public_url": website or instagram or facebook or osm_url,
        "public_phone": phone,
        "public_instagram": instagram,
        "address": address,
        "signal": signal,
        "opportunity": opportunity,
        "offer": "Pacote Comercial Express: bio/posicionamento, 5 conteúdos, 5 legendas e mensagens de WhatsApp personalizadas.",
        "first_message": f"Olá! Encontrei o {name} em uma fonte pública de negócios de {city}. Trabalho com materiais digitais simples para pequenos negócios. Posso te mandar uma ideia de divulgação sem compromisso?",
        "source": source,
    }


def _nominatim_fallback(niche, city, count, segment, seen):
    params = urlencode({
        "q": f"{niche}, {_location_query(city)}",
        "format": "jsonv2",
        "limit": min(max(count * 4, 8), 16),
        "countrycodes": "br",
        "addressdetails": 1,
        "extratags": 1,
        "namedetails": 1,
    })
    try:
        rows = _http_json(f"{base.NOMINATIM_URL}/search?{params}", timeout=6)
    except Exception:
        return []

    leads = []
    for row in rows or []:
        namedetails = row.get("namedetails") or {}
        name = str(namedetails.get("name") or row.get("display_name", "").split(",")[0]).strip()
        normalized = _norm(name)
        if not _useful_name(name, niche, segment) or normalized in seen:
            continue

        seen.add(normalized)
        extras = row.get("extratags") or {}
        tags = {
            "website": extras.get("website") or extras.get("contact:website") or "",
            "phone": extras.get("phone") or extras.get("contact:phone") or "",
        }
        item = _lead(
            name,
            segment,
            city,
            tags,
            row.get("osm_type") or "node",
            row.get("osm_id") or "",
            "OpenStreetMap/Nominatim",
        )
        item["address"] = row.get("display_name", "")
        leads.append(item)
        if len(leads) >= count:
            break

    return leads


def _selectors_for_niche(niche, filters, radius_m, lat, lon):
    key = _norm(niche)
    if key in {"pizzaria", "pizza"}:
        return [
            f'nwr["amenity"~"^(restaurant|fast_food)$"]["cuisine"~"pizza",i]["name"](around:{radius_m},{lat},{lon});',
            f'nwr["amenity"~"^(restaurant|fast_food)$"]["name"~"pizza|pizzaria",i](around:{radius_m},{lat},{lon});',
            f'nwr["food:pizza"="yes"]["name"](around:{radius_m},{lat},{lon});',
        ]

    return [
        f"nwr{filter_text}[\"name\"](around:{radius_m},{lat},{lon});"
        for filter_text in filters
    ]


def _unwrap_ddg_url(href):
    href = html.unescape(str(href or "").strip())
    if not href:
        return ""
    if href.startswith("//"):
        href = "https:" + href
    parsed = urlparse(href)
    if "duckduckgo.com" in parsed.netloc and "uddg" in parse_qs(parsed.query):
        return unquote(parse_qs(parsed.query)["uddg"][0])
    if parsed.scheme in {"http", "https"} and "duckduckgo.com" not in parsed.netloc:
        return href
    return ""


def _clean_search_title(title):
    value = re.sub(r"\s+", " ", html.unescape(str(title or ""))).strip()
    if not value:
        return ""

    # Remove sufixos típicos de plataformas sem destruir nomes comerciais com hífen.
    for sep in (" | ", " — ", " - "):
        if sep in value:
            left, right = value.rsplit(sep, 1)
            if any(word in _norm(right) for word in PLATFORM_WORDS):
                value = left.strip()

    value = re.sub(r"\s*[|·]\s*(Instagram|Facebook|iFood|Tripadvisor).*?$", "", value, flags=re.I).strip()
    return value[:120]


def _web_search_fallback(niche, city, count, segment, seen):
    query_text = f'"{niche}" "{_location_query(city)}"'
    search_pages = [
        ("https://html.duckduckgo.com/html/", {"q": query_text, "kl": "br-pt"}),
        ("https://lite.duckduckgo.com/lite/", {"q": query_text, "kl": "br-pt"}),
    ]

    raw_links = []
    for endpoint, payload in search_pages:
        try:
            page = _http_text(endpoint, payload, timeout=7)
            parser = _LinkParser()
            parser.feed(page)
            raw_links = parser.links
            if raw_links:
                break
        except Exception:
            continue

    leads = []
    seen_urls = set()
    for href, title, classes in raw_links:
        # No HTML completo, result__a reduz falsos positivos. No Lite aceitamos redirects externos.
        url = _unwrap_ddg_url(href)
        if not url or url in seen_urls:
            continue
        if classes and "result__a" not in classes and "result-link" not in classes:
            continue

        name = _clean_search_title(title)
        normalized = _norm(name)
        if not _useful_name(name, niche, segment) or normalized in seen:
            continue

        host = _norm(urlparse(url).netloc)
        if not host:
            continue
        if any(blocked in host for blocked in ("duckduckgo.com", "youtube.com", "wikipedia.org")):
            continue

        seen.add(normalized)
        seen_urls.add(url)
        leads.append({
            "name": name,
            "segment": segment,
            "city": city,
            "public_url": url,
            "public_phone": "",
            "public_instagram": url if "instagram.com" in host else "",
            "address": "",
            "signal": "Resultado público encontrado na web. Confirme o perfil, endereço e contato antes da abordagem.",
            "opportunity": "Analisar rapidamente a presença digital pública e oferecer um pacote curto de conteúdo comercial.",
            "offer": "Pacote Comercial Express: bio/posicionamento, 5 conteúdos, 5 legendas e mensagens de WhatsApp personalizadas.",
            "first_message": f"Olá! Encontrei o {name} pesquisando negócios de {city}. Trabalho com materiais digitais simples para pequenos negócios. Posso te mandar uma ideia de divulgação sem compromisso?",
            "source": "Busca web pública",
        })
        if len(leads) >= count:
            break

    return leads


def discover_leads(niche, city, count):
    filters, segment = base.osm_rule_for_niche(niche)
    leads = []
    seen = set()

    # 1) OpenStreetMap/Overpass: estruturado, quando houver dados.
    try:
        lat, lon = _geocode_city(city)
        radius_m = 10000 if " - " in str(city) else 16000
        selectors = _selectors_for_niche(niche, filters, radius_m, lat, lon)
        query = "[out:json][timeout:6];(" + "".join(selectors) + ");out tags center 80;"
        elements = _overpass_once(query)

        for element in elements:
            tags = element.get("tags") or {}
            name = str(tags.get("name") or "").strip()
            normalized = _norm(name)
            if not _useful_name(name, niche, segment) or normalized in seen:
                continue
            seen.add(normalized)
            leads.append(_lead(
                name,
                segment,
                city,
                tags,
                element.get("type", "node"),
                element.get("id", ""),
                "OpenStreetMap/Overpass",
            ))
            if len(leads) >= count:
                return leads[:count]
    except Exception:
        pass

    # 2) Nominatim: busca textual na mesma base.
    if len(leads) < count:
        leads.extend(_nominatim_fallback(niche, city, count - len(leads), segment, seen))
    if len(leads) >= count:
        return leads[:count]

    # 3) Busca web pública: cobre negócios que não estão bem cadastrados no OSM.
    leads.extend(_web_search_fallback(niche, city, count - len(leads), segment, seen))
    return leads[:count]


# Mantém compatibilidade com app.py.
base.osm_discover = discover_leads


def _prospect_view():
    data = request.get_json(silent=True) or {}
    niche = str(data.get("niche", "")).strip()
    city = str(data.get("city", "")).strip()

    try:
        count = max(1, min(int(data.get("count", 5) or 5), 10))
    except Exception:
        count = 5

    if not niche or not city:
        return jsonify({"ok": False, "error": "Informe nicho e cidade."}), 400

    try:
        leads = discover_leads(niche, city, count)
        if not leads:
            return jsonify({
                "ok": False,
                "error": "Não encontrei leads verificáveis nessa tentativa. A fonte de mapas e a busca web pública não retornaram nomes úteis. Tente um bairro próximo ou outro nicho.",
            }), 404

        return jsonify({
            "ok": True,
            "leads": leads,
            "sources": [{
                "title": "Fontes públicas: OpenStreetMap e busca web",
                "url": "https://www.openstreetmap.org/copyright",
            }],
        })
    except Exception as exc:
        return jsonify({
            "ok": False,
            "error": f"Falha temporária na prospecção: {str(exc) or exc.__class__.__name__}",
        }), 503


app.view_functions["api_prospect"] = base.protected(_prospect_view)
