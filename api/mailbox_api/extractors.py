from html import unescape
from html.parser import HTMLParser
import re
from urllib.parse import urlparse


CODE_RE = re.compile(r"(?<!\d)(\d{4,8})(?!\d)")
LINK_RE = re.compile(r"https?://[^\s\"'<>]+")
TAG_RE = re.compile(r"<[a-zA-Z][^>]*>")
CODE_PATTERNS = [
    re.compile(
        r"(?:verification\s*(?:code|number)|temporary\s+verification|one[- ]time\s+code|security\s+code|验证码|登录代码)"
        r"[^A-Z0-9]{0,96}([0-9](?:[\s\u200b\u200c\u200d\ufeff-]*[0-9]){3,7})",
        re.I,
    ),
    re.compile(r"\b([A-Z0-9]{2,4})-([A-Z0-9]{2,4})\b", re.I),
    re.compile(r"(?<!\d)(\d{4,8})(?!\d)"),
    re.compile(r"\b(?=[A-Z0-9]{4,8}\b)(?=[A-Z0-9]*\d)([A-Z0-9]{4,8})\b", re.I),
]
STATIC_EXTENSIONS = (
    ".css",
    ".js",
    ".mjs",
    ".map",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
    ".eot",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".svg",
    ".ico",
)
PREFERRED_LINK_MARKERS = (
    "verify",
    "verification",
    "confirm",
    "activate",
    "auth",
    "login",
    "magic",
    "callback",
    "continue",
    "validate",
    "challenge",
    "token",
    "otp",
    "code",
)
CODE_KEYWORDS = (
    "验证码",
    "临时验证码",
    "验证代码",
    "verification code",
    "temporary code",
    "one-time code",
    "security code",
    "login code",
    "auth code",
    "code",
)
BAD_CODE_LINE_MARKERS = (
    "http://",
    "https://",
    ".woff",
    ".css",
    ".js",
    "cdn.",
    "font",
    "asset",
    "style",
)


def extract_code(text: str | None) -> str | None:
    if not text:
        return None
    preferred = preferred_codes(text)
    return preferred[0] if preferred else None


def extract_link(text: str | None) -> str | None:
    if not text:
        return None
    candidates = [_clean_url(match.group(0)) for match in LINK_RE.finditer(text)]
    candidates = [url for url in candidates if _usable_url(url)]
    if not candidates:
        return None
    preferred = next(
        (url for url in candidates if any(marker in url.lower() for marker in PREFERRED_LINK_MARKERS)),
        None,
    )
    return preferred or candidates[0]


def visible_text(text: str | None) -> str:
    if not text:
        return ""
    if not TAG_RE.search(text):
        return unescape(text)
    parser = _VisibleTextParser()
    try:
        parser.feed(text)
        parser.close()
    except Exception:
        return unescape(TAG_RE.sub(" ", text))
    return parser.text()


def preferred_codes(text: str | None) -> list[str]:
    if not text:
        return []
    return _preferred_code_candidates(text, extract_codes(text))


def extract_codes(text: str | None) -> list[str]:
    found: list[str] = []
    searchable = _code_search_text(text or "")
    for pattern_index, pattern in enumerate(CODE_PATTERNS):
        for match in pattern.finditer(searchable):
            if match.lastindex and match.lastindex >= 2 and match.group(2):
                code = f"{match.group(1)}{match.group(2)}".upper()
            else:
                code = match.group(1).upper()
            code = re.sub(r"[^A-Z0-9]", "", code)
            if code in found:
                continue
            if set(code) <= {"0"} or code in {"123456", "000000", "111111", "ABCDEF"}:
                continue
            grouped_alpha_code = (
                pattern_index == 1
                and len(code) == 6
                and code.isalpha()
                and bool(re.search(r"(?:x[.]ai|xai|spacexai)", searchable, re.I))
            )
            if not any(char.isdigit() for char in code) and not grouped_alpha_code:
                continue
            if len(code) == 4 and code[:2] in {"19", "20"}:
                continue
            found.append(code)
    return found


def _code_search_text(text: str) -> str:
    value = str(text or "")
    value = re.sub(r"(?is)<(script|style|head|svg|noscript|template)\b[^>]*>.*?</\1\s*>", " ", value)
    value = re.sub(r"(?i)https?://[^\s\"'<>]+", " ", value)
    value = re.sub(r"(?s)<[^>]+>", " ", value)
    value = unescape(value)
    value = value.replace("\xa0", " ")
    value = re.sub(r"[\u200b\u200c\u200d\ufeff]", "", value)
    value = re.sub(r"=\r?\n", "", value)
    value = re.sub(r"(?<=\d)[\s\u200b\u200c\u200d\ufeff-]+(?=\d)", "", value)
    return value


def _preferred_code_candidates(text: str, codes: list[str]) -> list[str]:
    value = _code_search_text(text)
    lowered = value.casefold()
    if not re.search(
        r"(?:verification|verify|confirmation|confirm|one[- ]time|security\s+code|login\s+code|\bcode\b|otp|验证码|登录代码|确认码|驗證碼|x\.ai)",
        lowered,
        re.I,
    ):
        return []
    normalized = [str(code).replace("-", "").upper() for code in codes]
    is_openai = any(marker in lowered for marker in ("openai", "chatgpt", "tm.openai.com"))
    is_xai = any(marker in lowered for marker in ("x.ai", "space xai", "spacexai"))
    if is_openai:
        numeric = [code for code in normalized if len(code) == 6 and code.isdigit()]
        if numeric:
            return numeric[:1]
    if is_xai:
        mixed = [
            code
            for code in normalized
            if len(code) == 6
            and code.isalnum()
            and any(char.isdigit() for char in code)
            and any(char.isalpha() for char in code)
        ]
        if mixed:
            return mixed[:1]
    return [code for code in normalized if not (code.endswith("PX") and len(code) <= 6)]


def _code_near_keyword(text: str) -> str | None:
    lines = _clean_lines(text)
    for index, line in enumerate(lines):
        if not any(keyword in line.lower() for keyword in CODE_KEYWORDS):
            continue
        window = " ".join(lines[index : index + 4])
        for match in CODE_RE.finditer(window):
            candidate = match.group(1)
            if _code_candidate_ok(candidate, window):
                return candidate
    return None


def _standalone_code(text: str) -> str | None:
    for line in _clean_lines(text):
        compact = re.sub(r"[\s-]+", "", line)
        if re.fullmatch(r"\d{4,8}", compact) and _code_candidate_ok(compact, line):
            return compact
    return None


def _fallback_code(text: str) -> str | None:
    for line in _clean_lines(text):
        for match in CODE_RE.finditer(line):
            candidate = match.group(1)
            if _code_candidate_ok(candidate, line):
                return candidate
    return None


def _clean_lines(text: str) -> list[str]:
    return [line.strip() for line in re.split(r"[\r\n]+", text) if line.strip()]


def _code_candidate_ok(candidate: str, context: str) -> bool:
    if not candidate or len(candidate) < 4:
        return False
    lower = context.lower()
    if any(marker in lower for marker in BAD_CODE_LINE_MARKERS):
        return False
    return True


def _clean_url(raw_url: str) -> str:
    return unescape(raw_url).strip().rstrip(").,;\"'")


def _usable_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    host = parsed.netloc.lower()
    path = parsed.path.lower()
    if _is_openai_tracking_url(host, path):
        return False
    if path.endswith(STATIC_EXTENSIONS):
        return False
    if any(part in path for part in ("/fonts/", "/assets/", "/static/")) and not any(
        marker in path for marker in PREFERRED_LINK_MARKERS
    ):
        return False
    return True


def _is_openai_tracking_url(host: str, path: str) -> bool:
    if host != "email.openai.com" and not host.endswith(".email.openai.com"):
        return False
    normalized_path = path.rstrip("/")
    return normalized_path in {"/wf/open", "/ls/click"} or normalized_path.startswith("/wf/") or normalized_path.startswith("/ls/")


class _VisibleTextParser(HTMLParser):
    skip_tags = {"style", "script", "head", "svg", "noscript", "template"}
    block_tags = {
        "address",
        "article",
        "aside",
        "br",
        "div",
        "footer",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "header",
        "li",
        "main",
        "p",
        "section",
        "table",
        "td",
        "th",
        "tr",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        if tag in self.skip_tags:
            self._skip_depth += 1
        if tag in self.block_tags:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self.skip_tags and self._skip_depth:
            self._skip_depth -= 1
        if tag in self.block_tags:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if data.strip():
            self._parts.append(data)

    def text(self) -> str:
        return unescape(" ".join(self._parts))
