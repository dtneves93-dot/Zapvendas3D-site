import json
import unicodedata
from urllib.parse import urlencode
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


def _norm(value):
    value = unicodedata.normalize("NFD", str(value or "").lower().strip())
    return "".join(ch for ch in value if unicodedata.category(ch) != "Mn")


def _useful_name(name, niche, segment):
    normalized = _norm(name)
    if not normalized or len(normalized) < 3:
        return False
    blocked = GENERIC_NAMES | {_norm(niche), _norm(segment)}
    return normalized not in blocked


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


def _location_query(city):
    text = str(city or "").strip()
    # Permite "Rio de Janeiro, RJ - Vila Valqueire" e prioriza o bairro.
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
        "first_message": f"Olá! Encontrei o {name} em um cadastro público de negócios de {city}. Trabalho com materiais digitais simples para pequenos negócios. Posso te mandar uma ideia de divulgação sem compromisso?",
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
        # Muitos cadastros não preenchem cuisine=pizza; cobre também nome e food:pizza.
        return [
            f'nwr["amenity"~"^(restaurant|fast_food)$"]["cuisine"~"pizza",i]["name"](around:{radius_m},{lat},{lon});',
            f'nwr["amenity"~"^(restaurant|fast_food)$"]["name"~"pizza|pizzaria",i](around:{radius_m},{lat},{lon});',
            f'nwr["food:pizza"="yes"]["name"](around:{radius_m},{lat},{lon});',
        ]

    return [
        f"nwr{filter_text}[\"name\"](around:{radius_m},{lat},{lon});"
        for filter_text in filters
    ]


def fast_osm_discover(niche, city, count):
    filters, segment = base.osm_rule_for_niche(niche)
    lat, lon = _geocode_city(city)

    radius_m = 10000 if " - " in str(city) else 16000
    selectors = _selectors_for_niche(niche, filters, radius_m, lat, lon)
    query = "[out:json][timeout:6];(" + "".join(selectors) + ");out tags center 80;"
    elements = _overpass_once(query)

    leads = []
    seen = set()

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
            return leads

    if len(leads) < count:
        leads.extend(_nominatim_fallback(niche, city, count - len(leads), segment, seen))

    return leads[:count]


# Usa a versão curta da prospecção.
base.osm_discover = fast_osm_discover


# Substitui explicitamente a view da prospecção para garantir JSON legível em qualquer falha.
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
        leads = fast_osm_discover(niche, city, count)
        if not leads:
            return jsonify({
                "ok": False,
                "error": "A fonte pública respondeu, mas não encontrei negócios com nome próprio nessa busca. Tente escrever o local como: Vila Valqueire, Rio de Janeiro, RJ; ou experimente outro nicho.",
            }), 404

        return jsonify({
            "ok": True,
            "leads": leads,
            "sources": [{
                "title": "© OpenStreetMap contributors",
                "url": "https://www.openstreetmap.org/copyright",
            }],
        })
    except Exception as exc:
        return jsonify({
            "ok": False,
            "error": f"Falha temporária na busca pública: {str(exc) or exc.__class__.__name__}",
        }), 503


app.view_functions["api_prospect"] = base.protected(_prospect_view)
