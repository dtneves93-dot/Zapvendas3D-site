import json

from flask import jsonify, request

import app as base

app = base.app


def _strict_ai_view():
    data = request.get_json(silent=True) or {}
    action = str(data.get("action", "")).strip()
    context = data.get("context", {})
    if not action:
        return jsonify({"ok": False, "error": "Ação não informada."}), 400

    prompts = {
        "qualify": """
Analise este prospecto usando SOMENTE os dados literalmente presentes no CONTEXTO abaixo.
NÃO abra, leia, imagine ou descreva o conteúdo de URLs/perfis apenas porque uma URL foi fornecida.
NÃO transforme uma hipótese em fato.

Regras adicionais para FATO:
- Se o contexto contém apenas uma URL do Instagram/Facebook, o único fato permitido é que uma página/perfil público foi encontrado naquela URL.
- NÃO chame o perfil de ativo, atualizado, oficial, profissional, bem cuidado, frequente ou engajado sem evidência explícita no contexto.
- NÃO diga que há botão de WhatsApp, link de agendamento, bio otimizada, identidade visual, frequência de posts, seguidores, avaliações ou agenda se isso não estiver escrito literalmente no contexto.
- Se uma informação depende de abrir a URL para confirmar, trate como próximo passo verificável, nunca como fato atual.

Retorne SOMENTE JSON válido com:
- score: inteiro 0-100;
- reasons: array de 2 a 4 strings. Cada item deve começar exatamente por "Fato:" ou "Inferência:";
- best_offer: oferta simples coerente com os fatos disponíveis;
- risk: principal risco/limitação da qualificação;
- next_step: próximo passo verificável antes da abordagem.

Regras de score:
- poucos fatos sobre presença digital => seja conservador;
- não aumente score com suposições sobre frequência de posts, identidade visual, qualidade do perfil, número de seguidores, avaliações, agenda ou desempenho se isso não estiver explicitamente no contexto;
- o score mede apenas aderência potencial ao serviço digital, não riqueza, valor humano ou certeza de compra.

CONTEXTO:
{context}
""",
        "outreach": """
Crie UMA mensagem inicial curta para este prospecto usando SOMENTE fatos literalmente presentes no CONTEXTO.
Não diga que viu posts, frequência, identidade visual, avaliações, agenda, seguidores, atividade recente ou qualquer conteúdo do perfil se isso não estiver explicitamente descrito no contexto.
Se houver pouca informação, use uma abordagem neutra e transparente: diga que encontrou o negócio em uma fonte pública e ofereça uma ideia/amostra sem compromisso.
Não invente dor, não faça elogio genérico e não prometa resultado.
Retorne apenas a mensagem.

CONTEXTO:
{context}
""",
        "reply": """
Você está conduzindo uma conversa comercial. Use SOMENTE fatos e condições presentes no CONTEXTO. Redija a próxima resposta de forma natural, responda à intenção/objeção e avance apenas um passo. Se faltar informação indispensável, faça no máximo uma pergunta. Não invente preço, prazo, bônus, garantia ou condição. Retorne apenas a mensagem.
CONTEXTO:
{context}
""",
        "offer": """
Monte uma oferta comercial simples para este lead. Retorne SOMENTE JSON com: nome_do_pacote, entregaveis (3-6 itens), prazo_sugerido, preco_sugerido_brl, justificativa_curta, mensagem_de_fechamento.
Baseie a justificativa apenas nos fatos disponíveis. O preço é uma sugestão interna, não um fato sobre o cliente. Não prometa resultado garantido.
CONTEXTO:
{context}
""",
        "brief": """
Crie um briefing mínimo para produzir o que foi vendido. Retorne SOMENTE JSON com: known, missing, questions. Em known coloque apenas informações explicitamente presentes no CONTEXTO. Não invente dados do negócio. Faça no máximo 6 perguntas curtas.
CONTEXTO:
{context}
""",
        "product": """
Produza o material digital vendido usando somente o briefing e os dados do CONTEXTO. Se algum dado indispensável estiver ausente, marque [PREENCHER] em vez de inventar. Não faça promessas enganosas. Organize com títulos claros e conteúdo pronto para copiar/editar.
CONTEXTO:
{context}
""",
        "delivery": """
Crie uma mensagem curta e profissional de entrega. Resuma somente o que realmente consta no CONTEXTO como entregue, explique como usar e ofereça ajustes de forma leve. Não invente anexos, resultados ou itens não produzidos. Retorne apenas a mensagem.
CONTEXTO:
{context}
""",
        "followup": """
Crie UMA mensagem de follow-up respeitosa para um prospecto que ainda não respondeu ou não decidiu. Use somente fatos do CONTEXTO. Nada de culpa, insistência, falsa urgência ou falsa escassez. Dê uma saída elegante para não receber novas mensagens. Retorne apenas a mensagem.
CONTEXTO:
{context}
""",
    }

    if action not in prompts:
        return jsonify({"ok": False, "error": "Ação desconhecida."}), 400

    prompt = prompts[action].format(context=json.dumps(context, ensure_ascii=False, indent=2))
    try:
        response = base.run_ai(prompt)
        result = base.extract_json(response.text) if action in {"qualify", "offer", "brief"} else response.text.strip()
        return jsonify({"ok": True, "result": result})
    except Exception as exc:
        message = str(exc)
        if "RESOURCE_EXHAUSTED" in message or "429" in message:
            message = "A cota gratuita da IA atingiu o limite temporário. Aguarde alguns minutos e tente novamente."
        return jsonify({"ok": False, "error": message}), 500


# Mantém a mesma proteção de sessão e substitui a rota existente.
app.view_functions["api_ai"] = base.protected(_strict_ai_view)
