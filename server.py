import json
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import app as base

app = base.app

OVERPASS_ENDPOINTS = [
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
    "https://overpass-api.de/api/interpreter",
]


def _norm(value):
    value = unicodedata.normalize("NFD", str(value or "").lower().strip())
    return "".join(ch for ch in value if unicodedata.category(ch) != "Mn")


def _http_json(url, data=None, timeout=8):
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


def _geocode_city(city):
    key = _norm(city)
    if key in base.CITY_CACHE:
        return base.CITY_CACHE[key]

    params = urlencode({
        "q": city,
        "format": "jsonv2",
        "limit": 1,
        "countrycodes": "br",
    })
    try:
        results = _http_json(f"{base.NOMINATIM_URL}/search?{params}", timeout=7)
    except Exception as exc:
        raise RuntimeError("Não consegui localizar a cidade agora. Tente novamente em alguns segundos.") from exc

    if not results:
        raise RuntimeError("Cidade não encontrada. Informe também o estado, por exemplo: Rio de Janeiro, RJ.")

    coords = (float(results[0]["lat"]), float(results[0]["lon"]))
    base.CITY_CACHE[key] = coords
    return coords


def _overpass(query):
    last_error = None
    for endpoint in OVERPASS_ENDPOINTS:
        try:
            payload = _http_json(endpoint, {"data": query}, timeout=8)
            if isinstance(payload, dict) and "elements" in payload:
                return payload
        except Exception as exc:
            last_error = exc
            continue
    raise RuntimeError("Os servidores públicos de busca estão ocupados. Espere alguns segundos e tente novamente.") from last_error


def _nominatim_fallback(niche, city, count, segment):
    params = urlencode({
        "q": f"{niche}, {city}",
        "format": "jsonv2",
        "limit": min(max(count * 2, 4), 10),
        "countrycodes": "br",
        "addressdetails": 1,
        "extratags": 1,
        "namedetails": 1,
    })
    try:
        rows = _http_json(f"{base.NOMINATIM_URL}/search?{params}", timeout=8)
    except Exception:
        return []

    leads = []
    seen = set()
    for row in rows or []:
        name = ((row.get("namedetails") or {}).get("name") or row.get("display_name", "").split(",")[0]).strip()
        if not name or _norm(name) in seen:
            continue
        seen.add(_norm(name))
        extras = row.get("extratags") or {}
        website = str(extras.get("website") or extras.get("contact:website") or "").strip()
        phone = str(extras.get("phone") or extras.get("contact:phone") or "").strip()
        osm_type = row.get("osm_type") or "node"
        osm_id = row.get("osm_id") or ""
        osm_url = f"https://www.openstreetmap.org/{osm_type}/{osm_id}" if osm_id else "https://www.openstreetmap.org"
        leads.append({
            "name": name,
            "segment": segment,
            "city": city,
            "public_url": website or osm_url,
            "public_phone": phone,
            "public_instagram": "",
            "address": row.get("display_name", ""),
            "signal": "Negócio localizado em cadastro público do OpenStreetMap. Os canais digitais precisam ser confirmados antes da abordagem.",
            "opportunity": "Oferecer uma análise rápida da presença digital e um pequeno pacote de conteúdo comercial.",
            "offer": "Pacote Comercial Express: bio/posicionamento, 5 conteúdos, 5 legendas e mensagens de WhatsApp personalizadas.",
            "first_message": f"Olá! Encontrei o {name} em um cadastro público de negócios de {city}. Trabalho com materiais digitais simples para pequenos negócios. Posso te mandar uma ideia de divulgação sem compromisso?",
            "source": "OpenStreetMap/Nominatim",
        })
        if len(leads) >= count:
            break
    return leads


def fast_osm_discover(niche, city, count):
    filters, segment = base.osm_rule_for_niche(niche)
    lat, lon = _geocode_city(city)

    # Busca progressiva: começa pequena para responder rápido e só amplia se necessário.
    elements = []
    for radius_m in (6000, 11000):
        selectors = [
            f"nwr{filter_text}[\"name\"](around:{radius_m},{lat},{lon});"
            for filter_text in filters
        ]
        query = "[out:json][timeout:7];(" + "".join(selectors) + ");out center tags 20;"
        try:
            elements = (_overpass(query) or {}).get("elements", [])
        except RuntimeError:
            elements = []
        if elements:
            break

    leads = []
    seen = set()
    for element in elements:
        tags = element.get("tags") or {}
        name = str(tags.get("name") or "").strip()
        if not name or _norm(name) in seen:
            continue
        seen.add(_norm(name))

        website = str(tags.get("contact:website") or tags.get("website") or "").strip()
        phone = str(tags.get("contact:phone") or tags.get("phone") or "").strip()
        instagram = str(tags.get("contact:instagram") or tags.get("instagram") or "").strip()
        facebook = str(tags.get("contact:facebook") or tags.get("facebook") or "").strip()
        street = str(tags.get("addr:street") or "").strip()
        house = str(tags.get("addr:housenumber") or "").strip()
        address = " ".join(x for x in (street, house) if x).strip()
        osm_type = element.get("type", "node")
        osm_id = element.get("id", "")
        osm_url = f"https://www.openstreetmap.org/{osm_type}/{osm_id}" if osm_id else "https://www.openstreetmap.org"

        if not website and not instagram and not facebook:
            signal = "No cadastro público consultado, não há site ou rede social informados; isso precisa ser confirmado antes do contato."
            opportunity = "Oferecer presença digital básica e conteúdo comercial pronto."
        else:
            channels = [label for value, label in ((website, "site"), (instagram, "Instagram"), (facebook, "Facebook"), (phone, "telefone")) if value]
            signal = "O cadastro público informa: " + ", ".join(channels) + "."
            opportunity = "Oferecer revisão da comunicação e um pacote curto de conteúdo comercial."

        leads.append({
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
            "first_message": f"Olá! Encontrei o {name} em um cadastro público de negócios de {city}. Trabalho com materiais digitais simples para pequenos negócios. Posso te mandar uma ideia de divulgação sem compromisso?",
            "source": "OpenStreetMap/Overpass",
        })
        if len(leads) >= count:
            return leads

    if len(leads) < count:
        fallback = _nominatim_fallback(niche, city, count - len(leads), segment)
        for lead in fallback:
            if _norm(lead["name"]) not in seen:
                leads.append(lead)
                seen.add(_norm(lead["name"]))
                if len(leads) >= count:
                    break

    return leads


# Substitui somente a parte de prospecção. Todo o restante do agente continua em app.py.
base.osm_discover = fast_osm_discover
