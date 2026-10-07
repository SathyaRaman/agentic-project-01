"""The tools the harness can run, and the JSON that describes them to the model."""

import json
import os
from datetime import date, timedelta
from urllib.parse import quote

import requests
from cachetools import TTLCache, cached

# No key needed, but Wikipedia rate-limits requests that don't send a User-Agent with contact info.
WIKI_API_URL = "https://en.wikipedia.org/w/api.php"
PAGEVIEWS_URL = (
    "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
    "en.wikipedia/all-access/user/{title}/monthly/2015070100/{end}"
)
WIKI_HEADERS = {"User-Agent": "NopeAgent/0.1 (https://github.com/SathyaRaman/agentic-project-01)"}


# Cache the network tools: the same question often comes up twice in a session, and SerpAPI
# only allows ~100 searches a month. Pageviews only change monthly, so an hour is plenty.
search_cache = TTLCache(maxsize=64, ttl=3600)
pageviews_cache = TTLCache(maxsize=64, ttl=3600)


@cached(pageviews_cache)
def get_item_pageviews(item: str) -> str:
    """Return monthly Wikipedia pageviews for a clothing item."""
    try:
        # Find the article. Redirects handle other names, e.g. "loafers" -> "Slip-on shoe".
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

        # July 2015 through last month (this month isn't over yet).
        end = (date.today().replace(day=1) - timedelta(days=1)).strftime("%Y%m%d00")
        resp = requests.get(
            PAGEVIEWS_URL.format(title=quote(title.replace(" ", "_"), safe=""), end=end),
            headers=WIKI_HEADERS,
            timeout=10,
        )
        resp.raise_for_status()
        items = resp.json()["items"]
    except requests.RequestException:
        return json.dumps({"error": "Wikipedia is temporarily unavailable.",
                           "next_step": "Answer without popularity data and say it couldn't be checked."})
    except (KeyError, StopIteration):
        # Wikipedia answered, but not in the shape we expect - usually an odd or empty item name.
        return json.dumps({"error": f"Wikipedia returned no pageview data for '{item}'.",
                           "next_step": "Retry with a plain garment name (e.g. 'loafers'), or answer without "
                                        "popularity data and say it couldn't be checked."})

    return json.dumps({
        "item": item,
        "wikipedia_article": title,
        "monthly_pageviews": {f"{i['timestamp'][:4]}-{i['timestamp'][4:6]}": i["views"] for i in items},
    })


# SerpAPI needs a key (free tier is ~100 searches a month): https://serpapi.com/manage-api-key
# Set SERPAPI_KEY as an environment variable. Don't commit the real key.
SERPAPI_URL = "https://serpapi.com/search.json"
SERPAPI_KEY = os.environ.get("SERPAPI_KEY", "YOUR_SERPAPI_KEY_HERE")
MAX_SNIPPETS = 10


@cached(search_cache)
def get_event_outfit_map(event_type: str, venue: str = "") -> str:
    """Search Google for what to wear to an event and return the result snippets."""
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
            timeout=45,  # new searches can take 20+ seconds
        ).json()
    except requests.RequestException:
        # Don't include the exception text: it contains the request URL, which has the API key.
        return json.dumps({"error": "Web search is temporarily unavailable.",
                           "next_step": "Answer from general dress-code knowledge and say sources weren't checked."})

    if "error" in results:  # bad key, no searches left, or no results
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


# Ideas for what to look for in the closet when something's missing.
CLOSET_HINTS = {
    "top": "a tee, tank, button-up or knit",
    "bottom": "trousers, jeans or a skirt",
    "shoes": "sneakers, flats, boots or loafers",
    "accessory": "jewelry, a bag, a belt, a scarf, sunglasses or a watch",
}
MIN_ACCESSORIES = 3
# How dressy a piece (or an event) is. A piece below the event's dress code doesn't count
# toward the outfit: jeans and a hoodie don't make a wedding look, however many slots they fill.
FORMALITY = {"casual": 0, "smart": 1, "formal": 2}


def check_outfit_completeness(items: list[dict], event: str = "", dress_code: str = "casual") -> str:
    """Check if the user's pieces make a full outfit and list what's missing."""
    # Only formal events filter, and they only set aside 'casual' pieces: jeans and a hoodie don't
    # make a wedding look, while a tie or a watch is 'smart' and perfectly fine there. Smart events
    # like a rooftop party or a gallery opening are happy with a plain tee and sneakers.
    bar = 1 if dress_code == "formal" else 0
    try:
        found, too_casual = {}, []
        for item in items:
            if FORMALITY.get(item.get("formality", "smart"), 1) < bar:
                too_casual.append(item["name"])
                continue
            found.setdefault(item["role"], []).append(item["name"])
    except (TypeError, KeyError, AttributeError):
        return json.dumps({"error": "Each item needs a 'name' and a 'role'.",
                           "next_step": "Retry with items like {'name': 'dark pants', 'role': 'bottom'}."})
    if not found:
        if too_casual:
            return json.dumps({
                "status": "search_closet", "event": event, "have": {}, "too_casual": too_casual,
                "missing_roles": ["top", "bottom", "shoes", "accessory"],
                "accessories_needed": MIN_ACCESSORIES,
                "closet_hints": {r: CLOSET_HINTS[r] for r in ("top", "bottom", "shoes", "accessory")},
                "next_step": f"Nothing they listed is dressy enough for a {dress_code} event. Tell them which "
                             "pieces won't work and ask what else is in their closet.",
            })
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
            "too_casual": too_casual,
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
        "too_casual": too_casual,
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
                "piece's role is unclear. At a formal event, casual pieces don't count toward the outfit and "
                "come back in 'too_casual'. Call it every time the user asks about an event or adds pieces."
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
                                "formality": {
                                    "type": "string",
                                    "enum": ["casual", "smart", "formal"],
                                    "description": "How dressy the piece is: 'casual' (jeans, hoodie, sneakers, "
                                                   "flip-flops, gym wear), 'smart' (blazer, loafers, slip skirt, "
                                                   "midi dress) or 'formal' (suit, gown, tuxedo, heels). Judge the "
                                                   "piece itself, not the event.",
                                },
                            },
                            "required": ["name", "role", "formality"],
                        },
                    },
                    "event": {
                        "type": "string",
                        "description": "The event these pieces are for, in plain words, e.g. 'pool party' or "
                                       "'rooftop party'. Pass it when the user has named an event. Leave it out if they "
                                       "haven't - don't invent one.",
                    },
                    "dress_code": {
                        "type": "string",
                        "enum": ["casual", "smart", "formal"],
                        "description": "How dressy the event is: 'casual' (brunch, beach, the gym, a pool party), "
                                       "'smart' (rooftop party, gallery opening, dinner, a casual office) or "
                                       "'formal' (wedding, funeral, black tie gala, christening, job interview). "
                                       "At a formal event, pieces marked 'casual' won't count toward the outfit. Pass it "
                                       "when you know how dressy the event is. Leave it out if no event has been "
                                       "named - the tool then just checks the pieces make a full outfit.",
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
        return json.dumps({"error": f"Unknown tool '{name}'. Available: {list(TOOL_MAP)}",
                           "next_step": "Call one of the available tools instead, or answer without one."})
    try:
        return TOOL_MAP[name](**args)
    except TypeError as e:
        return json.dumps({"error": f"Bad arguments for {name}: {e}",
                           "next_step": f"Call {name} again with exactly the arguments its schema lists."})
