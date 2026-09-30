import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

from . import userconfig  # noqa: E402


def require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise SystemExit(f"Missing {name} in .env (see .env.example)")
    return value


# ---- the signed-in person's files (users/<Name>/; see runner/userconfig.py) -------------------
# Empty until activate(): the runner calls it right after signing in, the server per request.
USER: userconfig.UserConfig | None = None
HOME: Path | None = None
PROFILE_PATH: Path | None = None
COVER_LETTER: Path | None = None
RESUMES: dict[str, Path] = {}  # key -> PDF that gets uploaded
RESUME_TEX: dict[str, Path] = {}  # key -> LaTeX source (= tailorable)
RESUME_LABELS: dict[str, str] = {}
RESUME_FOCUS: dict[str, str] = {}
RESUME_TITLE_KEYWORDS: dict[str, list[str]] = {}
PRIMARY_TEX: Path | None = None  # the first LaTeX resume supplies official school names for education entries
TAILORED_DIR = ROOT / "out" / "tailored"


def activate(owner_id: str, required: bool = True) -> userconfig.UserConfig | None:
    """Point every setting above at this person's folder."""
    global USER, HOME, PROFILE_PATH, COVER_LETTER, RESUMES, RESUME_TEX, RESUME_LABELS, RESUME_FOCUS
    global RESUME_TITLE_KEYWORDS, PRIMARY_TEX, TAILORED_DIR
    cfg = userconfig.load(owner_id)
    if cfg is None:
        if required:
            raise SystemExit("This account has no files on this computer yet: open the dashboard (start.bat) and "
                             "finish the setup screens first.")
        return None
    USER, HOME, PROFILE_PATH, COVER_LETTER = cfg, cfg.folder, cfg.profile_path, cfg.cover_letter
    RESUMES = {r.key: r.pdf for r in cfg.resumes}
    RESUME_TEX = {r.key: r.tex for r in cfg.resumes if r.tex and r.tex.exists()}
    RESUME_LABELS = {r.key: r.label for r in cfg.resumes}
    RESUME_FOCUS = {r.key: r.focus for r in cfg.resumes}
    RESUME_TITLE_KEYWORDS = {r.key: r.title_keywords for r in cfg.resumes}
    PRIMARY_TEX = next(iter(RESUME_TEX.values()), None)
    TAILORED_DIR = cfg.tailored_dir
    return cfg


RESUME_MATCH_THRESHOLD = float(os.getenv("RESUME_MATCH_THRESHOLD", "0.80"))

OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

_DEFAULT_MODELS = {"fast": "deepseek/deepseek-v4.1-flash", "write": "openai/gpt-6-luna"}


def model(kind: str) -> str:
    """kind: 'fast' or 'write'. Model IDs come from .env (OpenRouter slugs, e.g. 'vendor/model')."""
    return os.getenv(f"LLM_MODEL_{kind.upper()}") or _DEFAULT_MODELS[kind]
