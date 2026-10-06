"""The tools the harness can run, and the JSON that describes them to the model."""

import json
import os
from datetime import date, timedelta
from urllib.parse import quote

import requests

# Wikipedia is free and needs no key, but throttles requests without a User-Agent that includes a contact URL.
WIKI_API_URL = "https://en.wikipedia.org/w/api.php"
PAGEVIEWS_URL = (
    "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
    "en.wikipedia/all-access/user/{title}/monthly/2015070100/{end}"
)
WIKI_HEADERS = {"User-Agent": "WardrobeAgent/0.1 (https://github.com/SathyaRaman/agentic-project-01)"}


def get_item_pageviews(item: str) -> str:
    """Get monthly Wikipedia pageviews for a clothing item, as a measure of how popular it is over time."""
    try:
        # Find the item's article. Following redirects turns "ballet flats" into "Ballet flat"
        # and "loafers" into "Slip-on shoe".
        page = next(iter(requests.get(
            WIKI_API_URL,
            params={"action": "query", "titles": item, "redirects": 1, "format": "json"},
            headers=WIKI_HEADERS,
            timeout=10,
        ).json()["query"]["pages"].values()))
        if "missing" in page:
            return json.dumps({
                "error": f"Wikipedia has no article for '{item}'.",
                "next_step": "Retry with the plain garment name, dropping modifiers like color or material "
                             "(e.g. 'ballet flats' for 'mesh ballet flats'), or say there's no data on it.",
            })
        title = page["title"]

        # Monthly pageviews from July 2015 up to the last complete month.
        end = (date.today().replace(day=1) - timedelta(days=1)).strftime("%Y%m%d00")
        resp = requests.get(
            PAGEVIEWS_URL.format(title=quote(title.replace(" ", "_"), safe=""), end=end),
            headers=WIKI_HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        items = resp.json()["items"]
    except (requests.RequestException, KeyError) as e:
        # The model cannot see an exception. Return something it can reason about.
        return json.dumps({"error": f"Wikipedia lookup failed: {e}",
                           "next_step": "Answer without popularity data and say it couldn't be checked."})

    return json.dumps({
        "item": item,
        "wikipedia_article": title,
        "monthly_pageviews": {f"{i['timestamp'][:4]}-{i['timestamp'][4:6]}": i["views"] for i in items},
    })


# SerpAPI needs a key (free tier: ~100 searches/month). Get one at https://serpapi.com/manage-api-key.
# Set SERPAPI_KEY in the environment (locally and on Cloud Run); never commit a real key here.
SERPAPI_URL = "https://serpapi.com/search.json"
SERPAPI_KEY = os.environ.get("SERPAPI_KEY", "YOUR_SERPAPI_KEY_HERE")
MAX_SNIPPETS = 10


def get_event_outfit_map(event_type: str, venue: str = "") -> str:
    """Search Google for what to wear to an event and return the top organic results' snippets."""
    if SERPAPI_KEY == "YOUR_SERPAPI_KEY_HERE":
        return json.dumps({
            "error": "Web search unavailable: SERPAPI_KEY is not configured on this server.",
            "next_step": "Answer from general dress-code knowledge and tell the user you couldn't check sources.",
        })

    query = f"what to wear to {event_type} {venue} outfit ideas".replace("  ", " ")
    try:
        results = requests.get(
            SERPAPI_URL,
            params={"engine": "google", "q": query, "api_key": SERPAPI_KEY},
            timeout=45,  # SerpAPI runs a live Google search; uncached queries can take 20s+
        ).json()
    except requests.RequestException:
        # The model cannot see an exception. Return something it can reason about.
        # Don't echo the exception: requests puts the full URL, including api_key, in its message.
        return json.dumps({"error": "Web search is temporarily unavailable.",
                           "next_step": "Answer from general dress-code knowledge and say sources weren't checked."})

    if "error" in results:  # bad key, out of searches, or no results
        return json.dumps({
            "error": f"Web search failed: {results['error']}",
            "next_step": "If there were no results, retry with a more common event_type and no venue. "
                         "Otherwise answer from general dress-code knowledge and say sources weren't checked.",
        })

    snippets = [
        {"source": r.get("source"), "snippet": r["snippet"]}
        for r in results.get("organic_results", []) if r.get("snippet")
    ][:MAX_SNIPPETS]
    if not snippets:
        return json.dumps({
            "error": f"No articles found for '{query}'.",
            "next_step": "Retry with a more common event_type (e.g. 'rooftop party', 'wedding guest', "
                         "'gallery opening') and/or without the venue.",
        })

    return json.dumps({"query": query, "snippets": snippets})


# What to look for in the closet when a role is missing.
CLOSET_HINTS = {
    "top": "a tee, tank, button-up or knit",
    "bottom": "trousers, jeans or a skirt",
    "shoes": "sneakers, flats, boots or loafers",
    "accessory": "jewelry, a bag, a belt, a scarf, sunglasses or a watch",
}
MIN_ACCESSORIES = 3


def check_outfit_completeness(items: list[dict], event: str = "") -> str:
    """Check whether the pieces a user owns make a complete outfit, and list any missing roles."""
    try:
        found = {}
        for item in items:
            found.setdefault(item["role"], []).append(item["name"])
    except (TypeError, KeyError):
        return json.dumps({"error": "Each item needs a 'name' and a 'role'.",
                           "next_step": "Retry with items like {'name': 'dark pants', 'role': 'bottom'}."})
    if not found:
        return json.dumps({"error": "items is empty.",
                           "next_step": "Ask the user which pieces they own for this event, then retry."})
    if "unclear" in found:
        return json.dumps({
            "status": "needs_clarification",
            "event": event,
            "unclear_pieces": found["unclear"],
            "next_step": "Ask the user what each unclear piece is or how they'd wear it (e.g. a 'jumper' could be a "
                         "sweater or a dress), then call this tool again with the full list and a clear role for each.",
        })

    required = ["dress", "shoes"] if "dress" in found else ["top", "bottom", "shoes"]
    missing = [r for r in required if r not in found]
    accessories_needed = max(0, MIN_ACCESSORIES - len(found.get("accessory", [])))
    if accessories_needed:
        missing.append("accessory")

    if missing:
        return json.dumps({
            "status": "search_closet",
            "event": event,
            "have": found,
            "missing_roles": missing,
            "accessories_needed": accessories_needed,
            "closet_hints": {r: CLOSET_HINTS.get(r, "") for r in missing},
            "next_step": "Ask the user to check their closet for each missing role. Only if they confirm they own "
                         "nothing for it, call get_item_pageviews on a plain garment for that role.",
        })
    return json.dumps({
        "status": "complete",
        "event": event,
        "have": found,
        "next_step": "An outfit is possible from what they own. No purchase needed. Suggest a layer if none is listed.",
    })


# What the model sees: the "set notes" in the screenplay.
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_item_pageviews",
            "description": (
                "Get monthly English Wikipedia pageviews for a clothing item or fashion trend since 2015, as a "
                "measure of how much attention it gets over time. Use it to judge whether something is a "
                "microtrend (no history, then a sudden spike that fades) or a staple (steady interest for "
                "years). Note: clothing articles overall lost a lot of readers in 2025-26, so compare an item's "
                "shape over time rather than reading every recent drop as the item dying."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "item": {
                        "type": "string",
                        "description": "Plain garment or trend name, e.g. 'ballet flats', 'loafers', 'cargo pants', "
                                       "'coquette aesthetic'. Drop colors and materials ('ballet flats', not 'mesh "
                                       "ballet flats').",
                    },
                },
                "required": ["item"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_event_outfit_map",
            "description": (
                "Search Google for what to wear to an event (optionally at a specific venue or neighborhood) and "
                "return the top organic results' snippets with their source: fashion sites, social posts, "
                "articles and more. Read across "
                "the snippets for the common basics people wear there, e.g. 'black tank top', 'black ballet "
                "flats', 'slip skirt', 'oversized blazer'. Use whenever the user asks what to wear somewhere, "
                "before judging their wardrobe."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "event_type": {
                        "type": "string",
                        "description": "Kind of event in plain words, e.g. 'rooftop party', 'engagement dinner', "
                                       "'gallery opening', 'wedding guest', 'first date', 'tech job interview'.",
                    },
                    "venue": {
                        "type": "string",
                        "description": "Optional venue, neighborhood or city to localise the search, e.g. "
                                       "'Carbone NYC', 'Williamsburg', 'Whitney Museum'. Leave empty if unknown.",
                    },
                },
                "required": ["event_type"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "check_outfit_completeness",
            "description": (
                "Check whether the pieces the user owns make a complete outfit (dress + shoes, or top + bottom + "
                "shoes, plus at least 3 accessories; layers are optional). Returns 'complete', 'search_closet' "
                "with the missing roles and what to look for in their closet, or 'needs_clarification' when a "
                "piece's role is unclear. Call it every time the user asks about an event or adds pieces."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "description": "The pieces the user owns that could plausibly be worn to this event, drawn from "
                                       "everything they have mentioned owning so far in the conversation (accessories "
                                       "like belts, watches and jewelry carry across events). Leave out pieces that "
                                       "clearly don't suit the event. Don't pick a single outfit yourself; this tool "
                                       "decides whether the pieces make one. Never add items they did not mention.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string", "description": "Garment as the user said it, e.g. 'dark pants'."},
                                "role": {
                                    "type": "string",
                                    "enum": ["top", "bottom", "dress", "layer", "shoes", "accessory", "unclear"],
                                    "description": "The closest part of an outfit this piece plays: 'top', 'bottom', "
                                                   "'dress' (one-piece, e.g. jumpsuit), 'layer' (worn over a top, "
                                                   "e.g. blazer, cardigan, kimono), 'shoes', or 'accessory' (e.g. "
                                                   "bag, jewelry, belt, hat, scarf, tights). Use 'unclear' only if "
                                                   "the piece could plausibly be two roles (e.g. 'jumper': sweater "
                                                   "or dress); the tool will then tell you to ask the user.",
                                },
                            },
                            "required": ["name", "role"],
                        },
                    },
                    "event": {
                        "type": "string",
                        "description": "The event these pieces are for, in plain words, e.g. 'pool party' or "
                                       "'rooftop party'. Always pass it so the result is labeled for the right event.",
                    },
                },
                "required": ["items"],
            },
        },
    },
]

# What the harness runs: tool name -> Python function.
TOOL_MAP = {
    "get_item_pageviews": get_item_pageviews,
    "get_event_outfit_map": get_event_outfit_map,
    "check_outfit_completeness": check_outfit_completeness,
}


def run_tool(name: str, args: dict) -> str:
    """Run one tool call. Models invent tool names and arguments; never let that crash the loop."""
    if name not in TOOL_MAP:
        return json.dumps({"error": f"Unknown tool '{name}'. Available: {list(TOOL_MAP)}"})
    try:
        return TOOL_MAP[name](**args)
    except TypeError as e:
        return json.dumps({"error": f"Bad arguments for {name}: {e}"})
