import os
import tempfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BASE_DIR / "data" / "companies"
WORK_DIR = Path(os.getenv("COMPANY_RESUME_WORK_DIR", Path(tempfile.gettempdir()) / "company-resume"))

MAX_DEPTH = 2
MAX_PAGES = 30
REQUEST_TIMEOUT = 10
RENDER_TIMEOUT = 20
RENDER_VIRTUAL_TIME_BUDGET_MS = 5000
MIN_PRIMARY_CONTENT_CHARS = 200
CRAWL_DELAY = 1.0
MAX_PAGE_SIZE_MB = 5
TOP_K = 5
MAX_BLOG_ARTICLES = 7
EMBEDDING_DIMENSIONS = 384
USER_AGENT = "CompanyResumeBot/1.0"
ALLOWED_SCHEMES = {"http", "https"}
DISALLOWED_HOST_PATTERNS = (
    "localhost",
    "127.",
    "0.0.0.0",
    "10.",
    "192.168.",
    "169.254.",
    "file://",
    "ftp://",
    "gopher://",
)

RELEVANCE_KEYWORDS = [
    "sobre", "about", "quem somos", "empresa", "história", "historia", "missão", "missao",
    "visão", "visao", "valores", "manifesto", "serviços", "servicos", "services",
    "soluções", "solucoes", "solutions", "produtos", "products", "clientes", "customers",
    "cases", "segmentos", "mercados", "indústrias", "industrias", "tecnologia", "technology",
    "carreiras", "careers", "trabalhe conosco", "blog", "artigos", "insights",
    "transparência", "transparencia", "governança", "governanca", "contato", "contact",
    "ecossistema", "diferenciais", "destaques", "notícias", "noticias", "impacto",
    "sustentabilidade", "educação", "educacao", "responsabilidade", "futuro",
    "institucional", "quem somos", "nossa história", "nossa historia"
]

LOW_RELEVANCE_TOKENS = [
    "login", "logout", "admin", "carrinho", "checkout", "cookies", "search", "busca",
    "feed", "rss", "tag", "author", "categoria", "pagination"
]

BLOCKED_FILE_EXTENSIONS = {
    ".7z", ".avi", ".css", ".doc", ".docx", ".eot", ".exe", ".gif", ".gz",
    ".ico", ".jpeg", ".jpg", ".js", ".json", ".map", ".mov", ".mp3", ".mp4",
    ".pdf", ".png", ".rar", ".svg", ".tar", ".ttf", ".wav", ".webp", ".woff",
    ".woff2", ".xls", ".xlsx", ".xml", ".zip",
}
