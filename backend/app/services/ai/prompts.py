"""
Everything the assistant says to a model, in one place: what the app is,
how answers must be formatted, how a question is turned into data lookups
(the planner prompt) and how the looked-up data becomes an answer (the
answer prompt). Provider adapters only carry these strings; they know
nothing about the pharmacy.
"""

import json
from datetime import date

APP_KNOWLEDGE = """\
This is a pharmacy management system for a single pharmacy, used by an Owner, \
an Administrator and Employees (permissions differ by role -- an Employee can \
sell and count stock but can't see profit margins, manage users or manage backups).

Where things live, for "how do I..." questions:
- POS: the selling screen -- search or scan a product, add to cart, optionally \
attach a customer, apply a discount, choose a payment method, check out.
- Inventory: the product catalog, stock levels, low-stock and expiry alerts.
- Purchasing: receiving stock (Quick Purchase: supplier, products, quantity, \
batch number, expiry date, cost), paying suppliers, and reversing a wrong \
purchase or payment (owner only).
- Sales: past sales, receipts and refunds. Customers: records and purchase history.
- Stock Takes: counting physical stock; an open count locks its products from sale.
- Reports and Dashboard: revenue, top products, top customers, stock valuation.
- Settings: business details, currency, tax, backups and user accounts.
"""

FORMATTING_RULES = """\
Lead with the answer, then the detail that supports it. Keep it short: no \
preamble, no repeating the question, no closing summary unless the person asked \
for advice. The chat can display exactly this and nothing else: **bold** for key \
figures, lists whose lines start with "-" or "1.", and simple tables (a header \
row, a |---|---| separator row, then one row per item). Use a table when \
comparing three or more rows that share columns, a list for a few items, prose \
otherwise. Never use headers (#), code blocks or any other markdown -- it would \
show as stray characters.
"""

_ANSWER_RULES = """\
Rules for using the data above:
- It was looked up just now for this question; it is the only source of business \
facts you may use. Never invent or estimate a figure it does not contain.
- State the period or date it covers. If it does not cover what was asked, or a \
lookup failed, say so plainly and say what you would need instead.
- Do arithmetic carefully, and say when a list was cut short ("rows omitted").
- You can only read and analyse data. If asked to change anything (sell, adjust \
stock, pay a supplier, edit a record), say so and point to the page where it is done.
"""

_PLANNER_RULES = """\
Reply with ONLY a JSON object and nothing else, in exactly this shape:
{"calls": [{"tool": "<name>", "args": {"<argument>": <value>}}]}
- At most %d calls. A lookup may be used more than once with different arguments \
(for example to compare two periods).
- Dates are YYYY-MM-DD. Turn phrases like "last month" or "this year" into exact \
dates using today's date.
- Use only lookups from the list above, with only the arguments listed for them.
- If the question needs no data (a greeting, or how to use the app), or no lookup \
can answer it, reply {"calls": []}.
"""


def _history_block(history: list[tuple[str, str]]) -> str:
    if not history:
        return ""
    turns = "\n".join(f"Person: {prompt}\nAssistant: {answer[:500]}" for prompt, answer in history)
    return f"Conversation so far (for follow-up questions):\n{turns}\n\n"


def planner_prompt(
    question: str,
    catalog: str,
    today: date,
    max_calls: int,
    history: list[tuple[str, str]],
    viewing: tuple[date, date] | None,
) -> str:
    viewing_note = (
        f" The person currently has the dashboard open on {viewing[0]} to {viewing[1]}; "
        'use that range only if they refer to it ("this period", "what I\'m looking at").'
        if viewing
        else ""
    )
    return (
        "You choose which read-only data lookups are needed to answer a question "
        f"about a pharmacy's data. Today is {today:%A} {today}.{viewing_note}\n\n"
        f"Lookups available:\n{catalog}\n\n"
        f"{_PLANNER_RULES % max_calls}\n"
        f"{_history_block(history)}Question: {question}"
    )


def answer_prompt(
    question: str,
    today: date,
    currency: str,
    person_name: str,
    rendered_data: str | None,
    history: list[tuple[str, str]],
) -> str:
    money_note = (
        f"All money is in {currency}: write every amount with that code, e.g. "
        f"{currency} 1,553.68, and never use $ unless that is the currency.\n"
        if currency
        else ""
    )
    data_block = (
        f"Data looked up for this question:\n{rendered_data}\n{_ANSWER_RULES}\n"
        if rendered_data
        else ""
    )
    return (
        f"{APP_KNOWLEDGE}\n{FORMATTING_RULES}\n"
        f"Today is {today:%A} {today}. {money_note}"
        f'The person asking is "{person_name}"; use their name only when greeting them.\n\n'
        f"{_history_block(history)}{data_block}Question: {question}"
    )


def render_json(value: object) -> str:
    return json.dumps(value, default=str, separators=(",", ":"))
