import math
import re

import server


def _parts(location):
    return [p.strip() for p in re.split(r",|\s+-\s+", str(location or "")) if p.strip()]


def _max_distance_km(location):
    # Bairro + cidade + UF: mantém foco local, mas inclui bairros vizinhos próximos.
    return 8.0 if len(_parts(location)) >= 3 else 16.0


def _coords(element):
    try:
        if element.get("lat") is not None and element.get("lon") is not None:
            return float(element["lat"]), float(element["lon"])
        center = element.get("center") or {}
        if center.get("lat") is not None and center.get("lon") is not None:
            return float(center["lat"]), float(center["lon"])
    except (TypeError, ValueError):
        pass
    return None


def _distance_km(lat1, lon1, lat2, lon2):
    r = 6371.0088
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def geographically_safe_discover(niche, city, count):
    filters, segment = server.base.osm_rule_for_niche(niche)
    leads = []
    seen = set()

    try:
        lat, lon = server._geocode_city(city)
        max_km = _max_distance_km(city)
        radius_m = int(max_km * 1000)
        selectors = server._selectors_for_niche(niche, filters, radius_m, lat, lon)
        query = "[out:json][timeout:6];(" + "".join(selectors) + ");out tags center 120;"
        elements = server._overpass_once(query)

        ranked = []
        for element in elements:
            point = _coords(element)
            if not point:
                continue
            distance = _distance_km(lat, lon, point[0], point[1])
            if distance > max_km:
                continue

            tags = element.get("tags") or {}
            name = str(tags.get("name") or "").strip()
            normalized = server._norm(name)
            if not server._useful_name(name, niche, segment) or normalized in seen:
                continue
            ranked.append((distance, element, tags, name, normalized))

        ranked.sort(key=lambda item: item[0])
        for distance, element, tags, name, normalized in ranked:
            seen.add(normalized)
            lead = server._lead(
                name,
                segment,
                city,
                tags,
                element.get("type", "node"),
                element.get("id", ""),
                "OpenStreetMap/Overpass + filtro geográfico",
            )
            lead["distance_km"] = round(distance, 1)
            original_signal = lead.get("signal", "")
            lead["signal"] = f"Aproximadamente {distance:.1f} km do centro do local pesquisado. {original_signal}"
            leads.append(lead)
            if len(leads) >= count:
                return leads[:count]
    except Exception:
        pass

    # Para bairro, evita Nominatim textual porque ele pode misturar homônimos distantes.
    if len(_parts(city)) < 3 and len(leads) < count:
        try:
            leads.extend(server._nominatim_fallback(niche, city, count - len(leads), segment, seen))
        except Exception:
            pass

    if len(leads) < count:
        try:
            leads.extend(server._web_search_fallback(niche, city, count - len(leads), segment, seen))
        except Exception:
            pass

    return leads[:count]


server.discover_leads = geographically_safe_discover
server.base.osm_discover = geographically_safe_discover
