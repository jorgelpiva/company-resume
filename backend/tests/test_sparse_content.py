import asyncio

from app.chat.service import answer_question_from_context
from app.processing.summarizer import (
    assess_site_content,
    has_institutional_identity,
    summarize_company_profile,
)
from app.research.company_context import research_company_context


def _fallback_profile(name: str, executive: str) -> str:
    return (
        f"# {name}\n\n"
        f"## Resumo executivo\n\n{executive}\n\n"
        "## Quem é\n\nNão identificado no conteúdo público analisado.\n\n"
        "## Fontes analisadas\n\n- https://example.com/\n"
    )


def test_sparse_search_home_explains_local_limit_and_verified_external_identity(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    pages = [{
        "url": "https://google.com/",
        "title": "Google",
        "content": "Pesquisa Google. Estou com sorte.",
        "summary": "Pesquisa Google. Estou com sorte.",
    }]
    context = {
        "entity_name": "Google",
        "site_type": "mecanismo_de_busca",
        "summary": "Google Search é um mecanismo de busca que ajuda a encontrar informações na Web.",
        "relationship": "O domínio google.com oferece o Google Search.",
        "confidence": "alta",
        "sources": [{"url": "https://www.google.com/search/howsearchworks/", "title": "Google Search"}],
    }

    profile, generation = asyncio.run(summarize_company_profile(
        {"name": "Google", "mapped_at": "2026-08-14"},
        pages,
        _fallback_profile("Google", pages[0]["summary"]),
        research_context=context,
    ))

    assert generation == "extractive+external"
    assert "mecanismo de busca" in profile
    assert "pouco conteúdo textual relevante" in profile
    assert "página inicial" in profile
    assert "identidade foi complementada por pesquisa pública" in profile
    assert "Fonte externa: https://www.google.com/search/howsearchworks/" in profile
    assert "marketplace" not in profile.lower()

    answer = asyncio.run(answer_question_from_context(
        profile,
        [{"url": "https://google.com/", "title": "Google", "content": pages[0]["content"]}],
        context,
        "Como funciona o mecanismo de busca?",
    ))
    assert "mecanismo de busca" in answer["answer"]
    assert "https://www.google.com/search/howsearchworks/" in answer["sources"]


def test_sparse_unknown_site_describes_structure_when_research_is_inconclusive(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    pages = [{
        "url": "https://example.com/help",
        "title": "Central de ajuda",
        "content": "Envie sua dúvida pelo formulário.",
        "summary": "Envie sua dúvida pelo formulário.",
    }]

    profile, generation = asyncio.run(summarize_company_profile(
        {"name": "Example", "mapped_at": "2026-08-14"},
        pages,
        _fallback_profile("Example", pages[0]["summary"]),
        research_context=None,
    ))

    assert generation == "extractive"
    assert "Central de ajuda (/help)" in profile
    assert "não encontrou evidência verificável suficiente" in profile
    assert "Envie sua dúvida pelo formulário" in profile


def test_sparse_home_is_reported_even_when_subproduct_pages_have_a_lot_of_text(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    pages = [
        {
            "url": "https://example.com/",
            "title": "Example",
            "content": "Pesquisar.",
            "summary": "Pesquisar.",
        },
        {
            "url": "https://example.com/docs/about",
            "title": "Example Docs",
            "content": "Editor colaborativo, comentários, arquivos, modelos e exportação. " * 30,
            "summary": "Editor colaborativo e recursos para documentos.",
        },
    ]
    context = {
        "entity_name": "Example Search",
        "summary": "Example Search é um mecanismo de busca.",
        "relationship": "O domínio oferece o serviço Example Search.",
        "confidence": "alta",
        "sources": [{"url": "https://example.com/", "title": "Example"}],
    }

    profile, _generation = asyncio.run(summarize_company_profile(
        {"name": "Example"},
        pages,
        _fallback_profile("Example", "Conteúdo de produtos digitais."),
        context,
    ))

    assessment = assess_site_content(pages)
    assert assessment["status"] == "sem_identidade_institucional"
    assert assessment["homepage_sparse"] is True
    assert "A página inicial forneceu pouco conteúdo textual relevante" in profile
    assert "demais páginas rastreadas" in profile


def test_identity_detector_requires_textual_identity_and_exact_route_segment():
    product_page = [{
        "url": "https://example.com/about-product",
        "title": "Sobre o produto",
        "content": "Preços, entrega, catálogo e condições de compra. " * 10,
    }]
    company_page = [{
        "url": "https://example.com/company",
        "title": "Company",
        "content": "We are a company founded to build useful software for teams. " * 8,
    }]
    subproduct_about_page = [{
        "url": "https://example.com/docs/about",
        "title": "About Example Docs",
        "content": "We are a product team founded to build collaborative documents. " * 8,
    }]

    assert not has_institutional_identity(product_page)
    assert has_institutional_identity(company_page)
    assert not has_institutional_identity(subproduct_about_page)
    assert assess_site_content(product_page)["status"] == "escasso"
    assert assess_site_content(company_page)["status"] == "escasso"


def test_homepage_founding_claim_must_refer_to_the_mapped_entity():
    third_party_history = [{
        "url": "https://loja.example/",
        "title": "Loja",
        "content": "A marca Acme foi fundada em 1998 e agora está disponível em nosso catálogo. " * 8,
    }]
    own_history = [{
        "url": "https://acme.example/",
        "title": "Acme",
        "content": "A Acme foi fundada em 1998 para desenvolver soluções para equipes. " * 8,
    }]

    assert not has_institutional_identity(third_party_history)
    assert has_institutional_identity(own_history)


def test_homepage_first_person_claim_must_refer_to_the_mapped_entity():
    merchant_testimonial = [{
        "url": "https://vitrine.example/",
        "title": "Vitrine",
        "content": (
            "Depoimento da Acme: somos uma empresa familiar e nossa missão é atender bem. " * 8
        ),
    }]
    own_description = [{
        "url": "https://vitrine.example/",
        "title": "Vitrine",
        "content": "Vitrine: somos uma empresa de tecnologia para o varejo. " * 8,
    }]

    assert not has_institutional_identity(merchant_testimonial)
    assert has_institutional_identity(own_description)


def test_product_subdomain_about_page_is_not_root_company_identity():
    pages = [
        {
            "url": "https://google.com/",
            "title": "Google",
            "content": "Pesquisa Google.",
        },
        {
            "url": "https://cloud.google.com/about",
            "title": "About Google Cloud",
            "content": "We are a company founded to provide cloud products and services. " * 8,
        },
    ]

    assert not has_institutional_identity(pages, root_domain="google.com")


def test_grounded_marketplace_hallucination_without_evidence_is_rejected(monkeypatch):
    async def fake_grounded_content(*_args, **_kwargs):
        return {
            "text": (
                '{"entity_name":"Google","site_type":"marketplace",'
                '"summary":"A organização é uma plataforma on-line para venda de produtos de comerciantes.",'
                '"organization_name":"Google LLC",'
                '"relationship":"O domínio google.com está associado ao Google.",'
                '"content_focus":[],"classification_evidence":[],"confidence":"alta"}'
            ),
            "sources": [{"url": "https://www.google.com/", "title": "Google"}],
            "queries": ["google.com site oficial o que é"],
            "model": "gemini-test",
        }

    monkeypatch.setattr("app.research.company_context.get_api_key", lambda: "teste")
    monkeypatch.setattr("app.research.company_context.generate_grounded_content", fake_grounded_content)

    result = asyncio.run(research_company_context(
        "https://google.com/",
        "google.com",
        page_evidence=[{"url": "https://google.com/", "title": "Google", "content": "Pesquisa Google"}],
    ))

    assert result["site_type"] == "mecanismo_de_busca"
    assert result["confidence"] == "media"
    assert "mecanismo de busca" in result["summary"]
    assert "venda de produtos" not in result["summary"]
    assert all("marketplace" not in question.lower() for question in result["suggested_questions"])
