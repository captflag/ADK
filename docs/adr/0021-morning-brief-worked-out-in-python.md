# ADR 0021: The morning brief is worked out in Python and sent on WhatsApp

- **Status:** Accepted
- **Date:** 2026-09-22

## Context

The blueprint promises the owner a morning brief: the day's decisions on
WhatsApp, each with a rupee figure and a way to say yes. The reporter agent
already wrote one from six tools, choosing which five facts came first. That
choice was the model's. Choosing what matters most is a judgment an owner must
be able to check and rely on, and ADR 0003 keeps judgments with figures in them
out of the model. The brief also needed Gemini to exist at all, and reached
nobody: it was an answer in a chat window, not a message on the owner's phone.

Everything the brief reports is already worked out by an analysis: where a
recall stands (ADR 0004), batches the price guard blocks (ADR 0011), claim
windows (ADR 0018), what to order and what is overdue (ADR 0020), credit owed
on claims, expiry risk, dead stock and past overcharges. WhatsApp takes a
free-form message only within 24 hours of the person last writing to the
business number, and otherwise only a template Meta has approved (ADR 0019).

## Decision

- The brief is built in Python from those analyses. Each topic with something
  to report gives one line with a rupee figure, and lines follow a fixed order
  of urgency: what the law requires, then money with a date on it, then today's
  buying, then money owed, money at risk, money idle and past exposure:
  1. recalls with units still with chemists, valued at cost, and any batch a
     notice names that is not blocked;
  2. batches on hand priced above their ceiling, which must not be billed;
  3. claim windows closing within 15 days, as credit;
  4. what to order today at last purchase rates, and orders over 30 days old
     still waiting for units;
  5. credit companies still owe on claims made;
  6. stock that will expire before it sells, and how much can be claimed now;
  7. dead stock;
  8. past sales above the allowed price, with interest.
- The first five lines are given in full. Topics after them are named in one
  line with their figures, so nothing is hidden, only shortened.
- Requests waiting for approval follow, oldest first, each with its number,
  its kind, how long it has waited and its summary, which carries its value.
- Without a records database the brief is still worked out, and says which
  topics it could not check and why, as it does when no return terms are on
  record.
- `batchward brief` prints the brief. With `--send` it is kept in the records
  database (ADR 0010), in a ninth migration, and sent to every approver: as a
  text message to anyone who wrote to the business number in the last 24 hours,
  and otherwise as an approved template (`WHATSAPP_BRIEF_TEMPLATE`). The
  template carries the day and a one-line headline of the topics and their
  figures, and a button asking for the brief. Tapping it, or replying `brief`,
  gets the latest brief kept, in full. Without a template, anyone outside the
  24 hours is not sent the brief, and the command says why.
- After the brief, each request it lists waiting is sent again with its
  Approve and Reject buttons (ADR 0019). A decision in the brief is one tap
  away.
- When each approver last wrote comes from the messages the webhook recorded.
  The same rule now holds for approval requests: without an approval template,
  a request is not sent to an approver outside the 24 hours.
- The reporter agent calls a `morning_brief` tool and gives its lines in the
  order returned, in the user's language, with every figure as written.
- Batchward does not wake itself. The brief is sent by running the command each
  morning from Task Scheduler or cron.

## Consequences

- The brief an owner receives is the same whichever way it is made, and the
  same facts in the same order would give the same brief tomorrow. A person can
  read why a line is where it is.
- What the owner was told on each day is on record.
- Nothing about the brief needs a model or an API key; WhatsApp needs Meta's
  settings in `.env`.
- The order of urgency is fixed. A large dead stock figure never outranks a
  small recall, by design. If owners want a different order, it is changed here
  and in code, for everyone, not by a prompt.
- The brief's template must be approved by Meta, and its wording is the
  stockist's. Template messages outside the 24 hours are billed by Meta.
- A person who wrote to the business number while the webhook was not running
  is not known to have written, and is treated as outside the window. Nothing
  is claimed as sent that WhatsApp would drop.

## Alternatives rejected

- **Letting the model choose and order the lines.** The ranking is itself a
  judgment an owner acts on; it belongs in code, where it can be read and tested
  (ADR 0003).
- **Ranking lines by rupee value alone.** A recall or a price block is urgent
  whatever its value; money comparisons across topics mean different things.
- **Sending a free-form brief to everyone and letting WhatsApp sort it out.**
  Meta accepts such a message and then does not deliver it outside the window;
  the command would report a message sent that nobody received.
- **Putting the whole brief in the template.** A template variable cannot hold
  line breaks, and the brief's length varies; the headline and a button that
  asks for the brief keep the template short and the brief whole.
- **Drafting orders and claims automatically for the brief.** Drafting is a
  person's decision (ADR 0019 rejected automatic sending for the same reason);
  the brief lists what is already waiting and says what could be drafted.
