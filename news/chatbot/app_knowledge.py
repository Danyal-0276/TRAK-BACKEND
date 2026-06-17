"""Public TRAK product knowledge for in-app help, team info, and security guardrails."""
from __future__ import annotations

import re

# Mirrors TRAK/src/route/AboutScreen/aboutContent.js (keep in sync manually).
ABOUT_TAGLINE = "News that follows you — not the other way around."
ABOUT_INTRO = (
    "TRAK aggregates stories from trusted sources, scores credibility, "
    "and surfaces what matters through personalized feeds, Trend Radar, and smart categories."
)

TEAM_MEMBERS = [
    {"name": "Shahroz Butt", "role": "Lead Developer"},
    {"name": "Danyal", "role": "Developer"},
    {"name": "Abdullah", "role": "Developer"},
]

APP_FEATURES = [
    ("For You feed", "Personalized headlines based on your interests, keywords, and reading habits."),
    ("Trend Radar", "Live topic signals on Discover so you spot what is heating up."),
    ("Source trust", "Credibility cues help you judge stories before you read."),
    ("Explore & categories", "Browse by topic, search the catalog, and save bookmarks."),
    ("TRAK AI", "Ask about headlines and get quick context inside the app (this chat)."),
    ("One account", "Sign in on web or mobile — saves and preferences sync."),
    ("Keyword alerts", "Save keywords in Settings to get notified when matching articles arrive."),
    ("Notifications", "Keyword matches and updates appear in your Notifications tab."),
]

HELP_TOPICS: list[dict] = [
    {
        "id": "bookmarks",
        "patterns": re.compile(
            r"(?i)\b(bookmark|bookmarks|saved\s+articles?|save\s+(?:an?\s+)?article)\b"
        ),
        "reply": (
            "To bookmark an article, open it from your feed or search results and tap the bookmark icon. "
            "Open Bookmarks from your profile or library area to see everything you saved. "
            "Bookmarks sync when you are signed in on web and mobile."
        ),
    },
    {
        "id": "keywords",
        "patterns": re.compile(
            r"(?i)\b(keyword\s+alert|keyword\s+match|add\s+keywords?|saved\s+keywords?|"
            r"topic\s+alert|notify\s+(?:me\s+)?when)\b"
        ),
        "reply": (
            "Go to Settings → Keywords and add words or phrases you care about. "
            "When a new article matches, TRAK sends an alert in Notifications. "
            "You can tap a notification to open the article directly."
        ),
    },
    {
        "id": "categories",
        "patterns": re.compile(
            r"(?i)\b(categor(y|ies)|topics?|interests?|tags?|for\s+you\s+feed|personaliz)\b"
        ),
        "reply": (
            "Open Settings → Categories / Topics and pick news areas you follow. "
            "Your For You home feed uses those interests plus your keywords. "
            "You can change categories anytime — pull down on the feed to refresh."
        ),
    },
    {
        "id": "notifications",
        "patterns": re.compile(
            r"(?i)\b(notifications?|alerts?|push\s+notifications?|bell\s+icon)\b"
        ),
        "reply": (
            "The Notifications tab shows keyword matches and other alerts. "
            "Enable push notifications in Settings on mobile if you want alerts on your device. "
            "Tap a keyword alert to jump straight to the article."
        ),
    },
    {
        "id": "discover",
        "patterns": re.compile(
            r"(?i)\b(discover|explore|search|trend\s+radar|browse\s+categories)\b"
        ),
        "reply": (
            "Use Discover / Search to explore all articles, filter by category, and see Trend Radar signals. "
            "Search works across titles and topics. Category browse helps when you want a specific beat (tech, sports, etc.)."
        ),
    },
    {
        "id": "chat",
        "patterns": re.compile(
            r"(?i)\b(trak\s+ai|this\s+chat|chatbot|how\s+(?:do\s+i|to)\s+(?:use|ask)\s+(?:you|ai))\b"
        ),
        "reply": (
            "I'm TRAK AI. Ask me about headlines, topics, or stories in TRAK — for example "
            "\"Latest Pakistan news\" or \"Summarize tech headlines\". "
            "Use the sidebar (panel icon) for past chats. I can also explain how TRAK features work."
        ),
    },
    {
        "id": "account",
        "patterns": re.compile(
            r"(?i)\b(sign\s+in|log\s+in|account|sync|profile|settings)\b"
        ),
        "reply": (
            "Sign in with your TRAK account on web or mobile — bookmarks, keywords, and preferences follow you. "
            "Open Settings from your profile for theme, notifications, keywords, and categories. "
            "Visit About for version info and the team."
        ),
    },
    {
        "id": "web",
        "patterns": re.compile(
            r"(?i)\b(website|web\s+app|browser|desktop)\b"
        ),
        "reply": (
            "On web, browse the home feed, Discover, bookmarks, notifications, and settings — "
            "same account as mobile. The TRAK AI floating button opens this assistant. "
            "Article pages open in-app with listen/share options where available."
        ),
    },
    {
        "id": "mobile",
        "patterns": re.compile(
            r"(?i)\b(mobile\s+app|android|iphone|ios|phone\s+app|bottom\s+(?:nav|tabs?))\b"
        ),
        "reply": (
            "On mobile, use the bottom tabs: Home, Discover, Chat, Notifications, and Profile. "
            "Pull down on feeds to refresh. Settings, keywords, and categories live under Profile → Settings. "
            "Scroll feeds to hide the header; tap Chat for TRAK AI."
        ),
    },
    {
        "id": "about",
        "patterns": re.compile(
            r"(?i)\b(about\s+(?:page|screen|trak)|app\s+version|what\s+is\s+trak)\b"
        ),
        "reply": (
            f"Open About from your profile to see TRAK version info, platform details, and the dev team. "
            f"{ABOUT_TAGLINE} {ABOUT_INTRO}"
        ),
    },
    {
        "id": "theme",
        "patterns": re.compile(
            r"(?i)\b(dark\s+mode|light\s+mode|theme|appearance)\b"
        ),
        "reply": (
            "Go to Settings and choose your theme (light or dark). "
            "Your choice applies across the app on that device."
        ),
    },
    {
        "id": "listen",
        "patterns": re.compile(
            r"(?i)\b(listen\s+to\s+articles?|text\s+to\s+speech|tts|read\s+aloud|audio)\b"
        ),
        "reply": (
            "On supported article pages, use the listen option to hear the story read aloud. "
            "Look for the audio control on the article detail screen in web or mobile."
        ),
    },
    {
        "id": "feedback",
        "patterns": re.compile(
            r"(?i)\b(report\s+article|feedback|bug\s+report|something\s+wrong)\b"
        ),
        "reply": (
            "To report an issue with an article, use the report option on the article page. "
            "For app feedback, check Settings or the About page for how to reach the TRAK team."
        ),
    },
]

SECURITY_BLOCK_PATTERN = re.compile(
    r"(?i)\b("
    r"api\s+key|apikey|secret\s+key|access\s+token|refresh\s+token|bearer\s+token"
    r"|\.env|environment\s+variable|firebase\s+config|service\s+account"
    r"|mongodb|mongo\s+uri|database\s+password|db\s+credentials|connection\s+string"
    r"|django\s+secret|jwt\s+secret|admin\s+password|root\s+password"
    r"|backend\s+code|source\s+code|server\s+code|scraper\s+code|internal\s+api"
    r"|/api/|api\s+endpoint|rest\s+endpoint|graphql|webhook\s+url"
    r"|gemini\s+key|openai\s+key|llm\s+prompt|system\s+prompt|your\s+instructions"
    r"|bypass\s+auth|hack|exploit|vulnerability|sql\s+injection|pen\s+test"
    r"|curl\s+.+/api|fetch\s+.+/api|postman|swagger|openapi"
    r"|how\s+(?:is|are)\s+(?:you|trak)\s+(?:built|deployed|hosted)"
    r"|what\s+(?:stack|framework|database)\s+(?:do\s+you|does\s+trak)\s+use"
    r"|show\s+me\s+(?:the\s+)?(?:api|backend|server|infra)"
    r")\b",
)

APP_HELP_PATTERN = re.compile(
    r"(?i)\b("
    r"how\s+(?:do\s+i|to)\s+(?:use|navigate|find|enable|set\s+up|get\s+started\s+with)\s+"
    r"(?:trak|the\s+trak\s+app|the\s+app|this\s+app|mobile|website|web)"
    r"|how\s+(?:do\s+i|to)\s+(?:bookmark|save|subscribe|follow|customize)"
    r"|(?:user\s+)?manual|app\s+help|help\s+(?:me\s+)?(?:use|with)\s+(?:trak|the\s+app|mobile|features?)"
    r"|what\s+(?:does|can)\s+trak\s+(?:do|offer|have)"
    r"|what\s+features?\s+(?:does\s+)?trak\s+have"
    r"|where\s+(?:is|are|do\s+i\s+find)\s+(?:settings|notifications|bookmarks|keywords|categories|about)"
    r"|how\s+(?:does|do)\s+(?:for\s+you|trend\s+radar|notifications?|bookmarks?)\s+work"
    r"|getting\s+started\s+with\s+trak"
    r"|explain\s+(?:the\s+)?(?:app|features?|mobile\s+app|website)"
    r")\b",
)

TEAM_PATTERN = re.compile(
    r"(?i)\b("
    r"who\s+(?:are|is)\s+(?:the\s+)?(?:developers?|dev\s+team|engineering\s+team|makers?|creators?)"
    r"|(?:about\s+(?:page|the\s+team|trak\s+team))"
    r"|(?:meet\s+)?(?:the\s+)?team"
    r"|lead\s+developer"
    r"|shahroz|danyal|abdullah"
    r"|who\s+(?:built|created|developed|made)\s+(?:the\s+)?trak(?:\s+(?:app|application))?(?!\s+ai\b)"
    r"|who\s+works\s+on\s+trak"
    r")\b",
)

SECURITY_REPLY = (
    "I can't share internal technical details — things like API keys, backend code, database access, "
    "or infrastructure. I'm TRAK AI for news and in-app help only. "
    "If you found a security concern, please reach out to the TRAK team through the About page."
)

APP_HELP_GENERIC = (
    f"TRAK is your credibility-first news app on web and mobile. {ABOUT_INTRO} "
    "Main areas: Home (For You), Discover / Trend Radar, Bookmarks, Notifications, "
    "Settings (keywords and categories), and TRAK AI (this chat). "
    "Tell me what you want to do — e.g. bookmarks, keyword alerts, or notifications — and I'll walk you through it."
)

TEAM_REPLY = (
    "TRAK is built by a small dev team you can see on the About page: "
    + ", ".join(f"{m['name']} ({m['role']})" for m in TEAM_MEMBERS)
    + f". {ABOUT_TAGLINE} I'm TRAK AI — their in-app assistant for news and product help."
)


def is_security_sensitive_message(message: str) -> bool:
    text = (message or "").strip()
    if not text:
        return False
    return bool(SECURITY_BLOCK_PATTERN.search(text))


def is_app_help_message(message: str) -> bool:
    text = (message or "").strip()
    if not text:
        return False
    if is_security_sensitive_message(text):
        return False
    if re.search(
        r"(?i)\bwho\s+(?:made|built|created|developed)\s+(?:you|trak\b|this|the\s+bot|the\s+assistant)\b",
        text,
    ):
        return False
    if APP_HELP_PATTERN.search(text):
        return True
    return match_app_help_topic(text) is not None


def is_team_about_message(message: str) -> bool:
    text = (message or "").strip()
    if not text:
        return False
    if is_security_sensitive_message(text):
        return False
    return bool(TEAM_PATTERN.search(text))


def match_app_help_topic(message: str) -> str | None:
    text = (message or "").strip()
    if not text:
        return None
    for topic in HELP_TOPICS:
        if topic["patterns"].search(text):
            return topic["reply"]
    return None


def get_security_reply() -> str:
    return SECURITY_REPLY


def get_app_help_reply(message: str) -> str:
    specific = match_app_help_topic(message)
    if specific:
        return specific
    return APP_HELP_GENERIC


def get_team_reply() -> str:
    return TEAM_REPLY


def build_app_knowledge_context() -> str:
    """Compact facts for Gemini grounding on help/team turns."""
    features = "\n".join(f"- {title}: {desc}" for title, desc in APP_FEATURES)
    team = ", ".join(f"{m['name']} ({m['role']})" for m in TEAM_MEMBERS)
    return (
        f"TRAK tagline: {ABOUT_TAGLINE}\n"
        f"Overview: {ABOUT_INTRO}\n"
        f"Features:\n{features}\n"
        f"Team (public About page): {team}\n"
        "Platforms: web app and mobile app share one account.\n"
        "Do NOT disclose API keys, backend stack, database, scraper internals, or credentials."
    )
