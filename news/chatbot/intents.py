"""Message intent and search-query helpers for the TRAK chatbot."""
from __future__ import annotations

import re

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

CHAT_STOPWORDS = frozenset(
    """
    a an the is are was were be been being have has had do does did will would could
    should may might can about tell give show what who how when where why which please
    summarize summary latest news headlines headline today todays trak assistant chatbot
    bot stories story article articles read open find any some this that with from for
    you your me my i we they them it its of on in at to and or not no yes
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
    "what's new",
    "whats new",
    "trending",
    "breaking news",
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

CHITCHAT_PATTERN = re.compile(
    r"(?i)^("
    r"hi|hello|hey|hiya|yo|sup"
    r"|thanks|thank you|thx"
    r"|ok|okay|cool|nice|great"
    r"|bye|goodbye|good morning|good night"
    r"|how are you|what\'?s up"
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
    """.split()
)


def has_news_intent(message: str) -> bool:
    """True when the user is plausibly asking about news."""
    text = (message or "").strip()
    if not text:
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


def is_off_topic_message(message: str) -> bool:
    """True when the user message is clearly not a news request."""
    text = (message or "").strip()
    if not text:
        return False
    if CHITCHAT_PATTERN.search(text):
        return True
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


def detect_intent(message: str) -> str:
    """identity | off_topic | summarize | headlines | search"""
    text = (message or "").strip()
    if not text:
        return "search"
    if IDENTITY_PATTERN.search(text):
        return "identity"
    if is_off_topic_message(text):
        return "off_topic"
    if SUMMARIZE_PATTERN.search(text):
        return "summarize"
    low = text.lower()
    if any(phrase in low for phrase in HEADLINE_PHRASES):
        return "headlines"
    return "search"


def filter_relevant_articles(message: str, articles: list[dict]) -> list[dict]:
    """Keep articles that match the user's question."""
    matched = [
        art
        for art in articles
        if article_relevance_score(message, art) >= LINK_SCORE_THRESHOLD
    ]
    if matched:
        return matched
    # DB search returned rows but strict score failed — keep best only for clear news queries
    if (
        articles
        and has_news_intent(message)
        and not is_off_topic_message(message)
        and article_relevance_score(message, articles[0]) >= LINK_SCORE_THRESHOLD
    ):
        return articles[:3]
    return []


def extract_search_terms(message: str) -> list[str]:
    """Significant tokens for MongoDB search (drops filler words)."""
    raw = re.findall(r"[a-z0-9]+", (message or "").lower())
    terms: list[str] = []
    seen: set[str] = set()
    for tok in raw:
        if tok in CHAT_STOPWORDS or tok in seen:
            continue
        if len(tok) < 2:
            continue
        seen.add(tok)
        terms.append(tok)
    return terms[:8]


def build_search_query(message: str) -> str:
    terms = extract_search_terms(message)
    if terms:
        return " ".join(terms)
    return (message or "").strip()


def article_relevance_score(message: str, article: dict) -> float:
    if not article:
        return 0.0
    q = (message or "").strip().lower()
    title = str(article.get("title") or "").lower()
    summary = str(article.get("summary") or article.get("excerpt") or "").lower()
    hay = f"{title} {summary}"
    score = 0.0
    if q and q in title:
        score += 10.0
    if q and len(q) > 4 and q in hay:
        score += 5.0
    for term in extract_search_terms(message):
        if term in title:
            score += 3.0
        elif term in hay:
            score += 1.5
    return score


def should_link_article(message: str, article: dict | None) -> bool:
    return article_relevance_score(message, article) >= LINK_SCORE_THRESHOLD


def article_matches_terms(article: dict, terms: list[str]) -> bool:
    if not terms:
        return True
    title = str(article.get("title") or "").lower()
    summary = str(article.get("summary") or "").lower()
    topic_kw = " ".join(str(k) for k in (article.get("topic_keywords") or [])).lower()
    hay = f"{title} {summary} {topic_kw}"
    return any(t in hay for t in terms)
