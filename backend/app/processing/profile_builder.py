from __future__ import annotations

from typing import Any, Dict, List

from app.processing.summarizer import summarize_text


SECTION_RULES = [
    ("Quem é", ("sobre", "quem somos", "empresa", "institucional", "about")),
    ("História", ("história", "historia", "fundada", "fundação", "trajetória")),
    ("Missão", ("missão", "missao", "propósito", "proposito")),
    ("Visão", ("visão", "visao")),
    ("Valores", ("valores", "princípios", "principios")),
    ("Posicionamento", ("posicionamento", "manifesto", "especialista", "transforma")),
    ("Principais serviços", ("serviço", "servico", "solução", "solucao", "consultoria", "outsourcing")),
    ("Principais produtos", ("produto", "plataforma", "software")),
    ("Tecnologias mencionadas", ("tecnologia", "cloud", "dados", "inteligência artificial", "ia ", "digital")),
    ("Segmentos atendidos", ("segmento", "mercado", "indústria", "industria", "setor")),
    ("Clientes mencionados", ("cliente", "customers")),
    ("Cases mencionados", ("case", "caso de sucesso")),
    ("Diferenciais declarados", ("diferencial", "vantagem", "qualidade", "experiência", "experiencia")),
    ("Cultura", ("cultura", "pessoas", "ambiente", "diversidade")),
    ("Carreiras", ("carreira", "vaga", "trabalhe conosco", "oportunidade")),
]


def build_company_profile(company: Dict[str, Any], pages: List[Dict[str, Any]]) -> str:
    name = company.get("name", "Empresa")
    source_url = company.get("source_url", "")

    page_summaries = []
    for page in pages:
        summary = page.get("summary") or summarize_text(page.get("content", ""))
        if summary:
            page_summaries.append({**page, "summary": summary})

    executive_source = " ".join(item["summary"] for item in page_summaries[:4])
    executive = summarize_text(executive_source, max_sentences=6, max_chars=2200)
    sections = [f"# {name}", "", "## Resumo executivo", "", executive or "Não identificado no conteúdo público analisado.", ""]

    career_section_text = ""
    for heading, keywords in SECTION_RULES:
        matches = []
        for page in page_summaries:
            searchable = " ".join([page.get("title", ""), page.get("url", ""), page["summary"]]).lower()
            if any(keyword in searchable for keyword in keywords):
                excerpt = summarize_text(page["summary"], max_sentences=3, max_chars=900)
                matches.append(f"{excerpt}\n\nFonte: {page.get('url', source_url)}")
        section_text = "\n\n".join(matches[:3]) or "Não identificado no conteúdo público analisado."
        if heading == "Carreiras":
            career_section_text = section_text
        sections.extend([f"## {heading}", "", section_text, ""])

    titles = [page.get("title", "") for page in page_summaries if page.get("title")]
    candidate_info = career_section_text if career_section_text and "Não identificado" not in career_section_text else "Não identificado no conteúdo público analisado. Este é um ponto adequado para confirmar diretamente em uma entrevista."
    sections.extend([
        "## Conteúdos e temas recorrentes", "",
        ", ".join(dict.fromkeys(titles[:10])) or "Não identificado no conteúdo público analisado.", "",
        "## Informações relevantes para candidatos", "", candidate_info, "",
        "## Pontos não esclarecidos pelo site", "",
        "Políticas internas, progressão de carreira, remuneração e detalhes operacionais não devem ser presumidos quando não aparecem nas fontes públicas analisadas.", "",
        "## Fontes analisadas", "",
        "\n".join(f"- {page.get('url')}" for page in page_summaries if page.get("url")) or f"- {source_url}", "",
        "## Data do mapeamento", "", company.get("mapped_at", "-"), "",
    ])
    return "\n".join(sections)
