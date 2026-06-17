"""Message intent and search-query helpers for the TRAK chatbot."""
from __future__ import annotations

import re

from news.chatbot.app_knowledge import (
    is_app_help_message,
    is_security_sensitive_message,
    is_team_about_message,
)

IDENTITY_REPLY = (
    "I was built by the TRAK team to help you explore news inside the TRAK app. "
    "Ask me about headlines, topics, or stories from your TRAK feed."
)

OFF_TOPIC_REPLY = (
    "I'm TRAK AI and I only help with news — headlines, topics, and stories in your TRAK feed. "
    "Ask me about something in the news, for example: \"Summarize tech headlines\" or \"Latest Pakistan news\"."
)

NO_MATCH_REPLY = (
    "I couldn't find articles in TRAK on that topic yet. "
    "Try different keywords or browse your feed for the latest headlines."
)

GREETING_REPLY = (
    "Hi! I'm TRAK AI — I help you explore headlines and stories in your feed. "
    "What news topic would you like to look at?"
)

GREETING_FILLER_TERMS = frozenset(
    """
    there friend pal buddy mate again much very so well too
    fam bro dude bruv innit
    """.split()
)

GREETING_TOKENS = frozenset(
    """
    hi hello hey heya heyy hiya yo yoo yooo sup howdy thanks thank thx ty okay ok
    cool nice great bye goodbye cya hola ola salaam salam assalamu
    wassup wazzup wasup waddup whaddup wagwan wag1 wsg ayo ayoo gm gn
    """.split()
)

_GREETING_SUFFIX = (
    r"(?:\s+(?:fam|bro|man|mate|dude|bruv|innit|there|friend|pal|buddy))*?"
)

# Pure greeting / thanks / goodbye (no news topic bundled in).
GREETING_ONLY_PATTERN = re.compile(
    r"(?i)^(?:"
    r"hi(?: there)?|hello(?: there)?|hey(?: there)?|hiya|yo+|sup|howdy"
    r"|heya+|heyy+"
    r"|wass?up|wazz?up|was+up|wssp"
    r"|waddup|whaddup|wad+up"
    r"|wagwan|wag1|wsg"
    r"|ayo+|ayoo+"
    r"|yooo+|yoo+"
    r"|what'?s\s+good|whats\s+good|wat'?s\s+good"
    r"|thanks?(?:\s+you)?|thank\s+you|thx|ty"
    r"|ok(?:ay)?|cool|nice|great|got\s+it|sounds\s+good"
    r"|bye|goodbye|see\s+ya|see\s+you|cya"
    r"|good\s+(?:morning|afternoon|evening|night)|gm|gn"
    r"|how\s+are\s+you(?:\s+doing)?|how'?s\s+it\s+going|hows\s+it\s+going"
    r"|how\s+(?:you|u|ya)\s+doing"
    r"|what'?s\s+up|whats\s+up|what\s+up"
    r"|hola|ola|sala?am(?:\s+alaikum)?|assalamu(?:\s+alaikum)?|salam(?:\s+alaikum)?"
    r"|peace(?:\s+out)?|later|laters|cheers"
    r")"
    + _GREETING_SUFFIX
    + r"(?:[!?.…,]*\s*)*$"
)

CHAT_STOPWORDS = frozenset(
    """
    a an the is are was were be been being have has had do does did will would could
    should may might can about tell give show what who how when where why which please
    summarize summary latest today todays trak assistant chatbot bot read open find any
    some this that with from for you your me my i we they them it its of on in at to
    and or not no yes
    """.split()
)

# Filler stripped from search, but topic words like "news" / "headlines" are kept when paired.
SEARCH_STOPWORDS = CHAT_STOPWORDS | frozenset(
    """
    breaking trending recent
    """.split()
)

SUMMARIZE_PATTERN = re.compile(
    r"(?i)\b(summarize|summarise|summary|sum up|tldr|tl;dr|recap|brief overview|overview of)\b",
)

HEADLINE_PHRASES = (
    "headline",
    "headlines",
    "top stories",
    "latest stories",
    "recent news",
    "today's news",
    "todays news",
    "news for today",
    "news today",
    "today in news",
    "what's new",
    "whats new",
    "what new",
    "what happened today",
    "what's happening today",
    "whats happening today",
    "catch me up",
    "brief me",
    "morning briefing",
    "evening briefing",
    "daily briefing",
    "trending",
    "breaking news",
)

SIMPLE_EXPLAIN_PATTERN = re.compile(
    r"(?i)\b("
    r"explain\s+(?:simply|in\s+simple\s+terms|like\s+i'?m\s+\d+)"
    r"|eli5|make\s+it\s+simple|simple\s+terms"
    r"|break\s+it\s+down\s+for\s+me"
    r")\b",
)

COMPARE_PATTERN = re.compile(
    r"(?i)\b(compare|comparison|versus|vs\.?|difference\s+between|how\s+do\s+.+\s+compare)\b",
)

IDENTITY_PATTERN = re.compile(
    r"(?i)\b("
    r"who\s+(made|built|created|developed)\s+(you|trak|this|the\s+bot|the\s+assistant)"
    r"|who\s+are\s+you"
    r"|what\s+are\s+you"
    r"|who\s+is\s+trak\s+ai"
    r"|are\s+you\s+(gemini|chatgpt|gpt|google|an?\s+ai\b)"
    r"|your\s+(creator|developer|makers?|team)"
    r"|made\s+by\s+whom"
    r"|which\s+team\s+(built|made|created)"
    r")\b",
)

LINK_SCORE_THRESHOLD = 1.5
STRICT_LINK_SCORE_THRESHOLD = 2.5

# Subject-specific terms: when present, linked articles must match these (not just "pakistan", etc.).
TOPIC_ANCHOR_TERMS = frozenset(
    """
    sports sport cricket football soccer tennis basketball hockey rugby golf boxing mma
    pcb bcci ipl psl nfl nba mlb f1 formula athletics olympics
    tech technology ai software startup silicon chip smartphone cyber
    business economy economic markets stocks finance banking inflation earnings ipo
    politics election government minister parliament senate congress vote referendum
    health medical hospital vaccine disease pandemic who
    science space climate environment energy nuclear
    entertainment movie film music celebrity hollywood bollywood
    war conflict military defense sanctions nato
    """.split()
)

ANCHOR_CATEGORY_ALIASES: dict[str, tuple[str, ...]] = {
    "sports": ("sports", "cricket", "football", "soccer", "tennis", "basketball", "hockey", "athlete"),
    "sport": ("sports", "cricket", "football", "soccer", "tennis", "basketball", "hockey", "athlete"),
    "cricket": ("cricket", "sports", "pcb", "bcci", "ipl", "psl"),
    "pcb": ("cricket", "sports", "pcb"),
    "tech": ("technology", "tech", "software", "ai", "startup"),
    "technology": ("technology", "tech", "software", "ai", "startup"),
    "politics": ("politics", "election", "government", "minister", "parliament"),
    "business": ("business", "economy", "markets", "finance", "stocks"),
    "economy": ("business", "economy", "markets", "finance", "stocks"),
}

CHITCHAT_PATTERN = re.compile(
    r"(?i)^("
    r"hi|hello|hey|heya|heyy|hiya|yo+|sup|howdy"
    r"|wass?up|wazz?up|was+up|waddup|whaddup|wagwan|wag1|wsg|ayo+|yoo+"
    r"|what'?s good|whats good|hola|ola|sala?am|salam|assalamu"
    r"|thanks|thank you|thx|ty"
    r"|ok|okay|cool|nice|great"
    r"|bye|goodbye|good morning|good night|gm|gn"
    r"|how are you|how'?s it going|what'?s up|peace|later|cheers"
    r")\b",
)

# Non-news requests the assistant should decline
OFF_TOPIC_PATTERN = re.compile(
    r"(?i)\b("
    r"write\s+(me\s+)?(an?\s+)?(essay|eassy|essai|paper|assignment|report|thesis|dissertation)"
    r"|draft\s+(me\s+)?(an?\s+)?(essay|paper|assignment)"
    r"|help\s+(me\s+)?(write|with)\s+(my\s+)?(essay|paper|homework|assignment)"
    r"|do\s+my\s+homework|complete\s+my\s+assignment"
    r"|write\s+(me\s+)?(a\s+)?(python|javascript|java|c\+\+|code|script|program|function)"
    r"|debug\s+(my\s+)?code"
    r"|homework|assignment\s+due|essay\s+for\s+class"
    r"|recipe\s+for|how\s+to\s+(cook|bake|make)\s+"
    r"|tell\s+me\s+a\s+joke|write\s+(me\s+)?a\s+poem"
    r"|solve\s+(this|the)\s+math|calculate\s+\d"
    r"|dating\s+advice|relationship\s+advice"
    r"|play\s+a\s+game|video\s+game\s+tips"
    r"|translate\s+this\s+sentence"
    r"|what\s+is\s+\d+\s*[\+\-\*\/]"
    r")\b",
)

OFF_TOPIC_TERMS = frozenset(
    """
    python javascript java code coding script program function debug homework essay
    paper assignment thesis dissertation report draft compose write eassy essai
    recipe cook bake joke poem math calculate dating relationship game translate
    """.split()
)

NEWS_SIGNAL_TERMS = frozenset(
    """
    news headline headlines story stories article articles report breaking trending
    latest today world politics election government minister president economy market
    stock business tech technology sport sports cricket football pakistan india
    war conflict summit interview press media journalist feed trak summarize summary
    ukraine gaza israel climate inflation earnings ipo sanctions nato eu brexit
    """.split()
)

# Common misspellings → corrected token for search.
_TYPO_MAP = {
    "pakisthan": "pakistan",
    "afganistan": "afghanistan",
    "ukrane": "ukraine",
    "technolgy": "technology",
    "summery": "summary",
    "headlineses": "headlines",
    "econmy": "economy",
    "politicans": "politicians",
    "goverment": "government",
}

# Short tokens worth keeping even when filtering filler.
_KEEP_SHORT_TERMS = frozenset({"ai", "uk", "eu", "us", "un", "pm", "gdp", "ipo"})

# Extra search tokens for abbreviations / aliases (query expansion only).
TERM_ALIASES: dict[str, list[str]] = {
    "uk": ["britain", "british"],
    "us": ["america", "american"],
    "usa": ["america", "american"],
    "uae": ["emirates", "dubai"],
    "ai": ["artificial", "intelligence"],
    "tech": ["technology"],
    "crypto": ["bitcoin", "cryptocurrency"],
    "oil": ["petroleum", "energy"],
}

FOLLOW_UP_PATTERN = re.compile(
    r"(?i)\b("
    r"tell me more|more (?:about )?(?:that|this|it)|what else|anything else"
    r"|go on|go deeper|continue|elaborate|expand on that"
    r"|and (?:what about|how about)|same topic|on that topic|about that"
    r"|what (?:happened|about it) there|more on (?:that|this|it)"
    r"|any updates?|latest on (?:that|this|it)"
    r")\b",
)

QUESTION_LEAD_PATTERN = re.compile(
    r"(?i)^(?:what|who|where|when|why|how|is|are|was|were|did|does|can|could|will|would)\s+",
)


def normalize_user_message(message: str) -> str:
    """Collapse whitespace and fix common typos before parsing."""
    text = re.sub(r"\s+", " ", (message or "").strip())
    if not text:
        return ""
    tokens = text.split(" ")
    fixed: list[str] = []
    for tok in tokens:
        low = tok.lower().strip(".,!?;:'\"")
        if low in _TYPO_MAP:
            fixed.append(_TYPO_MAP[low])
        else:
            fixed.append(tok)
    return " ".join(fixed)


def extract_quoted_phrases(message: str) -> list[str]:
    """Quoted strings are kept as phrase search terms."""
    phrases: list[str] = []
    for match in re.finditer(r'"([^"]{2,80})"|\'([^\']{2,80})\'', message or ""):
        phrase = (match.group(1) or match.group(2) or "").strip()
        if phrase and phrase.lower() not in phrases:
            phrases.append(phrase)
    return phrases


def is_follow_up_message(message: str) -> bool:
    return bool(FOLLOW_UP_PATTERN.search((message or "").strip()))


def _last_user_news_message(history: list[dict] | None) -> str:
    """Most recent user turn that looked like a news question."""
    if not history:
        return ""
    for row in reversed(history):
        if row.get("role") != "user":
            continue
        text = normalize_user_message(str(row.get("text") or ""))
        if not text or is_greeting_message(text) or is_off_topic_message(text):
            continue
        if is_follow_up_message(text):
            continue
        return text
    return ""


def resolve_search_message(message: str, history: list[dict] | None = None) -> str:
    """
    Effective text for MongoDB search — expands follow-ups with prior topic.
    The raw user message is still sent to Gemini for natural replies.
    """
    text = normalize_user_message(message)
    if not text:
        return ""
    if not history or not is_follow_up_message(text):
        return text

    prior = _last_user_news_message(history)
    if not prior:
        return text

    new_terms = extract_search_terms(text)
    if new_terms:
        return f"{prior} {' '.join(new_terms)}"
    return prior


def expand_search_terms(terms: list[str]) -> list[str]:
    """Add alias tokens to improve recall (deduped, capped)."""
    out: list[str] = []
    seen: set[str] = set()
    for term in terms:
        for tok in [term, *(TERM_ALIASES.get(term, []))]:
            t = tok.lower().strip()
            if not t or t in seen:
                continue
            seen.add(t)
            out.append(t)
            if len(out) >= 12:
                return out
    return out


def has_news_intent(message: str, *, history: list[dict] | None = None) -> bool:
    """True when the user is plausibly asking about news."""
    text = normalize_user_message(message)
    if not text:
        return False
    if is_security_sensitive_message(text) or is_app_help_message(text) or is_team_about_message(text):
        return False
    if history and is_follow_up_message(text) and _last_user_news_message(history):
        return True
    if is_greeting_message(text):
        return False
    if OFF_TOPIC_PATTERN.search(text):
        return False
    low = text.lower()
    if any(phrase in low for phrase in HEADLINE_PHRASES):
        return True
    if SUMMARIZE_PATTERN.search(text):
        return True
    if any(re.search(rf"\b{re.escape(sig)}\b", low) for sig in NEWS_SIGNAL_TERMS):
        return True
    terms = extract_search_terms(text)
    if not terms:
        return False
    return not all(t in OFF_TOPIC_TERMS for t in terms)


def is_greeting_message(message: str) -> bool:
    """True for hi/thanks/bye-style messages without a news question."""
    text = (message or "").strip()
    if not text:
        return False
    if OFF_TOPIC_PATTERN.search(text):
        return False
    if GREETING_ONLY_PATTERN.match(text):
        return True
    if CHITCHAT_PATTERN.search(text):
        terms = extract_search_terms(text)
        return not terms or all(t in GREETING_FILLER_TERMS for t in terms)
    return False


def get_greeting_reply(message: str) -> str:
    """Static friendly reply for pure greetings (no Gemini)."""
    low = (message or "").strip().lower()
    if re.search(r"\b(thanks?|thank you|thx|ty)\b", low):
        return (
            "You're welcome! Whenever you want headlines or stories from TRAK, "
            "just ask — for example, \"Latest tech news\" or \"Pakistan headlines\"."
        )
    if re.search(r"\b(bye|goodbye|see ya|see you|cya)\b", low) or "good night" in low:
        return "Goodbye! Come back anytime you want to catch up on news in TRAK."
    if "good morning" in low:
        return (
            "Good morning! I can help you catch up on today's headlines in TRAK. "
            "What would you like to read about?"
        )
    if "good afternoon" in low or "good evening" in low:
        return (
            "Hello! Ask me about any topic in your TRAK feed and I'll find matching stories."
        )
    if re.search(
        r"how are you|what'?s up|whats up|how'?s it going|hows it going|how (?:you|u|ya) doing",
        low,
    ):
        return (
            "I'm here and ready to help you explore news in TRAK. "
            "What headlines or topics should we look at?"
        )
    if re.search(
        r"\b(wass?up|wazz?up|wagwan|wag1|wsg|ayo+|yoo+|waddup|whaddup|what'?s good|whats good)\b",
        low,
    ):
        return (
            "Hey! I'm TRAK AI — here for your headlines and stories. "
            "What topic should we dive into?"
        )
    if re.search(r"\b(sala?am|assalamu|salam)\b", low):
        return (
            "Wa alaikum assalam! I can help you explore news in TRAK. "
            "What would you like to read about?"
        )
    if re.search(r"\b(peace(?:\s+out)?|later|laters|cheers)\b", low):
        return "Take care! Come back anytime you want to catch up on news in TRAK."
    if re.search(r"\b(gm|gn|good night)\b", low):
        return (
            "Hey! Whenever you're ready, ask me about headlines or stories in your TRAK feed."
        )
    return GREETING_REPLY


def is_off_topic_message(message: str, *, history: list[dict] | None = None) -> bool:
    """True when the user message is clearly not a news request."""
    text = normalize_user_message(message)
    if not text:
        return False
    if is_security_sensitive_message(text) or is_app_help_message(text) or is_team_about_message(text):
        return False
    if history and is_follow_up_message(text) and _last_user_news_message(history):
        return False
    if is_greeting_message(text):
        return False
    if OFF_TOPIC_PATTERN.search(text):
        return True
    if has_news_intent(text):
        return False

    terms = extract_search_terms(text)
    if SUMMARIZE_PATTERN.search(text):
        if terms and all(t in OFF_TOPIC_TERMS for t in terms):
            return True
        return False
    if not terms:
        return True

    if all(t in OFF_TOPIC_TERMS for t in terms):
        return True
    return False


def classify_empty_result(message: str, *, had_search_hits: bool = False) -> str:
    """
    Pick off_topic vs no_match when no relevant articles remain.
    Non-news → off_topic; genuine news question → no_match.
    """
    if is_off_topic_message(message) or not has_news_intent(message):
        return "off_topic"
    if had_search_hits:
        return "no_match"
    return "no_match" if has_news_intent(message) else "off_topic"


def wants_simple_explanation(message: str) -> bool:
    return bool(SIMPLE_EXPLAIN_PATTERN.search(normalize_user_message(message)))


def wants_comparison(message: str) -> bool:
    return bool(COMPARE_PATTERN.search(normalize_user_message(message)))


def detect_intent(message: str, *, history: list[dict] | None = None) -> str:
    """security_block | app_help | team | identity | greeting | off_topic | summarize | headlines | search"""
    text = normalize_user_message(message)
    if not text:
        return "search"
    if is_security_sensitive_message(text):
        return "security_block"
    if is_app_help_message(text):
        return "app_help"
    if is_team_about_message(text):
        return "team"
    if IDENTITY_PATTERN.search(text):
        return "identity"
    if is_greeting_message(text):
        return "greeting"
    if is_off_topic_message(text):
        return "off_topic"

    if history and is_follow_up_message(text):
        prior = _last_user_news_message(history)
        if prior:
            if SUMMARIZE_PATTERN.search(text) or SUMMARIZE_PATTERN.search(prior):
                return "summarize"
            low_prior = prior.lower()
            if any(phrase in low_prior for phrase in HEADLINE_PHRASES):
                return "headlines"
            return "search"

    if SUMMARIZE_PATTERN.search(text):
        return "summarize"
    low = text.lower()
    if any(phrase in low for phrase in HEADLINE_PHRASES):
        return "headlines"
    return "search"


def extract_anchor_terms(message: str) -> list[str]:
    """Topic-specific terms that must appear in linked articles when present."""
    return [t for t in extract_search_terms(message) if t in TOPIC_ANCHOR_TERMS]


def article_haystack(article: dict) -> str:
    title = str(article.get("title") or "").lower()
    summary = str(article.get("summary") or article.get("excerpt") or "").lower()
    topic_kw = " ".join(str(k) for k in (article.get("topic_keywords") or [])).lower()
    category = str(
        article.get("primary_category") or article.get("category") or ""
    ).lower().replace("-", " ")
    return f"{title} {summary} {topic_kw} {category}"


def article_matches_anchors(article: dict, anchors: list[str]) -> bool:
    if not anchors:
        return True
    hay = article_haystack(article)
    for anchor in anchors:
        if anchor in hay:
            return True
        for alias in ANCHOR_CATEGORY_ALIASES.get(anchor, ()):
            if alias in hay:
                return True
    return False


def filter_relevant_articles(message: str, articles: list[dict]) -> list[dict]:
    """Keep articles that match the user's question."""
    terms = extract_search_terms(message)
    anchors = extract_anchor_terms(message)
    threshold = STRICT_LINK_SCORE_THRESHOLD if anchors else LINK_SCORE_THRESHOLD

    matched = [
        art
        for art in articles
        if article_relevance_score(message, art) >= threshold
        and article_matches_anchors(art, anchors)
    ]
    if matched and len(terms) >= 2:
        all_terms = [
            a
            for a in matched
            if article_matches_terms(a, terms, match="all")
            and article_matches_anchors(a, anchors)
        ]
        if all_terms:
            return all_terms
    if matched:
        return matched

    if anchors:
        anchor_only = [
            art
            for art in articles
            if article_matches_anchors(art, anchors)
            and article_relevance_score(message, art) >= LINK_SCORE_THRESHOLD
        ]
        if anchor_only:
            return anchor_only[:3]

    if (
        articles
        and not anchors
        and has_news_intent(message)
        and not is_off_topic_message(message)
        and article_relevance_score(message, articles[0]) >= LINK_SCORE_THRESHOLD
    ):
        return articles[:3]
    return []


def extract_search_terms(message: str) -> list[str]:
    """Significant tokens for MongoDB search (drops filler words)."""
    text = normalize_user_message(message).lower()
    for phrase in extract_quoted_phrases(message):
        text = text.replace(phrase.lower(), " ")
    raw = re.findall(r"[a-z0-9]+(?:'[a-z]+)?", text)
    terms: list[str] = []
    seen: set[str] = set()
    for tok in raw:
        tok = tok.strip("'")
        if tok in GREETING_TOKENS or tok in SEARCH_STOPWORDS or tok in seen:
            continue
        if len(tok) < 2 and tok not in _KEEP_SHORT_TERMS:
            continue
        seen.add(tok)
        terms.append(tok)
    return terms[:10]


def build_search_query(message: str, *, history: list[dict] | None = None) -> str:
    effective = resolve_search_message(message, history)
    phrases = extract_quoted_phrases(effective)
    terms = expand_search_terms(extract_search_terms(effective))
    parts: list[str] = []
    seen: set[str] = set()
    for phrase in phrases:
        p = phrase.strip()
        key = p.lower()
        if p and key not in seen:
            seen.add(key)
            parts.append(p)
    for term in terms:
        if term not in seen:
            seen.add(term)
            parts.append(term)
    if parts:
        return " ".join(parts[:14])
    return effective.strip()


def article_relevance_score(message: str, article: dict) -> float:
    if not article:
        return 0.0
    q = normalize_user_message(message).lower()
    hay = article_haystack(article)
    title = str(article.get("title") or "").lower()
    score = 0.0
    if q and q in title:
        score += 10.0
    if q and len(q) > 4 and q in hay:
        score += 5.0
    for phrase in extract_quoted_phrases(message):
        pl = phrase.lower()
        if pl in title:
            score += 6.0
        elif pl in hay:
            score += 3.0
    terms = extract_search_terms(message)
    anchors = extract_anchor_terms(message)
    hits = 0
    title_hits = 0
    for term in terms:
        if term in title:
            score += 3.5
            hits += 1
            title_hits += 1
        elif term in hay:
            score += 1.5
            hits += 1
    for anchor in anchors:
        if anchor in title:
            score += 5.0
        elif anchor in hay or any(alias in hay for alias in ANCHOR_CATEGORY_ALIASES.get(anchor, ())):
            score += 3.0
        else:
            score -= 2.0
    if len(terms) >= 2 and hits == len(terms):
        score += 2.5
    if title_hits >= 2:
        score += 1.5
    if QUESTION_LEAD_PATTERN.match(q) and terms and any(t in title for t in terms[:3]):
        score += 1.0
    return score


def should_link_article(message: str, article: dict | None) -> bool:
    if not article:
        return False
    anchors = extract_anchor_terms(message)
    threshold = STRICT_LINK_SCORE_THRESHOLD if anchors else LINK_SCORE_THRESHOLD
    if article_relevance_score(message, article) < threshold:
        return False
    return article_matches_anchors(article, anchors)


def article_matches_terms(article: dict, terms: list[str], *, match: str = "any") -> bool:
    if not terms:
        return True
    hay = article_haystack(article)
    if match == "all":
        return all(t in hay for t in terms)
    return any(t in hay for t in terms)
