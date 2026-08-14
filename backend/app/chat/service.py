from __future__ import annotations

import json
import re
import unicodedata

from dotenv import load_dotenv

from app.config import DATA_DIR, TOP_K
from app.chat.retrieval import retrieve_top_chunks_from_data
from app.gemini_client import generate_content, get_api_key

load_dotenv()


def build_system_prompt() -> str:
    return """Você é um analista especializado na empresa selecionada.

Seu conhecimento factual específico sobre essa empresa vem do COMPANY_PROFILE e dos SOURCE_CHUNKS recuperados.
Nunca invente fatos.
Nunca atribua à empresa uma informação que não esteja nas fontes.
Se algo não estiver disponível, diga claramente que o conteúdo público analisado não fornece essa informação.
Você pode utilizar conhecimento geral apenas para contextualizar setor, mercado ou tecnologia.
Quando fizer isso, deixe explícito que se trata de contexto geral e não de uma declaração da empresa.
Não seja um agente de propaganda.
Seja objetivo, profissional e analítico.
"""


def _sanitize_answer(text: str) -> str:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json|text)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    cleaned = re.sub(r"(?im)^(?:COMPANY_PROFILE|SOURCE_CHUNKS|Pergunta:|Resposta:|Sistema:|Usuário:).*?\s*$", "", cleaned)
    cleaned = cleaned.replace("**", "").replace("*", "")
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = re.sub(r"\s+\n", "\n", cleaned)
    cleaned = re.sub(r"\n\s+", "\n", cleaned)
    cleaned = cleaned.strip()
    paragraphs = [part.strip() for part in cleaned.split("\n\n") if part.strip()]
    return "\n\n".join(paragraphs[:4])


def _profile_section(profile_text: str, heading: str) -> tuple[str, list[str]]:
    match = re.search(
        rf"(?ms)^##\s+{re.escape(heading)}\s*$\n(.*?)(?=^##\s+|\Z)",
        profile_text,
    )
    if not match:
        return "", []
    body = match.group(1).strip()
    sources = re.findall(r"https?://[^\s)]+", body)
    body = re.sub(r"(?m)^\s*Fonte(?: externa)?:\s+https?://\S+\s*$", "", body)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return body, list(dict.fromkeys(sources))


def _intent_section(question: str) -> str | None:
    normalized = unicodedata.normalize("NFKD", question).encode("ascii", "ignore").decode().lower()
    intents = [
        (("servico", "solucao", "o que faz"), "Principais serviços"),
        (("produto",), "Principais produtos"),
        (("quem e", "quem eh", "sobre a empresa"), "Quem é"),
        (("valor", "principio"), "Valores"),
        (("diferencial",), "Diferenciais declarados"),
        (("tecnologia",), "Tecnologias mencionadas"),
        (("segmento", "setor", "mercado"), "Segmentos atendidos"),
        (("cliente",), "Clientes mencionados"),
        (("case", "caso de sucesso"), "Cases mencionados"),
        (("cultura",), "Cultura"),
        (("carreira", "promocao", "crescimento", "vaga", "entrevista"), "Carreiras"),
        (("missao",), "Missão"),
        (("visao",), "Visão"),
        (("historia", "fundacao"), "História"),
    ]
    return next((section for terms, section in intents if any(term in normalized for term in terms)), None)


async def _google_summary(
    question: str,
    profile_text: str,
    retrieved: list[dict],
    history: list[dict[str, str]] | None = None,
) -> str | None:
    if not get_api_key():
        return None

    relevant_text = "\n\n".join(
        f"Fonte: {item.get('url', 'desconhecida')}\n{(item.get('content') or '')[:1200]}"
        for item in retrieved[:5]
        if (item.get('content') or '').strip()
    )

    recent_history = "\n".join(
        f"{item.get('role', 'user')}: {item.get('content', '')[:500]}"
        for item in (history or [])[-6:]
    )

    prompt = f"""Responda em português, em até 3 parágrafos, sem tags como COMPANY_PROFILE ou SOURCE_CHUNKS.
Use apenas as informações abaixo. Se não houver informações suficientes, diga que o conteúdo público analisado não informa isso.
Não transforme ausência de informação em afirmação genérica sobre a empresa.
Só responda afirmativamente quando houver evidência direta para a pergunta; trechos apenas relacionados ao mesmo tema não bastam.

Pergunta: {question}

Histórico recente:
{recent_history}

Perfil da empresa:
{profile_text[:16000]}

Trechos relevantes:
{relevant_text[:12000]}
"""

    try:
        text = await generate_content(
            prompt,
            generation_config={"temperature": 0.1, "maxOutputTokens": 800},
            timeout=25,
        )
        answer = _sanitize_answer(text)
        if len(answer) < 80 or answer.endswith(("para", "de", "da", "do", "e", ":")):
            return None
        return answer
    except Exception:
        return None


async def answer_question_from_context(
    profile_text: str,
    chunks: list[dict],
    research_context: dict,
    question: str,
    history: list[dict[str, str]] | None = None,
) -> dict:
    """Responde usando somente o pacote local enviado pelo navegador."""
    retrieved = retrieve_top_chunks_from_data(question, chunks, top_k=TOP_K)

    sources = [item.get("url") for item in retrieved if item.get("url")]
    deduped_sources = []
    for source in sources:
        if source not in deduped_sources:
            deduped_sources.append(source)

    section_name = _intent_section(question)
    research_sources = [
        item.get("url")
        for item in research_context.get("sources", [])
        if isinstance(item, dict) and item.get("url")
    ]
    answer_sources = list(deduped_sources)
    if section_name == "Quem é":
        answer_sources = list(dict.fromkeys([*research_sources, *answer_sources]))

    google_answer = await _google_summary(question, profile_text, retrieved, history)
    if google_answer:
        return {"answer": google_answer, "sources": answer_sources[:5]}

    if section_name:
        section_text, section_sources = _profile_section(profile_text, section_name)
        if section_text and "Não identificado no conteúdo público analisado" not in section_text:
            return {
                "answer": _sanitize_answer(section_text[:1800]),
                "sources": section_sources[:5] or deduped_sources[:5],
            }
        answer = "O conteúdo público analisado não fornece informação suficiente para responder com segurança."
        if section_name == "Carreiras":
            answer += " Esse é um bom ponto para confirmar diretamente em uma entrevista."
        return {"answer": answer, "sources": section_sources[:5]}

    candidates: list[str] = []
    for item in retrieved:
        content = (item.get("content") or "").strip()
        if not content:
            continue
        sentences = [segment.strip() for segment in re.split(r"(?<=[.!?])\s+", content) if segment.strip()]
        for sentence in sentences[:2]:
            if len(sentence) > 180:
                sentence = sentence[:180].rsplit(" ", 1)[0] + "..."
            if sentence not in candidates:
                candidates.append(sentence)

    if not candidates:
        profile_body = re.sub(r"^#{1,6}\s+.*$", "", profile_text, flags=re.M)
        candidates = [
            sentence.strip()
            for sentence in re.split(r"(?<=[.!?])\s+|\n\n+", profile_body)
            if len(sentence.strip()) >= 30 and not sentence.startswith(("Fonte:", "http", "Não identificado"))
        ]

    stopwords = {"empresa", "conteúdo", "publico", "público", "quais", "sobre", "informa", "possui", "dessa", "desta"}
    question_terms = set(re.findall(r"[\wÀ-ÿ]{4,}", question.lower())) - stopwords
    ranked = sorted(
        (
            (len(question_terms & set(re.findall(r"[\wÀ-ÿ]{4,}", sentence.lower()))), sentence)
            for sentence in candidates
        ),
        reverse=True,
    )
    useful = [sentence for score, sentence in ranked if score > 0][:3]
    if useful:
        answer = _sanitize_answer(" ".join(useful))
    else:
        answer = "O conteúdo público analisado não fornece informação suficiente para responder com segurança."
        if any(term in question.lower() for term in ("entrevista", "carreira", "crescimento", "promoção")):
            answer += " Esse é um bom ponto para confirmar diretamente em uma entrevista."

    return {
        "answer": answer,
        "sources": deduped_sources[:5],
    }


async def answer_question(company_slug: str, question: str, history: list[dict[str, str]] | None = None) -> dict:
    """Compatibilidade com dados locais antigos; o fluxo web usa answer_question_from_context."""
    normalized_slug = re.sub(r"[^a-z0-9]", "", (company_slug or "").lower())
    company_dir = DATA_DIR / company_slug
    if not company_dir.exists() and normalized_slug and DATA_DIR.exists():
        for candidate in DATA_DIR.iterdir():
            if candidate.is_dir() and re.sub(r"[^a-z0-9]", "", candidate.name.lower()) == normalized_slug:
                company_dir = candidate
                break
    if not company_dir.exists():
        fallback_answer = "O conteúdo público analisado não informa isso com precisão suficiente para responder com segurança."
        return {"answer": fallback_answer, "sources": []}

    profile_path = company_dir / "company_profile.md"
    chunks_path = company_dir / "chunks.json"
    research_path = company_dir / "research_context.json"
    profile_text = profile_path.read_text(encoding="utf-8") if profile_path.exists() else ""
    try:
        chunks = json.loads(chunks_path.read_text(encoding="utf-8")) if chunks_path.exists() else []
    except (OSError, json.JSONDecodeError):
        chunks = []
    try:
        research_context = json.loads(research_path.read_text(encoding="utf-8")) if research_path.exists() else {}
    except (OSError, json.JSONDecodeError):
        research_context = {}
    return await answer_question_from_context(profile_text, chunks, research_context, question, history)
