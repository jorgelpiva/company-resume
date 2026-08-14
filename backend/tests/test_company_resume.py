import asyncio

import httpx
import numpy as np
import pytest
from fastapi import HTTPException

from app.chat.service import answer_question, answer_question_from_context
from app.crawler.extractor import extract_primary_content
from app.crawler.fetcher import fetch_html
from app.crawler.relevance import is_external_url, score_url_and_content
from app.crawler.robots import is_allowed_by_robots
from app.crawler.security import ensure_public_url, validate_url_syntax
from app.crawler.sitemap import extract_urls_from_sitemap
from app.crawler.url_normalizer import normalize_url
from app.main import get_company, get_job_status, get_research_seed_urls, list_companies, safe_slug
from app.gemini_client import MODELS_TO_TRY, generate_content, generate_grounded_content, get_models_to_try
from app.processing.chunker import chunk_text
from app.processing.cleaner import build_markdown
from app.processing.embeddings import encode_chunks
from app.processing.profile_builder import build_company_profile
from app.processing.summarizer import enrich_profile_with_external_identity, has_institutional_identity
from app.research.company_context import _build_public_context, research_company_context
from app.storage.local import LocalCompanyStorage


def test_gemini_model_override_is_tried_before_free_fallbacks(monkeypatch):
    monkeypatch.setenv("GEMINI_MODEL", "modelo-preferido")

    models = get_models_to_try()

    assert models[0] == "modelo-preferido"
    assert models[1:] == MODELS_TO_TRY


def test_gemini_client_falls_back_to_next_model(monkeypatch):
    calls = []

    class FakeResponse:
        def __init__(self, succeeds):
            self.succeeds = succeeds

        def raise_for_status(self):
            if not self.succeeds:
                raise httpx.HTTPStatusError(
                    "quota excedida",
                    request=httpx.Request("POST", "https://example.com"),
                    response=httpx.Response(429),
                )

        def json(self):
            return {"candidates": [{"content": {"parts": [{"text": "resposta"}]}}]}

    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, **_kwargs):
            calls.append(url)
            return FakeResponse(len(calls) > 1)

    monkeypatch.setenv("GEMINI_API_KEY", "teste")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_MODEL", raising=False)
    monkeypatch.setattr("app.gemini_client.httpx.AsyncClient", FakeClient)

    result = asyncio.run(generate_content("Olá"))

    assert result == "resposta"
    assert MODELS_TO_TRY[0] in calls[0]
    assert MODELS_TO_TRY[1] in calls[1]


def test_grounded_gemini_response_preserves_sources(monkeypatch):
    request_payloads = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "candidates": [{
                    "content": {"parts": [{"text": "resultado"}]},
                    "groundingMetadata": {
                        "webSearchQueries": ["empresa example"],
                        "groundingChunks": [{"web": {"uri": "https://example.com/sobre", "title": "Sobre"}}],
                    },
                }]
            }

    class FakeClient:
        def __init__(self, timeout):
            self.timeout = timeout

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _url, **kwargs):
            request_payloads.append(kwargs["json"])
            return FakeResponse()

    monkeypatch.setenv("GEMINI_API_KEY", "teste")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.setattr("app.gemini_client.httpx.AsyncClient", FakeClient)

    result = asyncio.run(generate_grounded_content("Identifique example.com"))

    assert request_payloads[0]["tools"] == [{"google_search": {}}]
    assert result["sources"] == [{"url": "https://example.com/sobre", "title": "Sobre"}]
    assert result["queries"] == ["empresa example"]


def test_company_research_classifies_portal_and_keeps_provenance(monkeypatch):
    async def fake_grounded_content(*_args, **_kwargs):
        return {
            "text": '{"entity_name":"Portal X","site_type":"portal_de_conteudo",'
            '"summary":"Portal de notícias.","organization_name":"Grupo X",'
            '"relationship":"Portal operado pelo Grupo X.","content_focus":["Notícias"],'
            '"suggested_questions":["Quais notícias estão em destaque?"],"confidence":"alta"}',
            "sources": [{"url": "https://example.com/institucional", "title": "Institucional"}],
            "queries": ["site example institucional"],
            "model": "gemini-2.5-flash-lite",
        }

    monkeypatch.setattr("app.research.company_context.get_api_key", lambda: "teste")
    monkeypatch.setattr("app.research.company_context.generate_grounded_content", fake_grounded_content)

    result = asyncio.run(research_company_context("https://example.com", "example.com"))

    assert result["site_type"] == "portal_de_conteudo"
    assert result["suggested_questions"] == ["Quais notícias estão em destaque?"]
    assert result["sources"][0]["url"] == "https://example.com/institucional"


def test_company_research_builds_context_from_public_search_results():
    results = [
            {
                "url": "https://pt.wikipedia.org/wiki/Mercado_Livre",
                "title": "Mercado Livre - Wikipédia",
                "snippet": "Mercado Livre é uma empresa de tecnologia e marketplace de comércio eletrônico.",
            },
            {
                "url": "https://mercadolivre.com.br/",
                "title": "Mercado Livre Brasil",
                "snippet": "Plataforma para comprar e vender produtos pela internet.",
            },
        ]

    result = _build_public_context("mercadolivre.com.br", "mercadolivre empresa", results)

    assert result["entity_name"] == "Mercado Livre"
    assert result["site_type"] == "marketplace"
    assert result["provider"] == "ddgs"
    assert "Como funciona o marketplace?" in result["suggested_questions"]


def test_external_identity_only_fills_profile_when_rag_lacks_institutional_page():
    marketplace_pages = [{"url": "https://example.com/produtos", "title": "Produtos", "content": "Produto " * 80}]
    institutional_pages = [{"url": "https://example.com/sobre", "title": "Sobre nós", "content": "Somos uma empresa. " * 30}]
    profile = "# Loja\n\n## Resumo executivo\nCatálogo de eletrônicos.\n\n## Quem é\nNão identificado no conteúdo público analisado.\n\n## História\nNão identificado no conteúdo público analisado.\n"
    context = {
        "summary": "A Loja é um marketplace que conecta compradores e vendedores.",
        "relationship": "O domínio pertence à Loja.",
        "sources": [{"url": "https://example.org/loja"}],
    }

    assert not has_institutional_identity(marketplace_pages)
    assert has_institutional_identity(institutional_pages)
    enriched = enrich_profile_with_external_identity(profile, context)
    assert "A Loja é um marketplace" in enriched
    assert "Fonte externa: https://example.org/loja" in enriched


def test_only_same_domain_research_sources_become_crawl_seeds():
    context = {
        "sources": [
            {"url": "https://www.example.com/institucional/sobre"},
            {"url": "https://carreiras.example.com/quem-somos"},
            {"url": "https://notexample.com/sobre"},
            {"url": "https://pt.wikipedia.org/wiki/Example"},
        ]
    }

    assert get_research_seed_urls(context, "example.com") == [
        "https://www.example.com/institucional/sobre",
        "https://carreiras.example.com/quem-somos",
    ]


def test_safe_slug_removes_dots_and_www():
    assert safe_slug("https://www.iconit.com.br/") == "iconit-com-br"


def test_backend_does_not_expose_a_shared_company_catalog():
    assert asyncio.run(list_companies()) == []
    with pytest.raises(HTTPException) as company_error:
        asyncio.run(get_company("empresa-de-outro-usuario"))
    with pytest.raises(HTTPException) as job_error:
        asyncio.run(get_job_status("qualquer-job"))
    assert company_error.value.status_code == 410
    assert job_error.value.status_code == 410


def test_stateless_chat_uses_context_sent_by_browser(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    result = asyncio.run(answer_question_from_context(
        "# Acme\n\n## Principais serviços\n\nConsultoria de dados para varejistas.",
        [{"url": "https://acme.example/servicos", "content": "A Acme presta consultoria de dados para varejistas."}],
        {},
        "Quais serviços oferece?",
    ))

    assert "Consultoria de dados" in result["answer"]


def test_url_canonicalization_removes_tracking_and_trailing_slash():
    assert normalize_url("HTTPS://Example.COM/page/?utm_source=x&b=2&a=1#top") == "https://example.com/page?a=1&b=2"
    assert normalize_url("https://example.com/index.html") == "https://example.com/"


def test_ssrf_rejects_unsupported_schemes_and_private_ips():
    with pytest.raises(ValueError):
        validate_url_syntax("file:///etc/passwd")
    with pytest.raises(ValueError, match="privada"):
        asyncio.run(ensure_public_url("http://127.0.0.1/"))


def test_robots_rules_apply_to_the_configured_user_agent():
    rules = ["User-agent: *", "Disallow: /admin", "Allow: /"]
    assert not is_allowed_by_robots("https://example.com/admin/users", rules)
    assert is_allowed_by_robots("https://example.com/sobre", rules)


def test_sitemap_parser_supports_namespaces():
    xml = """<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://example.com/sobre</loc></url>
    </urlset>"""
    assert extract_urls_from_sitemap(xml, "https://example.com/sitemap.xml") == ["https://example.com/sobre"]


def test_extractor_preserves_markdown_blocks_and_chunking():
    html = """<html><head><title>Empresa X</title></head><body><nav>Menu</nav><main>
      <h1>Quem somos</h1><p>{first}</p><h2>Serviços</h2><p>{second}</p>
    </main><footer>Rodapé</footer></body></html>""".format(first="A" * 900, second="B" * 900)
    extracted = extract_primary_content(html, "https://example.com")
    assert "# Quem somos\n\n" in extracted["content"]
    assert "Menu" not in extracted["content"]
    chunks = chunk_text(extracted["content"], 1000)
    assert len(chunks) >= 2
    assert all(len(chunk) <= 1000 for chunk in chunks)


def test_fetch_html_keeps_server_rendered_content_without_browser(monkeypatch):
    html = "<html><main><h1>Sobre</h1><p>" + ("Conteúdo institucional relevante. " * 12) + "</p></main></html>"

    async def fake_fetch_url(_url):
        return html

    async def unexpected_render(_url):
        raise AssertionError("não deveria renderizar conteúdo que já veio no HTML")

    monkeypatch.setattr("app.crawler.fetcher.fetch_url", fake_fetch_url)
    monkeypatch.setattr("app.crawler.fetcher.fetch_rendered_html", unexpected_render)

    assert asyncio.run(fetch_html("https://example.com/sobre")) == html


def test_fetch_html_renders_javascript_shell(monkeypatch):
    shell = "<html><head><title>Empresa</title></head><body><div id='root'></div><script>app()</script></body></html>"
    rendered = "<html><main><h1>Sobre</h1><p>" + ("História, missão, visão e valores. " * 12) + "</p></main></html>"

    async def fake_fetch_url(_url):
        return shell

    async def fake_render(_url):
        return rendered

    monkeypatch.setattr("app.crawler.fetcher.fetch_url", fake_fetch_url)
    monkeypatch.setattr("app.crawler.fetcher.fetch_rendered_html", fake_render)

    assert asyncio.run(fetch_html("https://example.com/sobre")) == rendered


def test_relevance_recognizes_institutional_ecosystem_content():
    score = score_url_and_content(
        "https://example.com/nosso-ecossistema",
        "Nosso Ecossistema",
        "Educação como protagonismo",
        content="Conheça o ecossistema, seus diferenciais, impacto e compromisso com a educação.",
    )

    assert score > 0


def test_external_url_requires_a_real_domain_boundary():
    assert not is_external_url("https://carreiras.example.com/vagas", "example.com")
    assert is_external_url("https://notexample.com/sobre", "example.com")


def test_markdown_has_traceable_front_matter():
    markdown = build_markdown("Quem somos", "https://example.com/sobre", "Conteúdo", "2026-08-14T00:00:00Z")
    assert 'title: "Quem somos"' in markdown
    assert "url: https://example.com/sobre" in markdown
    assert markdown.endswith("# Quem somos\n\nConteúdo\n")


def test_embeddings_create_multiple_searchable_vectors():
    vectors = encode_chunks(["serviços de tecnologia", "cultura e carreiras"])
    assert vectors.shape == (2, 384)
    assert np.linalg.norm(vectors[0]) == pytest.approx(1.0)


def test_profile_uses_page_content_instead_of_generic_claims():
    profile = build_company_profile(
        {"name": "Empresa X", "domain": "example.com", "source_url": "https://example.com", "mapped_at": "hoje"},
        [{"title": "Serviços", "url": "https://example.com/servicos", "summary": "A Empresa X oferece consultoria de dados para o varejo."}],
    )
    assert "consultoria de dados" in profile
    assert "organização ativa no mercado" not in profile


def test_storage_physically_deletes_company(tmp_path):
    storage = LocalCompanyStorage(tmp_path)
    company_dir = tmp_path / "empresa-x"
    (company_dir / "pages").mkdir(parents=True)
    (company_dir / "pages" / "home.md").write_text("conteúdo", encoding="utf-8")
    (company_dir / "metadata.json").write_text("{}", encoding="utf-8")
    assert storage.delete_company("empresa-x") is True
    assert not company_dir.exists()
    with pytest.raises(ValueError):
        storage.delete_company("../fora")


def test_answer_is_short_and_clean(tmp_path, monkeypatch):
    company_dir = tmp_path / "iconitcombr"
    company_dir.mkdir()
    (company_dir / "company_profile.md").write_text(
        "# Iconit\n\nA empresa atua em tecnologia e serviços digitais.\n",
        encoding="utf-8",
    )
    (company_dir / "chunks.json").write_text(
        '[{"url": "https://iconit.com.br/", "content": "A Iconit desenvolve soluções digitais e serviços para clientes corporativos."}]',
        encoding="utf-8",
    )
    (company_dir / "embeddings.npy").write_bytes(b"\x00")

    monkeypatch.setattr("app.chat.service.DATA_DIR", tmp_path)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    answer = asyncio.run(answer_question("iconit-com-br", "Quem é a empresa?"))
    paragraphs = [p.strip() for p in answer["answer"].split("\n\n") if p.strip()]
    assert len(paragraphs) <= 4
    assert "COMPANY_PROFILE" not in answer["answer"]
    assert "SOURCE_CHUNKS" not in answer["answer"]


def test_chat_uses_profile_section_and_admits_missing_career_info(tmp_path, monkeypatch):
    company_dir = tmp_path / "empresa-x"
    company_dir.mkdir()
    (company_dir / "company_profile.md").write_text(
        "# Empresa X\n\n## Principais serviços\n\nConsultoria de dados.\n\nFonte: https://example.com/servicos\n\n"
        "## Carreiras\n\nNão identificado no conteúdo público analisado.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("app.chat.service.DATA_DIR", tmp_path)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    services = asyncio.run(answer_question("empresa-x", "Quais serviços oferece?"))
    career = asyncio.run(answer_question("empresa-x", "Existe promoção interna?"))

    assert "Consultoria de dados" in services["answer"]
    assert services["sources"] == ["https://example.com/servicos"]
    assert "não fornece informação suficiente" in career["answer"]
