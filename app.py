import json
import os
import re
import unicodedata
from functools import wraps
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from flask import Flask, jsonify, redirect, request, send_from_directory, session
from google import genai
from google.genai import types

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, static_folder=None)

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "").strip()
SECRET_KEY = os.environ.get("SECRET_KEY", "").strip() or (ADMIN_PASSWORD + "::zapvenda30d")
app.secret_key = SECRET_KEY or "configure-secret-key"
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "").strip()
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.1-flash-lite").strip()

SYSTEM_RULES = """
Você é o motor comercial do ZapVenda Agent, uma microagência digital assistida por IA.
Escreva sempre em português do Brasil, de forma humana, clara e profissional.
Regras obrigatórias:
- Nunca invente nome, telefone, preço, avaliação, site ou qualquer fato sobre um prospecto.
- Nunca prometa vendas, renda, retorno financeiro ou resultado garantido.
- Não use pressão abusiva, ameaça, falsa urgência ou falsa escassez.
- Não produza spam em massa. A abordagem deve ser individual e baseada em sinais públicos reais.
- Use apenas dados comerciais públicos; não tente obter dados pessoais sensíveis.
- Ao redigir mensagens automáticas, não finja ser uma pessoa específica. Se perguntarem, assuma que é um assistente virtual.
- Priorize ofertas simples, objetivas e fáceis de entregar digitalmente.
""".strip()

OSM_USER_AGENT = "ZapVendaAgent/0.2 (public-business-discovery)"

NICHE_RULES = {
    "barbearia": (["[\"shop\"=\"hairdresser\"]"], "Barbearia"),
    "barbeiro": (["[\"shop\"=\"hairdresser\"]"], "Barbearia"),
    "salao": (["[\"shop\"=\"hairdresser\"]", "[\"shop\"=\"beauty\"]"], "Salão de beleza"),
    "salao de beleza": (["[\"shop\"=\"hairdresser\"]", "[\"shop\"=\"beauty\"]"], "Salão de beleza"),
    "manicure": (["[\"shop\"=\"beauty\"][\"beauty\"=\"nails\"]", "[\"shop\"=\"beauty\"]"], "Beleza / unhas"),
    "pizzaria": (["[\"amenity\"=\"restaurant\"][\"cuisine\"~\"pizza\",i]", "[\"amenity\"=\"fast_food\"][\"cuisine\"~\"pizza\",i]"], "Pizzaria"),
    "restaurante": (["[\"amenity\"=\"restaurant\"]"], "Restaurante"),
    "lanchonete": (["[\"amenity\"=\"fast_food\"]", "[\"amenity\"=\"cafe\"]"], "Lanchonete / café"),
    "cafeteria": (["[\"amenity\"=\"cafe\"]"], "Cafeteria"),
    "padaria": (["[\"shop\"=\"bakery\"]"], "Padaria"),
    "oficina": (["[\"shop\"=\"car_repair\"]"], "Oficina mecânica"),
    "oficina mecanica": (["[\"shop\"=\"car_repair\"]"], "Oficina mecânica"),
    "mecanica": (["[\"shop\"=\"car_repair\"]"], "Oficina mecânica"),
    "pet shop": (["[\"shop\"=\"pet\"]"], "Pet shop"),
    "petshop": (["[\"shop\"=\"pet\"]"], "Pet shop"),
    "academia": (["[\"leisure\"=\"fitness_centre\"]"], "Academia"),
    "dentista": (["[\"amenity\"=\"dentist\"]"], "Dentista"),
    "clinica": (["[\"amenity\"=\"clinic\"]"], "Clínica"),
    "farmacia": (["[\"amenity\"=\"pharmacy\"]"], "Farmácia"),
    "loja de roupas": (["[\"shop\"=\"clothes\"]"], "Loja de roupas"),
    "roupas": (["[\"shop\"=\"clothes\"]"], "Loja de roupas"),
    "eletricista": (["[\"craft\"=\"electrician\"]"], "Eletricista"),
    "encanador": (["[\"craft\"=\"plumber\"]"], "Encanador"),
    "pintor": (["[\"craft\"=\"painter\"]"], "Pintor"),
}


def normalize_text(value):
    value = unicodedata.normalize("NFD", str(value or "").lower().strip())
    return "".join(ch for ch in value if unicodedata.category(ch) != "Mn")


def http_json(url, data=None, timeout=20):
    headers = {"User-Agent": OSM_USER_AGENT, "Accept": "application/json"}
    if data is None:
        req = Request(url, headers=headers)
    else:
        body = urlencode(data).encode("utf-8")
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        req = Request(url, data=body, headers=headers, method="POST")
    with urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def geocode_city(city):
    params = urlencode({"q": city, "format": "jsonv2", "limit": 1, "countrycodes": "br"})
    results = http_json(f"https://nominatim.openstreetmap.org/search?{params}")
    if not results:
        raise RuntimeError("Não consegui localizar essa cidade. Tente informar cidade e estado, por exemplo: Rio de Janeiro, RJ.")
    bbox = results[0].get("boundingbox") or []
    if len(bbox) != 4:
        raise RuntimeError("A cidade foi encontrada, mas sem área geográfica utilizável.")
    south, north, west, east = bbox
    return float(south), float(west), float(north), float(east)


def osm_rule_for_niche(niche):
    key = normalize_text(niche)
    if key in NICHE_RULES:
        return NICHE_RULES[key]
    for candidate, rule in NICHE_RULES.items():
        if candidate in key or key in candidate:
            return rule
    supported = "barbearia, salão, pizzaria, restaurante, lanchonete, padaria, oficina, pet shop, academia, dentista, clínica, farmácia, roupas, eletricista, encanador ou pintor"
    raise RuntimeError(f"Esse nicho ainda não está mapeado na busca gratuita. Por enquanto tente: {supported}.")


def osm_discover(niche, city, count):
    filters, segment = osm_rule_for_niche(niche)
    south, west, north, east = geocode_city(city)
    bbox = f"{south},{west},{north},{east}"
    selectors = []
    for filter_text in filters:
        selectors.append(f"nwr{filter_text}[\"name\"]({bbox});")
    query = "[out:json][timeout:20];(" + "".join(selectors) + ");out center tags 60;"
    payload = http_json("https://overpass-api.de/api/interpreter", {"data": query}, timeout=30)
    elements = payload.get("elements", [])

    leads = []
    seen = set()
    for element in elements:
        tags = element.get("tags") or {}
        name = str(tags.get("name") or "").strip()
        if not name or normalize_text(name) in seen:
            continue
        seen.add(normalize_text(name))

        website = str(tags.get("contact:website") or tags.get("website") or "").strip()
        phone = str(tags.get("contact:phone") or tags.get("phone") or "").strip()
        instagram = str(tags.get("contact:instagram") or tags.get("instagram") or "").strip()
        facebook = str(tags.get("contact:facebook") or tags.get("facebook") or "").strip()
        opening_hours = str(tags.get("opening_hours") or "").strip()
        street = str(tags.get("addr:street") or "").strip()
        house = str(tags.get("addr:housenumber") or "").strip()
        address = " ".join(part for part in [street, house] if part).strip()
        osm_type = element.get("type", "node")
        osm_id = element.get("id", "")
        osm_url = f"https://www.openstreetmap.org/{osm_type}/{osm_id}" if osm_id else "https://www.openstreetmap.org"
        public_url = website or instagram or facebook or osm_url

        public_fields = []
        if website:
            public_fields.append("site")
        if phone:
            public_fields.append("telefone")
        if instagram:
            public_fields.append("Instagram")
        if facebook:
            public_fields.append("Facebook")
        if opening_hours:
            public_fields.append("horário")

        if not website and not instagram and not facebook:
            signal = "No cadastro público consultado, não há site ou rede social informados. Isso não significa que o negócio não possua esses canais."
            opportunity = "Oferecer um pacote simples de presença digital e conteúdo para facilitar apresentação e contato online."
        elif not website:
            signal = f"O cadastro público informa {', '.join(public_fields) or 'dados de contato'}, mas não traz site."
            opportunity = "Oferecer uma página simples de apresentação, bio comercial e conteúdo para redes sociais."
        else:
            signal = f"O cadastro público possui {', '.join(public_fields)}."
            opportunity = "Oferecer uma revisão da comunicação comercial e um pacote de conteúdo pronto para divulgação."

        offer = "Pacote Comercial Express: bio/posicionamento, 5 conteúdos, 5 legendas e mensagens de WhatsApp personalizadas."
        first_message = (
            f"Olá! Encontrei o {name} em um cadastro público de negócios de {city}. "
            "Trabalho com materiais digitais simples para pequenos negócios e preparei uma ideia de divulgação que pode ser adaptada para vocês. "
            "Posso te mandar uma amostra sem compromisso?"
        )

        leads.append({
            "name": name,
            "segment": segment,
            "city": city,
            "public_url": public_url,
            "public_phone": phone,
            "public_instagram": instagram,
            "address": address,
            "signal": signal,
            "opportunity": opportunity,
            "offer": offer,
            "first_message": first_message,
            "source": "OpenStreetMap",
        })
        if len(leads) >= count:
            break

    return leads


def ai_client():
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY não configurada.")
    return genai.Client(api_key=GEMINI_API_KEY)


def protected(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if not ADMIN_PASSWORD:
            return (
                "ADMIN_PASSWORD não configurada. Defina ADMIN_PASSWORD e GEMINI_API_KEY no Render e reinicie o serviço.",
                503,
            )
        if not session.get("zapvenda_admin"):
            if request.path.startswith("/api/"):
                return jsonify({"ok": False, "error": "Sessão expirada. Entre novamente."}), 401
            return redirect("/entrar")
        return view(*args, **kwargs)
    return wrapper


def extract_json(text):
    text = (text or "").strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S | re.I)
    if fenced:
        try:
            return json.loads(fenced.group(1).strip())
        except Exception:
            pass
    first_array = text.find("[")
    last_array = text.rfind("]")
    if first_array >= 0 and last_array > first_array:
        try:
            return json.loads(text[first_array:last_array + 1])
        except Exception:
            pass
    first_obj = text.find("{")
    last_obj = text.rfind("}")
    if first_obj >= 0 and last_obj > first_obj:
        try:
            return json.loads(text[first_obj:last_obj + 1])
        except Exception:
            pass
    return {"text": text}


def run_ai(prompt):
    client = ai_client()
    try:
        return client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(system_instruction=SYSTEM_RULES),
        )
    finally:
        try:
            client.close()
        except Exception:
            pass


@app.get("/")
def home():
    return send_from_directory(BASE_DIR, "index.html")


@app.route("/entrar", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        password = request.form.get("password", "")
        if ADMIN_PASSWORD and password == ADMIN_PASSWORD:
            session["zapvenda_admin"] = True
            return send_from_directory(BASE_DIR, "agent.html")
        error = "Senha incorreta."
    else:
        error = ""
    return f"""<!doctype html><html lang='pt-BR'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Entrar | ZapVenda Agent</title><style>body{{font-family:system-ui;background:#071d18;color:#eefcf7;display:grid;place-items:center;min-height:100vh;margin:0}}form{{width:min(92vw,390px);background:#0d2c24;padding:28px;border-radius:18px;box-shadow:0 20px 60px #0006}}input,button{{box-sizing:border-box;width:100%;padding:14px;border-radius:10px;border:0;margin-top:12px}}button{{background:#25d366;font-weight:800;cursor:pointer}}p{{color:#b9d6cc}}.err{{color:#ffb4ab}}</style></head><body><form method='post' action='/entrar'><h1>ZapVenda Agent</h1><p>Painel privado da sua IA comercial.</p><input name='password' type='password' placeholder='Senha do painel' autocomplete='current-password' required><button type='submit'>Entrar</button><p class='err'>{error}</p></form></body></html>"""


@app.get("/sair")
def logout():
    session.clear()
    return redirect("/")


@app.get("/agente")
@app.get("/painel")
@protected
def agent_page():
    return send_from_directory(BASE_DIR, "agent.html")


@app.get("/api/status")
@protected
def api_status():
    return jsonify({
        "ok": True,
        "model": GEMINI_MODEL,
        "gemini_configured": bool(GEMINI_API_KEY),
        "mode": "aprovação humana",
        "prospecting_source": "OpenStreetMap/Overpass",
    })


@app.post("/api/prospect")
@protected
def api_prospect():
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
        leads = osm_discover(niche, city, count)
        if not leads:
            return jsonify({"ok": False, "error": "Não encontrei negócios suficientes nesse cadastro público. Tente outro nicho, cidade ou bairro."}), 404
        return jsonify({
            "ok": True,
            "leads": leads,
            "sources": [{"title": "OpenStreetMap", "url": "https://www.openstreetmap.org"}],
        })
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.post("/api/ai")
@protected
def api_ai():
    data = request.get_json(silent=True) or {}
    action = str(data.get("action", "")).strip()
    context = data.get("context", {})
    if not action:
        return jsonify({"ok": False, "error": "Ação não informada."}), 400

    prompts = {
        "qualify": """
Analise este prospecto e retorne SOMENTE JSON com: score (0-100), reasons (array de 2 a 4 itens), best_offer, risk, next_step. O score mede aderência a um serviço digital simples e chance de haver uma dor clara, não riqueza nem valor humano. Diferencie claramente fatos públicos de inferências.
CONTEXTO:
{context}
""",
        "outreach": """
Crie UMA mensagem inicial curta para este prospecto. Use somente fatos presentes no contexto. Mostre uma oportunidade específica e ofereça uma pequena amostra ou ideia sem compromisso. Evite elogio genérico e não diga que analisou dados privados. Retorne apenas a mensagem.
CONTEXTO:
{context}
""",
        "reply": """
Você está conduzindo uma conversa comercial. Redija a próxima resposta ao cliente de forma natural. Entenda intenção, responda objeção e avance apenas um passo em direção ao fechamento. Se faltar informação, faça no máximo uma pergunta. Não invente condições. Retorne apenas a mensagem que deve ser enviada.
CONTEXTO:
{context}
""",
        "offer": """
Monte uma oferta comercial simples para este lead com: nome_do_pacote, entregaveis (3-6 itens), prazo_sugerido, preco_sugerido_brl, justificativa_curta, mensagem_de_fechamento. Use preço acessível para primeiro trabalho e mantenha o escopo fácil de produzir digitalmente. Retorne SOMENTE JSON.
CONTEXTO:
{context}
""",
        "brief": """
Crie um briefing mínimo para produzir o que foi vendido. Retorne SOMENTE JSON com: known (o que já sabemos), missing (até 6 informações indispensáveis), questions (até 6 perguntas curtas para o cliente). Não pergunte o que já estiver no contexto.
CONTEXTO:
{context}
""",
        "product": """
Produza o material digital vendido ao cliente usando o briefing fornecido. O conteúdo deve estar pronto para copiar, editar e entregar, ser específico ao nicho e não conter promessas enganosas. Organize com títulos claros. Se algum dado indispensável estiver ausente, marque [PREENCHER] em vez de inventar.
CONTEXTO:
{context}
""",
        "delivery": """
Crie uma mensagem curta e profissional de entrega do produto. Resuma o que está sendo entregue, diga como usar e faça um convite leve para ajustes. Não faça upsell agressivo. Retorne apenas a mensagem.
CONTEXTO:
{context}
""",
        "followup": """
Crie UMA mensagem de follow-up respeitosa para um prospecto que ainda não respondeu ou não decidiu. Nada de culpa, insistência ou falsa urgência. Dê uma saída elegante para a pessoa não receber novas mensagens. Retorne apenas a mensagem.
CONTEXTO:
{context}
""",
    }
    if action not in prompts:
        return jsonify({"ok": False, "error": "Ação desconhecida."}), 400

    prompt = prompts[action].format(context=json.dumps(context, ensure_ascii=False, indent=2))
    try:
        response = run_ai(prompt)
        result = extract_json(response.text) if action in {"qualify", "offer", "brief"} else response.text.strip()
        return jsonify({"ok": True, "result": result})
    except Exception as exc:
        message = str(exc)
        if "RESOURCE_EXHAUSTED" in message or "429" in message:
            message = "A cota gratuita da IA atingiu o limite temporário. Aguarde alguns minutos e tente novamente. A busca de empresas continua gratuita e não usa a cota da Gemini."
        return jsonify({"ok": False, "error": message}), 500


@app.get("/<path:filename>")
def public_files(filename):
    if filename.startswith("api/") or filename in {"agente", "painel", "entrar", "sair"}:
        return "Não encontrado", 404
    return send_from_directory(BASE_DIR, filename)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "10000")), debug=False)
