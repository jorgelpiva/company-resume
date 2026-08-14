import asyncio

from app.main import get_research_seed_urls, map_company_process


def _patch_mapping_io(monkeypatch, tmp_path, page_entry, events):
    monkeypatch.setattr("app.main.WORK_DIR", tmp_path)
    monkeypatch.setattr("app.main.update_job", lambda *_args, **_kwargs: None)

    async def fake_robots(_url):
        return {"rules": [], "source": None}

    async def fake_sitemaps(_url):
        return []

    async def fake_discover(url, _domain, **_kwargs):
        events.append("crawl")
        return [url], {"path": "/", "children": {}}

    async def fake_extract(_route, _validated_url, _html_cache):
        events.append("extract")
        return page_entry

    async def fake_profile(_company, _pages, fallback, research_context=None):
        return fallback, "extractive+external" if research_context else "extractive"

    monkeypatch.setattr("app.main.read_robots_txt", fake_robots)
    monkeypatch.setattr("app.main.discover_sitemap_urls", fake_sitemaps)
    monkeypatch.setattr("app.main.discover_routes", fake_discover)
    monkeypatch.setattr("app.main.extract_page_entry", fake_extract)
    monkeypatch.setattr("app.main.summarize_company_profile", fake_profile)


def test_mapping_uses_local_identity_without_running_external_research(monkeypatch, tmp_path):
    events = []
    url = "https://acme.example/"
    page_entry = {
        "url": url,
        "title": "Acme",
        "h1": "Acme",
        "meta_description": "Somos uma empresa de consultoria de dados.",
        "score": 10,
        "content": "Somos uma empresa de consultoria de dados para o varejo. " * 8,
    }
    _patch_mapping_io(monkeypatch, tmp_path, page_entry, events)

    async def unexpected_research(*_args, **_kwargs):
        raise AssertionError("a pesquisa externa não deve rodar quando o site já se identifica")

    monkeypatch.setattr("app.main.research_company_context", unexpected_research)

    bundle = asyncio.run(map_company_process(url, "local-identity"))

    assert events == ["crawl", "extract"]
    assert bundle["metadata"]["identity_source"] == "rag"
    assert bundle["metadata"]["site_type"] is None
    assert bundle["research_context"] == {}


def test_mapping_searches_after_sparse_page_and_uses_verified_identity(monkeypatch, tmp_path):
    events = []
    captured_research = {}
    url = "https://google.com/"
    page_entry = {
        "url": url,
        "title": "Google",
        "h1": "",
        "meta_description": "",
        "score": 0,
        "content": "Pesquisa Google. Estou com sorte.",
    }
    _patch_mapping_io(monkeypatch, tmp_path, page_entry, events)

    async def fake_research(*_args, **kwargs):
        events.append("research")
        captured_research["page_evidence"] = kwargs.get("page_evidence")
        return {
            "entity_name": "Google",
            "site_type": "mecanismo_de_busca",
            "summary": "Google Search é um mecanismo de busca na Web.",
            "organization_name": "Google LLC",
            "relationship": "O domínio google.com oferece o Google Search.",
            "content_focus": ["Pesquisa na Web"],
            "suggested_questions": ["Como funciona o mecanismo de busca?"],
            "confidence": "alta",
            "sources": [{"url": "https://www.google.com/", "title": "Google"}],
            "search_queries": ["google.com o que é"],
            "provider": "test",
        }

    monkeypatch.setattr("app.main.research_company_context", fake_research)

    bundle = asyncio.run(map_company_process(url, "sparse-page"))

    assert events == ["crawl", "extract", "research"]
    assert bundle["metadata"]["identity_source"] == "external_research"
    assert bundle["metadata"]["site_type"] == "mecanismo_de_busca"
    assert bundle["metadata"]["content_assessment"]["status"] == "escasso"
    assert bundle["metadata"]["suggested_questions"] == ["Como funciona o mecanismo de busca?"]
    assert bundle["chunks"][0]["content"] == "Pesquisa Google. Estou com sorte."
    assert captured_research["page_evidence"][0]["title"] == "Google"
    assert captured_research["page_evidence"][0]["content"] == "Pesquisa Google. Estou com sorte."


def test_mapping_keeps_completely_empty_home_as_a_sparse_observation(monkeypatch, tmp_path):
    events = []
    url = "https://empty.example/"
    empty_home = {
        "url": url,
        "title": "Página",
        "h1": "",
        "meta_description": "",
        "score": 0,
        "content": "",
    }
    _patch_mapping_io(monkeypatch, tmp_path, empty_home, events)

    async def inconclusive_research(*_args, **_kwargs):
        events.append("research")
        return None

    monkeypatch.setattr("app.main.research_company_context", inconclusive_research)

    bundle = asyncio.run(map_company_process(url, "empty-home"))

    assert bundle["metadata"]["status"] == "ready"
    assert bundle["metadata"]["content_assessment"]["status"] == "escasso"
    assert bundle["metadata"]["identity_source"] == "inferred"
    assert bundle["chunks"][0]["content"] == (
        "Nenhum conteúdo textual relevante pôde ser extraído da página inicial."
    )


def test_research_seed_urls_only_accept_identity_pages_from_the_same_domain():
    context = {
        "sources": [
            {"url": "https://about.example.com/quem-somos"},
            {"url": "https://cloud.example.com/about"},
            {"url": "https://cloud.example.com/marketplace"},
            {"url": "https://example.com/docs/about"},
            {"url": "https://example.com/produtos"},
            {"url": "https://notexample.com/sobre"},
        ]
    }

    assert get_research_seed_urls(context, "example.com") == [
        "https://about.example.com/quem-somos"
    ]


def test_research_identity_seed_displaces_product_when_page_budget_is_full(monkeypatch, tmp_path):
    events = []
    url = "https://example.com/"
    product_url = "https://example.com/produtos"
    about_url = "https://example.com/about"
    sparse_home = {
        "url": url,
        "title": "Example",
        "h1": "Produtos",
        "meta_description": "",
        "score": 1,
        "content": "Conheça nossos produtos.",
    }
    product_page = {
        "url": product_url,
        "title": "Produtos",
        "h1": "Produtos",
        "meta_description": "",
        "score": 20,
        "content": "Catálogo de produtos, preços e condições de entrega. " * 8,
    }
    _patch_mapping_io(monkeypatch, tmp_path, sparse_home, events)
    monkeypatch.setattr("app.main.MAX_PAGES", 2)

    async def fake_discover(_url, _domain, **_kwargs):
        events.append("crawl")
        return [url, product_url], {"path": "/", "children": {}}

    async def fake_extract(route, _validated_url, _html_cache):
        events.append(f"extract:{route}")
        if route == about_url:
            return {
                "url": about_url,
                "title": "About Example",
                "h1": "Who we are",
                "meta_description": "",
                "score": 12,
                "content": "We are a company founded to build useful software for teams. " * 8,
            }
        return product_page if route == product_url else sparse_home

    async def fake_research(*_args, **_kwargs):
        events.append("research")
        return {
            "entity_name": "Example",
            "site_type": "empresa",
            "summary": "Possível identidade da Example.",
            "confidence": "baixa",
            "sources": [{"url": about_url, "title": "About Example"}],
            "suggested_questions": ["Quem é Example?"],
        }

    monkeypatch.setattr("app.main.discover_routes", fake_discover)
    monkeypatch.setattr("app.main.extract_page_entry", fake_extract)
    monkeypatch.setattr("app.main.research_company_context", fake_research)

    bundle = asyncio.run(map_company_process(url, "full-budget-seed"))

    chunk_urls = {chunk["url"] for chunk in bundle["chunks"]}
    assert about_url in chunk_urls
    assert product_url not in chunk_urls
    assert bundle["metadata"]["identity_source"] == "rag"


def test_research_seed_on_subdomain_uses_that_subdomains_robots(monkeypatch, tmp_path):
    events = []
    robots_calls = []
    url = "https://example.com/"
    seed_url = "https://careers.example.com/about"
    sparse_home = {
        "url": url,
        "title": "Example",
        "h1": "",
        "meta_description": "",
        "score": 0,
        "content": "Produtos.",
    }
    _patch_mapping_io(monkeypatch, tmp_path, sparse_home, events)

    async def fake_robots(base_url):
        robots_calls.append(base_url)
        rules = ["User-agent: *", "Disallow: /about"] if "careers." in base_url else []
        return {"rules": rules, "source": f"{base_url.rstrip('/')}/robots.txt"}

    async def fake_research(*_args, **_kwargs):
        return {
            "entity_name": "Example",
            "site_type": "empresa",
            "summary": "Possível identidade da Example.",
            "confidence": "baixa",
            "sources": [{"url": seed_url, "title": "About Example"}],
            "suggested_questions": ["Quem é Example?"],
        }

    monkeypatch.setattr("app.main.read_robots_txt", fake_robots)
    monkeypatch.setattr("app.main.research_company_context", fake_research)

    bundle = asyncio.run(map_company_process(url, "subdomain-robots"))

    assert robots_calls == [url, "https://careers.example.com/"]
    assert seed_url not in {chunk["url"] for chunk in bundle["chunks"]}
    assert bundle["metadata"]["research_seed_urls"] == 1


def test_research_seed_uses_distinct_robots_for_www_and_bare_origins(monkeypatch, tmp_path):
    events = []
    robots_calls = []
    url = "https://www.example.com/"
    seed_url = "https://example.com/about"
    sparse_home = {
        "url": url,
        "title": "Example",
        "h1": "",
        "meta_description": "",
        "score": 0,
        "content": "Produtos.",
    }
    _patch_mapping_io(monkeypatch, tmp_path, sparse_home, events)

    async def fake_robots(base_url):
        robots_calls.append(base_url)
        rules = ["User-agent: *", "Disallow: /about"] if "www." in base_url else []
        return {"rules": rules, "source": f"{base_url.rstrip('/')}/robots.txt"}

    async def fake_extract(route, _validated_url, _html_cache):
        if route == seed_url:
            return {
                "url": seed_url,
                "title": "About Example",
                "h1": "Who we are",
                "meta_description": "",
                "score": 12,
                "content": "We are a company founded to build useful software for teams. " * 8,
            }
        return sparse_home

    async def fake_research(*_args, **_kwargs):
        return {
            "entity_name": "Example",
            "site_type": "empresa",
            "summary": "Possível identidade da Example.",
            "confidence": "baixa",
            "sources": [{"url": seed_url, "title": "About Example"}],
            "suggested_questions": ["Quem é Example?"],
        }

    monkeypatch.setattr("app.main.read_robots_txt", fake_robots)
    monkeypatch.setattr("app.main.extract_page_entry", fake_extract)
    monkeypatch.setattr("app.main.research_company_context", fake_research)

    bundle = asyncio.run(map_company_process(url, "origin-robots"))

    assert robots_calls == [url, "https://example.com/"]
    assert seed_url in {chunk["url"] for chunk in bundle["chunks"]}


def test_low_confidence_official_about_result_is_crawled_before_being_trusted(monkeypatch, tmp_path):
    events = []
    url = "https://example.com/"
    sparse_home = {
        "url": url,
        "title": "Example",
        "h1": "Produtos",
        "meta_description": "",
        "score": 0,
        "content": "Conheça nossos produtos.",
    }
    _patch_mapping_io(monkeypatch, tmp_path, sparse_home, events)

    async def fake_extract(route, _validated_url, _html_cache):
        events.append(f"extract:{route}")
        if route.endswith("/about"):
            return {
                "url": route,
                "title": "About Example",
                "h1": "Who we are",
                "meta_description": "",
                "score": 12,
                "content": "We are a company founded to build useful software for teams. " * 8,
            }
        return sparse_home

    async def fake_research(*_args, **_kwargs):
        events.append("research")
        return {
            "entity_name": "Example",
            "site_type": "empresa",
            "summary": "Possível identidade da Example.",
            "confidence": "baixa",
            "sources": [{"url": "https://example.com/about", "title": "About Example"}],
            "suggested_questions": ["Quem é Example?"],
        }

    monkeypatch.setattr("app.main.extract_page_entry", fake_extract)
    monkeypatch.setattr("app.main.research_company_context", fake_research)

    bundle = asyncio.run(map_company_process(url, "verify-about"))

    assert "extract:https://example.com/about" in events
    assert bundle["metadata"]["research_seed_urls"] == 1
    assert bundle["metadata"]["identity_source"] == "rag"
    assert bundle["metadata"]["site_type"] is None
    assert bundle["chunks"][0]["url"] == url
    assert any(chunk["url"] == "https://example.com/about" for chunk in bundle["chunks"])
