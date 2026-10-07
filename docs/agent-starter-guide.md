# Agent Starter Guide (for Shaivi, Role A)

Goal: before the event, build three tiny programs so that on the day you're building the real product, not learning how agents work. Each one takes an evening at most. Do them in order — each builds on the last.

**The one idea to hold onto:** an "agent" is just a loop. The AI looks at a goal, picks a tool to use, you run that tool, you hand the result back, and it decides what to do next — until it's done. That's it. Everything else is detail.

---

## Setup (15 minutes)

You need Python 3.10+ and one package.

```bash
pip install anthropic
export ANTHROPIC_API_KEY="your-key-here"
```

Get the key from the LLM account your team sets up. For cheap practice, use the small model `claude-haiku-4-5`; for the real product use a stronger one like `claude-sonnet-5-5`. The code is identical; you just change the model name.

> If the organisers give you a different AI provider (OpenAI, Google, etc.), the *ideas* below are the same, but the exact code differs. Tell me which one and I'll adjust this guide.

---

## Step 1 — A tiny agent that researches a company

**What you're building:** you give it a company name, it searches the web and writes 3 lines about the company.

The trick: you don't write the web-search code yourself. You tell the AI "here's a search tool you can use," and it uses it for you. This is the whole agent idea in its simplest form.

```python
import anthropic

client = anthropic.Anthropic()

company = "Spazor Labs"

response = client.messages.create(
    model="claude-haiku-4-5",
    max_tokens=1024,
    tools=[{"type": "web_search_20260209", "name": "web_search"}],
    messages=[{
        "role": "user",
        "content": f"Search the web for the company '{company}' and write exactly 3 lines: what they do, who they serve, and one recent fact.",
    }],
)

# The final text is in the response blocks:
for block in response.content:
    if block.type == "text":
        print(block.text)
```

**What just happened:** the AI decided to search, the search ran on Anthropic's side, and it wrote the summary from what it found. You gave it a goal and a tool; it did the rest.

**You've got it when:** you can swap in any company name and get a sensible 3-line summary.

---

## Step 2 — Make the answer structured

**Why:** a paragraph is nice for humans but useless for a program. The rest of the app needs clean fields it can store and score. So make the AI return data, not prose.

You describe the shape you want, and the SDK fills it in and checks it.

```python
import anthropic
from pydantic import BaseModel

client = anthropic.Anthropic()

class Company(BaseModel):
    name: str
    industry: str
    size: str          # e.g. "10-50 employees"
    summary: str       # 1-2 sentences
    recent_signal: str # something that suggests they might buy

company = "Spazor Labs"

response = client.messages.parse(
    model="claude-haiku-4-5",
    max_tokens=1024,
    tools=[{"type": "web_search_20260209", "name": "web_search"}],
    output_config={"format": Company},
    messages=[{
        "role": "user",
        "content": f"Research '{company}' on the web and fill in the fields.",
    }],
)

data = response.parsed_output   # this is a Company object
print(data.industry, "|", data.size)
print(data.summary)
```

**What just happened:** instead of free text, you got back a `Company` object with real fields. This is exactly what your lead database needs for every company.

**You've got it when:** `data.industry` and `data.size` come back as sensible strings you could save to a database.

---

## Step 3 — Give it a choice of tools (this is the real "agent")

**Why:** a real agent doesn't do one fixed thing. It has several tools and *decides* which to use. Here you give it two tools and let it choose.

You write the tool functions yourself. One searches the web (you can fake it for practice); one "finds an email" (fake it too). The AI picks which to call, and the SDK runs the loop for you.

```python
import anthropic
from anthropic import beta_tool

client = anthropic.Anthropic()

@beta_tool
def research_company(name: str) -> str:
    """Look up what a company does. Returns a short description."""
    # For practice, fake it. Later this calls a real search.
    return f"{name} is a mid-size software company in healthcare."

@beta_tool
def find_email(full_name: str, company: str) -> str:
    """Guess the work email for a person at a company."""
    first = full_name.split()[0].lower()
    domain = company.lower().replace(" ", "") + ".com"
    return f"{first}@{domain}"

runner = client.beta.messages.tool_runner(
    model="claude-haiku-4-5",
    max_tokens=1024,
    tools=[research_company, find_email],
    messages=[{
        "role": "user",
        "content": "Research Acme Health, then find the email for their head of sales, Priya Rao.",
    }],
)

final = runner.until_done()
for block in final.content:
    if block.type == "text":
        print(block.text)
```

**What just happened:** the AI called `research_company` first, then decided on its own to call `find_email` with the name and company, then wrote the answer. You never told it the order — it worked that out. **That decision-making is the thing judges will want to see.**

**You've got it when:** you can watch (add `print` inside each function) the AI call the two tools in a sensible order without you scripting it.

---

## Where this goes on the day

Your real agent is Steps 1–3 stacked up with more tools, in this order per lead:
1. find companies → 2. find the right person → 3. research (Step 1) → 4. **score and explain** → 5. write the email → then pause for a human to approve.

Steps 4 and 5 are more prompt-writing than new concepts. If you're solid on Steps 1–3 above, you'll spend the event wiring real tools into the loop instead of learning the loop.

## If you get stuck

- **Step 1 errors about the tool type** → your model may be too old for `web_search_20260209`; tell me and I'll give you the fallback.
- **Step 2 `parse` not found** → update the package: `pip install -U anthropic`.
- **Anything else** → paste the error in the thread and I'll sort it.
