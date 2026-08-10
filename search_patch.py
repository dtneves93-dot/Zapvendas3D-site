import json
import os
import re
import unicodedata
from urllib.parse import urlparse
from urllib.request import Request, urlopen

import server

TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "").strip()
TAVILY_URL = "https://api.tavily.com/search"

GENERIC_PREFIXES = (
    "melhores ", "os melhores ", "lista de ", "guia de ", "onde comer",
    "pizzarias em ", "pizzaria em ", "barbearias em ", "barbearia em ",
    "restaurantes em ", "salões em ", "saloes em ", "encontre ",
    "vagas de ", "vaga de ", "empregos em ", "emprego em ",
)

BLOCKED_DOMAINS = (
    "indeed.com", "indeed.com.br", "br.indeed.com", "linkedin.com", "glassdoor.com",
    "catho.com.br", "infojobs.com.br", "jooble.org", "simplyhired.com", "talent.com",
    "youtube.com", "wikipedia.org", "tiktok.com", "pinterest.com",
)

BAD_TITLE_TERMS = (
    " vagas ", " vaga ", " empregos ", " emprego ", " trabalhe conosco ",
    " oportunidade de emprego ", " salários ", " salarios ", " currículo ", " curriculo ",
)

SOCIAL_POST_PATHS = (
    "/p/", "/reel/", "/reels/", "/stories/", "/tv/", "/posts/", "/watch/", "/videos/",
)

SERVICE_TERMS = {
    "barbearia": ["barbearia", "barber", "barbershop", "corte masculino", "barba", "cabelo masculino"],
    "barbeiro": ["barbearia", "barber", "barbershop", "corte masculino", "barba", "cabelo masculino"],
    "pizzaria": ["pizzaria", "pizza", "pizzas", "forneria"],
    "pizza": ["pizzaria", "pizza", "pizzas", "forneria"],
    "salao": ["salao", "beleza", "beauty", "cabeleireiro", "cabelos"],
    "salao de beleza": ["salao", "beleza", "beauty", "cabeleireiro", "cabelos"],
    "oficina": ["oficina", "mecanica", "mecânica", "automotivo", "auto center"],
    "pet shop": ["pet shop", "petshop", "banho e tosa", "pet"],
    "academia": ["academia", "fitness", "gym", "musculacao", "musculação"],
}


def _norm(value):
    value = unicodedata.normalize("NFD", str(value or "").lower().strip())
    return "".join(ch for ch in value if unicodedata.category(ch) != "Mn")


def _location_parts(city):
    text = server._location_query(city)
    parts = []
    for part in re.split(r",|\s+-\s+", text):
        part = part.strip()
        if part and _norm(part) not in {"rj", "sp", "mg", "es", "br", "brasil"}:
            parts.append(part)
    return parts


def _primary_locality(city):
    parts = _location_parts(city)
    return parts[0] if parts else server._location_query(city)


def _clean_title(title, city):
    value = re.sub(r"\s+", " ", str(title or "")).strip()
    if not value:
        return ""

    match = re.match(r"^(.*?)\s+on\s+Instagram\s*:", value, flags=re.I)
    if match:
        value = match.group(1).strip()

    for sep in (" | ", " — ", " - "):
        if sep in value:
            left, right = value.rsplit(sep, 1)
            right_n = _norm(right)
            if any(word in right_n for word in (
                "instagram", "facebook", "ifood", "tripadvisor", "menu", "delivery",
                "telefone", "endereco", "rio de janeiro", "vila valqueire",
            )):
                value = left.strip()

    normalized = _norm(value)
    if any(normalized == _norm(part) for part in _location_parts(city)):
        return ""
    return value[:120]


def _niche_terms(niche):
    key = _norm(niche)
    if key in SERVICE_TERMS:
        return SERVICE_TERMS[key]
    return [w for w in key.split() if len(w) >= 4] or [key]


def _is_bad_url(url):
    parsed = urlparse(url)
    host = _norm(parsed.netloc)
    path = parsed.path.lower()
    if not host:
        return True
    if any(domain in host for domain in BLOCKED_DOMAINS):
        return True
    if any(domain in host for domain in ("instagram.com", "facebook.com")):
        if any(marker in path for marker in SOCIAL_POST_PATHS):
            return True
    return False


def _business_quality(url, title, content, city):
    parsed = urlparse(url)
    host = _norm(parsed.netloc)
    path = parsed.path.strip("/")
    haystack = _norm(f"{title} {content} {url}")
    score = 0

    if any(domain in host for domain in ("instagram.com", "facebook.com")):
        score += 4 if path and "/" not in path else 1
    elif any(domain in host for domain in ("ifood.com.br", "deliverydireto.com.br")):
        score += 3
    else:
        score += 4

    locality = _norm(_primary_locality(city))
    if locality and locality in haystack:
        score += 5

    if any(word in _norm(title) for word in ("oficial", "pizzaria", "pizza", "barbearia", "barber", "salao", "salão")):
        score += 1
    return score


def _looks_relevant(title, content, url, niche, city, tavily_score):
    haystack = _norm(f" {title} {content} {url} ")
    if any(term in haystack for term in BAD_TITLE_TERMS):
        return False

    if not any(_norm(term) in haystack for term in _niche_terms(niche)):
        return False

    locality = _norm(_primary_locality(city))
    if locality and locality in haystack:
        return True

    # Para perfil/site comercial muito relevante, o snippet pode omitir o bairro.
    # Só aceitamos nesse caso quando a própria Tavily deu relevância alta.
    host = _norm(urlparse(url).netloc)
    commercial = any(domain in host for domain in (
        "instagram.com", "facebook.com", "ifood.com.br", "deliverydireto.com.br",
    ))
    return bool(commercial and tavily_score >= 0.62)


def _request_tavily(query, max_results):
    payload = {
        "query": query,
        "search_depth": "basic",
        "topic": "general",
        "country": "brazil",
        "max_results": min(max(max_results, 8), 20),
        "include_answer": False,
        "include_raw_content": False,
        "exclude_domains": list(BLOCKED_DOMAINS),
    }
    req = Request(
        TAVILY_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {TAVILY_API_KEY}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    with urlopen(req, timeout=12) as response:
        return json.loads(response.read().decode("utf-8"))


def _candidate_rows(data, niche, city, segment, seen):
    candidates = []
    for result in data.get("results", []) or []:
        title = str(result.get("title") or "").strip()
        url = str(result.get("url") or "").strip()
        content = str(result.get("content") or "").strip()
        tavily_score = float(result.get("score") or 0)
        if not title or not url or _is_bad_url(url):
            continue

        name = _clean_title(title, city)
        normalized = _norm(name)
        if not name or normalized in seen:
            continue
        if any(normalized.startswith(prefix) for prefix in GENERIC_PREFIXES):
            continue
        if not server._useful_name(name, niche, segment):
            continue
        if not _looks_relevant(title, content, url, niche, city, tavily_score):
            continue

        host = _norm(urlparse(url).netloc)
        quality = _business_quality(url, title, content, city)
        candidates.append((quality, tavily_score, name, url, host, content))
    return candidates


def _tavily_search(niche, city, count, segment, seen):
    if not TAVILY_API_KEY:
        return []

    locality = _primary_locality(city)
    location = server._location_query(city)
    niche_key = _norm(niche)

    queries = [
        f'{niche} em "{locality}" Rio de Janeiro RJ endereço telefone perfil oficial',
    ]
    # Se a primeira consulta não completar, uma segunda formulação mais curta ajuda perfis sociais.
    queries.append(f'"{locality}" {niche} Instagram Facebook WhatsApp Rio de Janeiro')

    candidates = []
    seen_urls = set()
    for index, query in enumerate(queries):
        try:
            data = _request_tavily(query, count * 6)
        except Exception:
            continue

        for row in _candidate_rows(data, niche, city, segment, seen):
            if row[3] in seen_urls:
                continue
            seen_urls.add(row[3])
            candidates.append(row)

        # Evita gastar o segundo crédito quando a primeira busca já trouxe candidatos suficientes.
        unique_names = {_norm(row[2]) for row in candidates}
        if len(unique_names) >= count:
            break

    candidates.sort(key=lambda row: (row[0], row[1]), reverse=True)
    leads = []
    for quality, tavily_score, name, url, host, content in candidates:
        normalized = _norm(name)
        if normalized in seen:
            continue
        seen.add(normalized)
        locality_verified = _norm(locality) in _norm(f"{name} {content} {url}")
        verification = (
            f"A busca pública menciona {locality}." if locality_verified
            else "Perfil comercial relevante encontrado; confirme o endereço antes da abordagem."
        )
        leads.append({
            "name": name,
            "segment": segment,
            "city": city,
            "public_url": url,
            "public_phone": "",
            "public_instagram": url if "instagram.com" in host else "",
            "address": "",
            "signal": f"{verification} Resultado público da web com relevância {tavily_score:.2f}.",
            "opportunity": "Analisar a presença digital pública e oferecer um pacote curto de conteúdo comercial.",
            "offer": "Pacote Comercial Express: bio/posicionamento, 5 conteúdos, 5 legendas e mensagens de WhatsApp personalizadas.",
            "first_message": f"Olá! Encontrei o {name} pesquisando negócios de {city}. Trabalho com materiais digitais simples para pequenos negócios. Posso te mandar uma ideia de divulgação sem compromisso?",
            "source": "Busca web/Tavily",
        })
        if len(leads) >= count:
            break

    return leads


server._web_search_fallback = _tavily_search
