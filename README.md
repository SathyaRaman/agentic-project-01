# NOPE: the anti-shopping agent

### Sathya Raman (sr4213) and Nameera Faisal Akhtar (nf2613)

We have both been there: an event comes up, we decide we have nothing to wear, so we buy something and wear it once. NOPE helps you build the outfit from what you already own. It only suggests buying if something is actually missing, and even then only a lasting staple, bought secondhand.

## Tools

1. **`get_event_outfit_map(event_type, venue)`**: searches Google (via SerpAPI) for what people wear to the
   event and returns article snippets. The agent picks out the basics that keep coming up.
2. **`check_outfit_completeness(items, event)`**: checks whether your pieces make a full outfit (top, bottom
   and shoes, or a dress and shoes, plus 3 accessories). Returns what's missing, or asks when a piece is unclear.
3. **`get_item_pageviews(item)`**: monthly Wikipedia pageviews since 2015. The agent uses the shape of the
   curve to tell a microtrend (sudden spike, no history) from a staple (steady for years).

## Sample queries

1. I have a rooftop party in Williamsburg on Friday. I own dark pants, a few blazers, white sneakers and plain tees. What should I wear?
2. Gallery opening this weekend. I have a black slip skirt, a plain tee, ballet flats, a blazer, a belt, a watch and gold earrings. Do I need to buy anything?
3. Should I buy an infinity scarf?

## Run locally

Needs [uv](https://docs.astral.sh/uv/), the [gcloud CLI](https://cloud.google.com/sdk/docs/install),
and a GCP project with billing and the Agent Platform API enabled.

```bash
git clone https://github.com/SathyaRaman/agentic-project-01.git
cd agentic-project-01
gcloud auth application-default login
gcloud config set project YOUR_PROJECT_ID
export SERPAPI_KEY=your_key   # For SerpAPI; without it, event search is off but the other tools work
uv run app.py
```

Then open http://localhost:8000. `uv run` installs the dependencies on first run.
