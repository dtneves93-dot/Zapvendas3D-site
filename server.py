import json
import unicodedata
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import app as base

app = base.app

OVERPASS_ENDPOINTS = [
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.private.coffee/api/interpreter",
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


def _norm(value):
    value = unicodedata.normalize("NFD", str(value or "").lower().strip())
    return "".join(ch for ch in value if unicodedata.category(ch) != "Mn")


def _useful_name(name, niche, segment):
    normalized = _norm(name)
    if not normalized or len(normalized) < 3:
        return False
    blocked = GENERIC_NAMES | {_norm(niche), _norm(segment)}
    return normalized not in blocked


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
    raise RuntimeError("Os servidores públicos de busca estão ocupados. Espere alguns segundos e tente novamente.") from last_error


def _lead_from_osm(name, segment, city, tags, osm_type, osm_id, source):
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
        "first_message": f"Olá! Encontrei o {name} em um cadastro público de negócios de {city}. Trabalho com materiais digitais simples para pequenos negócios. Posso te mandar uma ideia de divulgação sem compromisso?",
        "source": source,
    }


def _nominatim_fallback(niche, city, count, segment, seen):
    params = urlencode({
        "q": f"{niche}, {city}",
        "format": "jsonv2",
        "limit": min(max(count * 4, 8), 20),
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
    for row in rows or []:
        name = ((row.get("namedetails") or {}).get("name") or row.get("display_name", "").split(",")[0]).strip()
        normalized = _norm(name)
        if not _useful_name(name, niche, segment) or normalized in seen:
            continue
        seen.add(normalized)
        extras = row.get("extratags") or {}
        tags = {
            "website": extras.get("website") or extras.get("contact:website") or "",
            "phone": extras.get("phone") or extras.get("contact:phone") or "",
        }
        lead = _lead_from_osm(
            name,
            segment,
            city,
            tags,
            row.get("osm_type") or "node",
            row.get("osm_id") or "",
            "OpenStreetMap/Nominatim",
        )
        lead["address"] = row.get("display_name", "")
        leads.append(lead)
        if len(leads) >= count:
            break
    return leads


def fast_osm_discover(niche, city, count):
    filters, segment = base.osm_rule_for_niche(niche)
    lat, lon = _geocode_city(city)

    leads = []
    seen = set()
    element_ids = set()

    # Faz buscas progressivas e acumula resultados, em vez de parar no primeiro item encontrado.
    for radius_m in (6000, 12000, 20000):
        selectors = [
            f"nwr{filter_text}[\"name\"](around:{radius_m},{lat},{lon});"
            for filter_text in filters
        ]
        query = "[out:json][timeout:7];(" + "".join(selectors) + ");out center tags 40;"
        try:
            elements = (_overpass(query) or {}).get("elements", [])
        except RuntimeError:
            elements = []

        for element in elements:
            unique_id = (element.get("type"), element.get("id"))
            if unique_id in element_ids:
                continue
            element_ids.add(unique_id)

            tags = element.get("tags") or {}
            name = str(tags.get("name") or "").strip()
            normalized = _norm(name)
            if not _useful_name(name, niche, segment) or normalized in seen:
                continue
            seen.add(normalized)

            leads.append(_lead_from_osm(
                name,
                segment,
                city,
                tags,
                element.get("type", "node"),
                element.get("id", ""),
                "OpenStreetMap/Overpass",
            ))
            if len(leads) >= count:
                return leads

    if len(leads) < count:
        leads.extend(_nominatim_fallback(niche, city, count - len(leads), segment, seen))

    return leads[:count]


# Substitui somente a prospecção. Todo o restante do agente continua em app.py.
base.osm_discover = fast_osm_discover
