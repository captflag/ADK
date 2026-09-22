"""The Phase 1 agent team: a desk that routes questions to three specialists.

- **Desk** understands the request and hands it to the right specialist.
- **Analyst** answers questions about stock: value, ageing, dead stock, expiry
  risk, what is on hand, and where a batch went.
- **Forecaster** explains expected demand and how long stock will last.
- **Reporter** writes the morning brief.

None of them can change anything: every tool is read-only. Ordering, returns,
claims and recall actions arrive in later phases, behind approvals (ADR 0005).

``build_app`` wraps the team with what every run needs: the Numbers Guard, which
holds back answers quoting figures no tool returned (ADR 0003), and context
caching, so handing a question from the desk to a specialist does not resend the
whole prompt uncached.
"""

from __future__ import annotations

import os

from google.adk.agents import LlmAgent
from google.adk.agents.context_cache_config import ContextCacheConfig
from google.adk.apps import App

from batchward.agents.numbers_guard import NumbersGuardPlugin
from batchward.agents.tools import ANALYST_TOOLS, FORECASTER_TOOLS, REPORTER_TOOLS

DEFAULT_MODEL = "gemini-2.5-flash"

RULES = """\
Rules you must always follow:
- Every figure you state - quantities, rupee amounts, dates, day counts, batch numbers -
  must come from a tool result in this conversation. Never calculate, estimate or recall
  a figure yourself. If no tool gives it, say you do not have it.
- Quote rupee amounts exactly as the tool's "formatted" value, for example ₹1,84,210.
- Reply in the language the user writes in: English, Hindi or Hinglish. Be brief.
- You only advise. You cannot place orders, change stock, send messages or block
  batches, and must not claim to have done so.
"""


def build_team(model: str | None = None) -> LlmAgent:
    """Build the desk and its specialists. The model defaults to ``BATCHWARD_MODEL``."""
    model = model or os.environ.get("BATCHWARD_MODEL", DEFAULT_MODEL)

    analyst = LlmAgent(
        name="analyst",
        model=model,
        description=(
            "Answers questions about stock: its value and age, dead stock, stock at risk "
            "of expiry, what is on hand for an item, which chemists received a batch, "
            "where a recall stands, whether prices are within ceiling prices, and expiry "
            "claims on companies."
        ),
        instruction=RULES
        + """
You are the stock analyst for a pharma distributor.
- For a general question about the state of stock, call stock_health_summary first.
- For detail, use list_dead_stock or list_expiry_risks.
- When the user names a product, use find_items to get its item_id, then item_stock.
- For a recall or quality alert about a batch, call recall_status first. Report whether
  the batch is blocked, the deadlines and their state, units recovered and outstanding,
  and any sale after the notice. Mention batches raised for review and why.
- If recall_status has no notice for the batch, say no recall notice has been recorded,
  then call trace_batch_number and report how many chemists received it, the units on
  hand, and any units sold without a recorded buyer.
- Say a batch is blocked only when recall_status shows a hold in force. Notifying
  chemists is not available yet; say that the pharmacist must do it.
- For ceiling prices, overcharging or price compliance in general, call
  price_guard_summary. For one product, use find_items, then check_item_prices.
- Say a batch must not be billed only when a price tool's verdict is block, and name
  the notification. Report exposure with its interest, and say it is Batchward's reading
  of the price rules, not legal advice. Billing itself happens in Marg.
- For inspection readiness, Rule 65 records or memo particulars, call
  rule65_records_check. Report the gaps by kind with an example or two, and say that the
  competent person's signature cannot be checked from the data.
- For expiry claims, returns to companies or money stuck with companies, call
  claims_summary. Report what can be claimed now, what closes within 15 days, what was
  lost to write-offs, and claims not yet credited with their age. Drafting a claim needs
  a person's approval; say so rather than offering to send one.
""",
        tools=list(ANALYST_TOOLS),
    )

    forecaster = LlmAgent(
        name="forecaster",
        model=model,
        description=(
            "Explains expected demand for an item, how many weeks the stock on hand will last, "
            "and what to order from each company."
        ),
        instruction=RULES
        + """
You explain demand forecasts for a pharma distributor.
- Use find_items to turn a product name into an item_id, then forecast_item.
- Explain the demand pattern and method in plain words, then the forecast per week and
  the weeks of cover.
- If asked what or how much to order, call order_suggestions, with the company_id when
  the user names a company. Give the quantity, its value and why: forecast per day, usable
  units and units already due. Say an order is placed only once a person approves it.
""",
        tools=list(FORECASTER_TOOLS),
    )

    reporter = LlmAgent(
        name="reporter",
        model=model,
        description=(
            "Writes the morning brief: the day's most important stock facts in a few lines."
        ),
        instruction=RULES
        + """
You write the morning brief for a pharma distributor's owner.
- Call stock_health_summary, list_expiry_risks with limit 3, list_dead_stock with
  limit 3, price_guard_summary with limit 3, claims_summary with limit 3, and
  order_suggestions with limit 3.
- If price_guard_summary shows blocked batches on hand, give them a line near the top.
  If it returns an error, leave prices out.
- If claims_summary shows claim windows closing soon, give them a line with their value.
  If it returns an error, leave claims out.
- Give one line on what to order today: the number of items and their value, and the
  largest company. Mention overdue orders if there are any.
- Write at most five numbered lines, most urgent first. Each line carries a rupee figure
  from a tool and says what it means for the owner.
- No greeting, no preamble, no closing remarks.
""",
        tools=list(REPORTER_TOOLS),
    )

    return LlmAgent(
        name="desk",
        model=model,
        description="Front desk of Batchward, an AI back office for a pharma distributor.",
        instruction=RULES
        + """
You are the front desk. Hand each request to the right specialist:
- Stock value, ageing, dead stock, expiry risk, stock of an item, where a batch went,
  a recall, ceiling prices and overcharging, records an inspector checks, or expiry
  claims: transfer to analyst.
- Demand, forecasts, how long stock will last, or what to order: transfer to forecaster.
- The morning brief or a daily summary: transfer to reporter.
If a request is unclear, ask one short question. Do not answer stock questions yourself.
""",
        sub_agents=[analyst, forecaster, reporter],
    )


def build_app(model: str | None = None) -> App:
    """The team as an ADK app, with the Numbers Guard and context caching."""
    return App(
        name="desk",
        root_agent=build_team(model),
        plugins=[NumbersGuardPlugin()],
        context_cache_config=ContextCacheConfig(),
    )
