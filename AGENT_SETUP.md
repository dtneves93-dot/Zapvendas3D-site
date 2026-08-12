# ZapVenda Agent — configuração do MVP

Esta branch transforma o site estático em um Web Service Python no Render, mantendo a landing page existente e adicionando o painel privado em `/agente`.

## Variáveis obrigatórias no Render

- `GEMINI_API_KEY`: chave criada no Google AI Studio para a Gemini Developer API.
- `ADMIN_PASSWORD`: senha que protege o painel `/agente`.
- `SECRET_KEY`: é gerada automaticamente pelo `render.yaml`.
- `GEMINI_MODEL`: já vem definido como `gemini-3.1-flash-lite`.

## Como usar

1. Publique esta branch ou faça merge na `main`.
2. No Render, altere/recrie o serviço usando o `render.yaml` como Web Service Python.
3. Cadastre `GEMINI_API_KEY` e `ADMIN_PASSWORD` nas Environment Variables.
4. Abra `https://SEU-DOMINIO/entrar` e use a senha definida.
5. Em `/agente`, informe nicho e cidade para buscar oportunidades públicas.
6. Adicione os melhores leads ao pipeline e use os botões de IA para qualificar, criar abordagem, responder, montar oferta, coletar briefing, produzir o material e preparar a entrega.

## Limites desta primeira versão

- O envio de mensagens e arquivos ainda exige aprovação/ação humana.
- Os leads e anotações ficam no `localStorage` do navegador do aparelho usado.
- A prospecção depende da disponibilidade e dos limites da Gemini API/Google Search.
- Não use disparo em massa nem dados pessoais sensíveis; trabalhe com contatos comerciais públicos e mensagens individualizadas.

## Próximas etapas

Depois de validar as primeiras vendas: persistência em banco, integrações de mensageria autorizadas, registro de conversas, pagamento/webhooks e entrega automática.
