# Company Resume

Aplicação demonstrativa que transforma conteúdo público de um site em um perfil empresarial navegável e conversacional. O resultado deve ser tratado como um resumo das páginas coletadas, não como uma fonte oficial completa.

## Como funciona

1. O usuário informa uma URL pública.
2. O backend valida a URL, consulta `robots.txt`, descobre rotas internas e coleta um conjunto limitado de páginas.
3. O conteúdo é extraído, limpo, deduplicado, dividido em trechos e resumido.
4. Um perfil corporativo e os trechos com URLs de origem retornam ao navegador.
5. O frontend salva esse bundle somente no IndexedDB do navegador e o envia ao endpoint de chat quando necessário.

Quando o próprio site não fornece identidade institucional suficiente, o backend pode usar Gemini com Google Search grounding ou DDGS como pesquisa pública complementar. Essa etapa é opcional, depende de configuração e mantém as fontes retornadas.

## Arquitetura

```text
Vue + Vite ── POST /api/companies/map ──> FastAPI
     │                                           │
     └─ RxDB / IndexedDB <── bundle ── crawler, processamento e RAG
                         │
                         └─ POST /api/chat (perfil + trechos locais)
```

- `frontend/`: Vue 3, Vite, RxDB, Dexie e IndexedDB.
- `backend/`: FastAPI, HTTPX, Beautiful Soup/lxml, NumPy, DDGS e integração opcional com Gemini.
- Não há banco vetorial: a recuperação ranqueia os chunks recebidos do navegador com vetores locais simples. Portanto, o RAG é stateless no servidor e não cria um catálogo compartilhado de empresas.

## Segurança e privacidade

- Chaves de LLM são lidas apenas pelo backend por `GEMINI_API_KEY` ou `GOOGLE_API_KEY`; não use variáveis `VITE_*` para segredos.
- A URL de entrada aceita somente HTTP(S), sem credenciais ou portas não padrão, e valida resolução para IPs públicos.
- Cada redirecionamento é revalidado; há limites de páginas, profundidade, tamanho de resposta e tempo de requisição/renderização.
- O crawler usa `CompanyResumeBot/1.0`, aplica atraso entre solicitações e verifica as regras disponíveis de `robots.txt` antes de explorar rotas.
- O CORS é limitado às origens locais de desenvolvimento por padrão. Em produção, frontend e API devem usar a mesma origem; origens adicionais são configuradas por `CORS_ALLOW_ORIGINS`.
- O backend não persiste empresas no fluxo web. Os bundles ficam no IndexedDB do navegador até o usuário removê-los ou limpar os dados do site.

O crawler não substitui uma revisão jurídica ou de termos de uso de cada site. Páginas que exigem autenticação, proteções anti-bot, JavaScript incompatível ou regras restritivas podem não ser processadas.

## Requisitos

- Python 3.11 ou superior
- Node.js 20 ou superior
- Opcional: Chrome/Chromium para o fallback de páginas renderizadas por JavaScript

## Configuração local

Crie o arquivo de ambiente a partir do exemplo e informe uma chave válida apenas se desejar usar Gemini:

```bash
cp .env.example backend/.env
```

| Variável | Uso |
| --- | --- |
| `GEMINI_API_KEY` ou `GOOGLE_API_KEY` | chave de servidor para recursos Gemini opcionais |
| `GEMINI_MODEL` | modelo principal opcional |
| `GEMINI_SEARCH_MODEL` | modelo de grounding opcional |
| `CORS_ALLOW_ORIGINS` | origens extras, separadas por vírgula |
| `COMPANY_RESUME_WORK_DIR` | diretório temporário dos jobs |
| `CHROME_BINARY` | caminho opcional para Chrome/Chromium |

Execute o backend em um terminal:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt
cd backend
uvicorn app.main:app --reload --host 127.0.0.1 --port 61080
```

E o frontend em outro:

```bash
cd frontend
npm install
npm run dev
```

Abra `http://localhost:5173`. Durante o desenvolvimento, Vite encaminha `/api` para `http://127.0.0.1:61080`.

Para servir o build pelo FastAPI:

```bash
cd frontend && npm run build
cd ../backend && uvicorn app.main:app --host 127.0.0.1 --port 61080
```

## API

- `GET /api/health`: verificação simples de saúde.
- `POST /api/companies/map`: recebe `{ "url": "https://example.com" }` e retorna o bundle processado.
- `POST /api/chat`: recebe pergunta, histórico curto e o bundle armazenado pelo navegador; retorna resposta e URLs de origem.

As antigas rotas de armazenamento no backend respondem `410 Gone`; o fluxo atual é intencionalmente local ao navegador.

## Testes e build

```bash
cd backend && pytest
cd frontend && npm run build
```

Os testes cobrem regras de URL/SSRF, parsing, crawling, recuperação, pesquisa complementar e fluxo de mapeamento. Não há um ambiente de demonstração configurado neste repositório.

## Limitações conhecidas

- A verificação de `robots.txt` depende de conseguir obter esse arquivo; sites indisponíveis podem ser ignorados ou fornecer resultados incompletos.
- Não há autenticação, fila distribuída, rate limit por usuário ou armazenamento compartilhado: este é um projeto de portfólio, não um serviço multiusuário pronto para produção.
- A pesquisa externa e a geração por LLM são auxiliares e podem falhar por quota, rede ou política do provedor; o pipeline mantém fallback extrativo.
- O conteúdo e as classificações refletem apenas a coleta do momento. Sempre confira as fontes exibidas.

Este repositório não inclui licença. Escolha uma licença conscientemente antes de aceitar contribuições ou redistribuição.
