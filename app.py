import json
import os
import re
from functools import wraps

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
            return json.loads(text[first_array : last_array + 1])
        except Exception:
            pass
    first_obj = text.find("{")
    last_obj = text.rfind("}")
    if first_obj >= 0 and last_obj > first_obj:
        try:
            return json.loads(text[first_obj : last_obj + 1])
        except Exception:
            pass
    return {"text": text}


def grounding_sources(response):
    sources = []
    try:
        metadata = response.candidates[0].grounding_metadata
        chunks = metadata.grounding_chunks or []
        seen = set()
        for chunk in chunks:
            web = getattr(chunk, "web", None)
            if not web:
                continue
            uri = getattr(web, "uri", None)
            title = getattr(web, "title", None)
            if uri and uri not in seen:
                sources.append({"title": title or uri, "url": uri})
                seen.add(uri)
    except Exception:
        pass
    return sources[:12]


def run_ai(prompt, use_search=False):
    config_kwargs = {"system_instruction": SYSTEM_RULES}
    if use_search:
        config_kwargs["tools"] = [types.Tool(google_search=types.GoogleSearch())]
    response = ai_client().models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(**config_kwargs),
    )
    return response


@app.get("/")
def home():
    return send_from_directory(BASE_DIR, "index.html")


@app.route("/entrar", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        password = request.form.get("password", "")
        if ADMIN_PASSWORD and password == ADMIN_PASSWORD:
            session["zapvenda_admin"] = True
            return redirect("/agente")
        error = "Senha incorreta."
    else:
        error = ""
    return f"""<!doctype html><html lang='pt-BR'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>Entrar | ZapVenda Agent</title><style>body{{font-family:system-ui;background:#071d18;color:#eefcf7;display:grid;place-items:center;min-height:100vh;margin:0}}form{{width:min(92vw,390px);background:#0d2c24;padding:28px;border-radius:18px;box-shadow:0 20px 60px #0006}}input,button{{box-sizing:border-box;width:100%;padding:14px;border-radius:10px;border:0;margin-top:12px}}button{{background:#25d366;font-weight:800;cursor:pointer}}p{{color:#b9d6cc}}.err{{color:#ffb4ab}}</style></head><body><form method='post'><h1>ZapVenda Agent</h1><p>Painel privado da sua IA comercial.</p><input name='password' type='password' placeholder='Senha do painel' autocomplete='current-password' required><button type='submit'>Entrar</button><p class='err'>{error}</p></form></body></html>"""


@app.get("/sair")
def logout():
    session.clear()
    return redirect("/")


@app.get("/agente")
@protected
def agent_page():
    return send_from_directory(BASE_DIR, "agent.html")


@app.get("/api/status")
@protected
def api_status():
    return jsonify(
        {
            "ok": True,
            "model": GEMINI_MODEL,
            "gemini_configured": bool(GEMINI_API_KEY),
            "mode": "aprovação humana",
        }
    )


@app.post("/api/prospect")
@protected
def api_prospect():
    data = request.get_json(silent=True) or {}
    niche = str(data.get("niche", "")).strip()
    city = str(data.get("city", "")).strip()
    count = max(1, min(int(data.get("count", 5) or 5), 10))
    if not niche or not city:
        return jsonify({"ok": False, "error": "Informe nicho e cidade."}), 400

    prompt = f"""
Pesquise na web e encontre até {count} negócios REAIS do nicho "{niche}" em "{city}" que possam se beneficiar de melhoria simples em divulgação digital.
Priorize negócios com presença pública verificável e algum sinal concreto de oportunidade (ex.: site simples, rede social pouco clara, comunicação sem CTA, cardápio/serviço mal apresentado). Não invente defeitos.

Retorne SOMENTE um JSON válido no formato:
{{"leads":[{{
  "name":"nome público do negócio",
  "segment":"segmento",
  "city":"cidade",
  "public_url":"URL pública verificada ou string vazia",
  "signal":"sinal público observado, em uma frase",
  "opportunity":"o que podemos melhorar sem prometer resultado",
  "offer":"uma oferta digital simples adequada ao caso",
  "first_message":"mensagem curta, individual e respeitosa de primeira abordagem"
}}]}}
""".strip()
    try:
        response = run_ai(prompt, use_search=True)
        parsed = extract_json(response.text)
        leads = parsed.get("leads", []) if isinstance(parsed, dict) else []
        return jsonify({"ok": True, "leads": leads[:count], "sources": grounding_sources(response)})
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
Analise este prospecto e retorne SOMENTE JSON com: score (0-100), reasons (array de 2 a 4 itens), best_offer, risk, next_step. O score mede aderência a um serviço digital simples e chance de haver uma dor clara, não riqueza nem valor humano.
CONTEXTO:
{context}
""",
        "outreach": """
Crie UMA mensagem inicial curta para este prospecto. Comece pelo que foi observado publicamente, mostre uma oportunidade específica e ofereça uma pequena amostra ou ideia sem compromisso. Evite elogio genérico e não diga que analisou dados privados. Retorne apenas a mensagem.
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
        return jsonify({"ok": False, "error": str(exc)}), 500


@app.get("/<path:filename>")
def public_files(filename):
    if filename.startswith("api/") or filename in {"agente", "entrar", "sair"}:
        return "Não encontrado", 404
    return send_from_directory(BASE_DIR, filename)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "10000")), debug=False)
