import json
import os
import uuid
from datetime import date
from pathlib import Path

import litellm
import uvicorn
from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel

from tools import TOOLS, run_tool

# --- Config ---

SYSTEM_PROMPT = f"""You are NOPE, a wardrobe-first styling assistant. People come to you with an event and the \
feeling that they have nothing to wear; you help them dress from what they already own. \
Today is {date.today():%B %d, %Y}.

Your tools:
- get_event_outfit_map(event_type, venue): searches Google for magazine and article advice on what to wear \
to that kind of event (and venue, if given) and returns the article snippets with their source. Read across \
the snippets for the common basics that keep coming up (e.g. "black tank top", "black ballet flats") - \
pieces people likely already own.
- get_item_pageviews(item): monthly Wikipedia pageviews for a garment or trend since 2015, a measure of \
how much attention it gets over time. Read the shape of the series:
  - Microtrend: little or no history (the data starts recently or sits near zero for years), then a sharp \
spike that is already fading. Not worth buying.
  - Staple: steady interest for years, even if there was a recent spike. Worth owning; find it secondhand.
  - Clothing articles in general lost a lot of readers in 2025-26, so don't read a recent drop alone as the \
item dying; compare its shape to its own history.
- check_outfit_completeness(items): checks whether the pieces the user owns make a full outfit (dress + shoes, \
or top + bottom + shoes, plus at least 3 accessories). Returns 'complete', or 'search_closet' with the missing \
roles, how many accessories are still needed, and what to look for. \
Label each piece with its role.

How to work:
- When the user asks what to wear somewhere, call get_event_outfit_map before giving advice. Use plain event \
types ('wedding guest', not 'wedding'; 'gallery opening', 'rooftop party') and pass the venue or \
neighborhood if they mention one.
- Don't call a tool again for something you already looked up in this conversation; reuse the result.
- Always call get_item_pageviews before giving any opinion on whether an item is worth buying, trendy, or \
still in style, and whenever you find a genuine gap in their wardrobe. Never judge popularity from memory. \
Use the plain garment name (drop colors and materials: 'ballet flats', not 'mesh black ballet flats').
- If a tool returns an error, follow its next_step (retry with a simpler name or without the venue, \
or answer without that data and say what couldn't be checked).
- If you don't know what the user owns yet, ask briefly before building an outfit.
- Once you know what the user owns, call check_outfit_completeness with their whole list of pieces (don't \
pick an outfit yourself), matching each to the closest role, or 'unclear' if it could plausibly be two roles. \
If it returns 'needs_clarification', ask the user about the unclear pieces, then call it again. \
If it returns 'search_closet', ask them to \
look through their closet for each missing role before suggesting any purchase.

How to answer:
- Build the outfit from what the user owns first. If something is close but not quite right, show how to \
restyle it (tuck, layer, swap) before suggesting anything else.
- Only when there is a genuine gap, name the missing piece and check it with get_item_pageviews. \
Microtrend: talk them out of it and offer a workaround from their closet. Staple: say it's worth owning and \
point them to secondhand. Never recommend buying new.
- Cite the evidence briefly (e.g. "Vogue's and Who What Wear's guides both lean on loafers"; \
"ballet flats have had steady interest since 2015, with a spike in early 2025").
- Keep it under 150 words. Be warm, candid and a little witty, like a friend with good taste.
- Remember what the user told you earlier in the conversation (their wardrobe, events, budget, style) and use it.
"""
MAX_TOOL_ROUNDS = 8

# --- The Harness ---


def run_agent(messages: list[dict]) -> tuple[str, list[dict]]:
    """Complete until the model answers without asking for a tool.

    Returns the final text and a record of every tool call made along the way.
    """
    tool_calls = []

    for _ in range(MAX_TOOL_ROUNDS):
        reply = litellm.completion(
            model="vertex_ai/gemini-3.5-flash-lite",
            vertex_location="global",
            messages=messages,
            tools=TOOLS,
        ).choices[0].message

        # Append assistant's reply (text, tool calls, or both) to the context.
        # model_dump() keeps it a plain dict: the raw object carries provider-specific
        # fields that trip Pydantic when LiteLLM re-serializes it next round.
        messages += [reply.model_dump()]

        if not reply.tool_calls:
            return reply.content, tool_calls

        # The harness, not the model, runs each tool and appends the result
        for call in reply.tool_calls:
            args = json.loads(call.function.arguments)
            result = run_tool(call.function.name, args)
            tool_calls += [{"name": call.function.name, "args": args, "result": result}]

            messages += [{"role": "tool", "tool_call_id": call.id, "content": result}]

    return "Sorry, I hit my tool-call limit before finishing.", tool_calls


# --- Session Store ---

# session_id -> list of messages. In-memory, single process.
sessions: dict[str, list] = {}

# --- FastAPI App ---

app = FastAPI()


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None


class ChatResponse(BaseModel):
    response: str
    session_id: str
    tool_calls: list[dict]


@app.get("/")
def index():
    return FileResponse(Path(__file__).parent / "index.html")


@app.post("/chat", response_model=ChatResponse)
def chat(request: ChatRequest):
    # Get or create the session
    session_id = request.session_id or str(uuid.uuid4())
    if session_id not in sessions:
        sessions[session_id] = [{"role": "system", "content": SYSTEM_PROMPT}]

    # Append user's message to the context
    sessions[session_id] += [{"role": "user", "content": request.message}]

    try:
        response, tool_calls = run_agent(sessions[session_id])
    except Exception as e:
        # Auth, billing, a model that is not running: show it in the chat, not as a 500.
        response, tool_calls = f"Model call failed: {type(e).__name__}: {str(e)[:300]}", []

    return ChatResponse(response=response, session_id=session_id, tool_calls=tool_calls)


@app.post("/clear")
def clear(session_id: str | None = None):
    sessions.pop(session_id, None)
    return {"status": "ok"}


if __name__ == "__main__":
    # Cloud Run sets PORT and needs 0.0.0.0; locally this stays on 127.0.0.1:8000.
    port = os.environ.get("PORT")
    uvicorn.run(app, host="0.0.0.0" if port else "127.0.0.1", port=int(port or 8000))
