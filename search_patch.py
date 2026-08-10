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


def _clean_title(title, city):
    value = re.sub(r"\s+", " ", str(title or "")).strip()
    if not value:
        return ""

    # Instagram frequentemente devolve "Nome do negócio on Instagram: legenda...".
    match = re.match(r"^(.*?)\s+on\s+Instagram\s*:", value, flags=re.I)
    if match:
        value = match.group(1).strip()

    # Remove apenas sufixos típicos de página/plataforma.
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
    special = {
        "pizzaria": ["pizzaria", "pizza"],
        "pizza": ["pizzaria", "pizza"],
        "barbearia": ["barbearia", "barber", "barbershop"],
        "barbeiro": ["barbearia", "barber", "barbershop"],
        "salao": ["salao", "beleza", "beauty"],
        "salao de beleza": ["salao", "beleza", "beauty"],
        "oficina": ["oficina", "mecanica"],
        "pet shop": ["pet shop", "petshop"],
        "academia": ["academia", "fitness", "gym"],
    }
    return special.get(key, [w for w in key.split() if len(w) >= 4] or [key])


def _is_bad_url(url):
    parsed = urlparse(url)
    host = _norm(parsed.netloc)
    path = parsed.path.lower()

    if not host:
        return True
    if any(domain in host for domain in BLOCKED_DOMAINS):
        return True

    # Para Instagram/Facebook aceitamos perfil/página, mas não um post isolado.
    if any(domain in host for domain in ("instagram.com", "facebook.com")):
        if any(marker in path for marker in SOCIAL_POST_PATHS):
            return True

    return False


def _looks_relevant(title, content, url, niche, city):
    haystack = _norm(f" {title} {content} {url} ")
    if any(term in haystack for term in BAD_TITLE_TERMS):
        return False
    if not any(_norm(term) in haystack for term in _niche_terms(niche)):
        return False

    location_terms = [_norm(p) for p in _location_parts(city) if len(_norm(p)) >= 4]
    if location_terms and not any(term in haystack for term in location_terms):
        host = _norm(urlparse(url).netloc)
        if not any(domain in host for domain in (
            "instagram.com", "facebook.com", "ifood.com.br", "deliverydireto.com.br",
            "restaurantguru.com", "tripadvisor.com", "google.com",
        )):
            return False
    return True


def _business_quality(url, title):
    parsed = urlparse(url)
    host = _norm(parsed.netloc)
    path = parsed.path.strip("/")
    score = 0

    if any(domain in host for domain in ("instagram.com", "facebook.com")):
        # Perfil/página social é um contato útil para prospecção.
        if path and "/" not in path:
            score += 3
        else:
            score += 1
    elif any(domain in host for domain in ("ifood.com.br", "deliverydireto.com.br")):
        score += 2
    else:
        # Site próprio tende a ser o melhor resultado.
        score += 4

    normalized_title = _norm(title)
    if any(word in normalized_title for word in ("oficial", "pizzaria", "pizza", "barbearia", "barber")):
        score += 1
    return score


def _tavily_search(niche, city, count, segment, seen):
    if not TAVILY_API_KEY:
        return []

    location = server._location_query(city)
    query = (
        f'{niche} em {location} negócio local oficial '
        'site Instagram Facebook iFood delivery telefone contato '
        '-vagas -emprego -curriculo'
    )
    payload = {
        "query": query,
        "search_depth": "basic",
        "topic": "general",
        "country": "brazil",
        "max_results": min(max(count * 6, 12), 20),
        "include_answer": False,
        "include_raw_content": False,
        "exclude_domains": list(BLOCKED_DOMAINS),
    }
    body = json.dumps(payload).encode("utf-8")
    req = Request(
        TAVILY_URL,
        data=body,
        headers={
            "Authorization": f"Bearer {TAVILY_API_KEY}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )

    try:
        with urlopen(req, timeout=12) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception:
        return []

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
        if not _looks_relevant(title, content, url, niche, city):
            continue

        host = _norm(urlparse(url).netloc)
        quality = _business_quality(url, title)
        candidates.append((quality, tavily_score, name, url, host))

    candidates.sort(key=lambda row: (row[0], row[1]), reverse=True)
    leads = []
    for _, _, name, url, host in candidates:
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
            "signal": "Negócio encontrado em resultado público da web. Confirme perfil, endereço e contato antes da abordagem.",
            "opportunity": "Analisar a presença digital pública e oferecer um pacote curto de conteúdo comercial.",
            "offer": "Pacote Comercial Express: bio/posicionamento, 5 conteúdos, 5 legendas e mensagens de WhatsApp personalizadas.",
            "first_message": f"Olá! Encontrei o {name} pesquisando negócios de {city}. Trabalho com materiais digitais simples para pequenos negócios. Posso te mandar uma ideia de divulgação sem compromisso?",
            "source": "Busca web/Tavily",
        })
        if len(leads) >= count:
            break

    return leads


# Substitui somente o fallback web. Sem chave ou se a API falhar, retorna vazio sem derrubar o worker.
server._web_search_fallback = _tavily_search
