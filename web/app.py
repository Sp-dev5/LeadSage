"""
The LeadSage web app: plain HTML pages over the same agent modules the Streamlit app uses.

Run it:  uvicorn web.app:app --host 127.0.0.1 --port 8000

Set LEADSAGE_PASSWORD in .env to ask for a shared password before any page opens. Without
one the app is open to anyone who can reach it, so keep it on 127.0.0.1.
"""

from __future__ import annotations

import hmac
import json
import logging
import os
import secrets
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

import pycountry
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.middleware.sessions import SessionMiddleware

from agent import export, labels, logos, mailer, store
from agent.contacts import typed_contact
from agent.evaluate import grade_rows, grade_summary
from agent.files import write_text_atomic
from agent.icp_assist import suggest, suggest_buyer
from agent.outreach import (OutreachConfig, contact_for, draft_step, due_dates, forget_lead, load_outreach, next_step,
                            save_outreach, saved_contact, saved_search, sending_problems, setup_problems)
from agent.pipeline import lead_files, read_leads, score_all, signal_rows
from agent.readiness import QUEUES, group, hook_label, in_queue, todo
from agent.score_lead import ALL_CRITERIA, FACT_VALUES, ICP, ICP_PATH, load_icp
from agent.watch import watch_leads
from web import jobs, views

load_dotenv()
HERE = Path(__file__).resolve().parent
PASSWORD = os.getenv("LEADSAGE_PASSWORD", "")
SECRET = os.getenv("LEADSAGE_SECRET", "")
HTTPS = os.getenv("LEADSAGE_HTTPS", "") == "1"
MAX_TRIES = 5
LOCK_SECONDS = 15 * 60
log = logging.getLogger("leadsage")
if PASSWORD and not SECRET:
    raise RuntimeError("LEADSAGE_PASSWORD is set but LEADSAGE_SECRET is not. Add a long random LEADSAGE_SECRET to .env "
                       "so logins survive a restart and the session cookie cannot be forged.")
if HTTPS and not PASSWORD:
    raise RuntimeError("LEADSAGE_HTTPS=1 means the app is on a server, but LEADSAGE_PASSWORD is not set, so anyone "
                       "could open it and send email from your mailbox. Add LEADSAGE_PASSWORD to .env.")
SESSION_SECONDS = 14 * 24 * 3600
OPEN_PATHS = ("/login", "/static/")
STOP_STAGES = {"replied": "Replied", "meeting": "Meeting booked", "lost": "Lost"}
PAGE_SIZE = 10
TEXT_FIELDS = {
    "buyer_persona": "Buyer persona",
    "pain_solved": "Problem you solve",
    "notes": "What you sell and buying signals",
}

failed_logins: dict[str, list[float]] = {}


def csrf_token(request: Request) -> str:
    if "csrf" not in request.session:
        request.session["csrf"] = secrets.token_urlsafe(32)
    return request.session["csrf"]


async def check_csrf(request: Request):
    """Every POST carries the token from this session's pages, as a form field or a header,
    so another site can't post a form in the user's name."""
    if request.method != "POST":
        return
    sent = request.headers.get("x-csrf-token") or (await request.form()).get("csrf") or ""
    want = request.session.get("csrf", "")
    if not want or not hmac.compare_digest(str(sent).encode(), want.encode()):
        raise HTTPException(403, "This page expired. Go back, reload it and try again.")


app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, dependencies=[Depends(check_csrf)])
app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
templates = Jinja2Templates(directory=HERE / "templates")
templates.env.filters.update(plain=views.plain, domain=views.domain, bare=views.bare, day=views.day, stamp=views.stamp, letter=views.letter)
templates.env.globals.update(is_link=views.is_link, share=views.share)


def _password_mark() -> str:
    """A fingerprint of today's password, kept in the session: change the password and every
    older session stops working."""
    return hmac.new(SECRET.encode(), PASSWORD.encode(), "sha256").hexdigest()[:32]


def _signed_in(request: Request) -> bool:
    """The session says logged in, was made with today's password, and was not logged out. The
    cookie alone is not enough: a copy of it stops working at logout."""
    s = request.session
    return (bool(s.get("in")) and hmac.compare_digest(str(s.get("pw", "")), _password_mark())
            and store.login_alive(str(s.get("sid", "")), SESSION_SECONDS, views.DB))


@app.middleware("http")
async def need_login(request: Request, call_next):
    path = request.url.path
    if PASSWORD and not path.startswith(OPEN_PATHS) and not _signed_in(request):
        return RedirectResponse("/login", status_code=303)
    return await call_next(request)


app.add_middleware(SessionMiddleware, secret_key=SECRET or secrets.token_hex(32),
                   same_site="strict", https_only=HTTPS, max_age=SESSION_SECONDS)

SECURITY_HEADERS = {
    "Content-Security-Policy": "; ".join([
        "default-src 'self'", "script-src 'self'", "style-src 'self'", "img-src 'self'",
        "font-src 'self'", "connect-src 'self'", "object-src 'none'", "base-uri 'none'", "form-action 'self'",
        "frame-ancestors 'none'"]),
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}
HSTS = "max-age=31536000"
LOGO_HEADERS = {"Cache-Control": "max-age=3600",
                "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox"}


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Browser rules on every response, redirects and errors included. Scripts load only from this
    app, no other site can frame it or read the URL a click came from (lead names are in it). A
    response that sets its own policy (a logo) keeps it."""
    response = await call_next(request)
    for name, value in SECURITY_HEADERS.items():
        response.headers.setdefault(name, value)
    if HTTPS:
        response.headers.setdefault("Strict-Transport-Security", HSTS)
    return response


def page(request: Request, name: str, **context) -> HTMLResponse:
    context |= {"flash": request.session.pop("flash", None), "job": jobs.running(), "now": datetime.now(),
                "password_set": bool(PASSWORD), "csrf": csrf_token(request)}
    return templates.TemplateResponse(request, name, context)


def back(request: Request, url: str, message: str = "", kind: str = "ok") -> RedirectResponse:
    if message:
        request.session["flash"] = [kind, message]
    return RedirectResponse(url, status_code=303)


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    return page(request, "login.html", wrong=False)


@app.post("/login")
def login(request: Request, password: str = Form("")):
    who, moment = request.client.host if request.client else "", time.monotonic()
    tries = [t for t in failed_logins.get(who, []) if moment - t < LOCK_SECONDS]
    if len(tries) >= MAX_TRIES:
        failed_logins[who] = tries
        response = page(request, "login.html", wrong=False, locked=True)
        response.status_code = 429
        return response
    if PASSWORD and hmac.compare_digest(password.encode(), PASSWORD.encode()):
        failed_logins.pop(who, None)
        request.session.clear()
        login_id = secrets.token_urlsafe(32)
        store.start_login(login_id, SESSION_SECONDS, views.DB)
        request.session.update({"in": True, "sid": login_id, "pw": _password_mark()})
        return RedirectResponse("/", status_code=303)
    failed_logins[who] = [*tries, moment]
    return page(request, "login.html", wrong=True)


@app.post("/logout")
def logout(request: Request):
    store.end_login(str(request.session.get("sid", "")), views.DB)
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    icp, cfg = load_icp(), load_outreach()
    rows = views.lead_rows(icp, cfg)
    return page(request, "home.html", nav="home", rows=rows, h=views.home(rows) if rows else None,
                qualify_at=icp.settings.qualify_at, fresh_days=cfg.fresh_days)


def _whole(text: str | None) -> int:
    """A whole number from a query value; 0 when it is missing or not one."""
    return int(text) if text and text.isdigit() else 0


def _filters(request: Request) -> views.LeadFilters:
    q = request.query_params
    picked = q.get("group", "Qualify")
    return views.LeadFilters(
        group=picked if picked in views.GROUPS else "Qualify",
        countries=tuple(q.getlist("country")),
        region_only=q.get("region") == "1",
        min_fit=_whole(q.get("min_fit")),
        hiring_only=q.get("hiring") == "1",
        signals_only=q.get("signals") == "1",
    )


def _query(request: Request, **change) -> str:
    """The current query string with some values changed; None drops a key."""
    items = [(k, v) for k, v in request.query_params.multi_items() if k not in change]
    items += [(k, v) for k, v in change.items() if v is not None]
    return "?" + urlencode(items) if items else "?"


@app.get("/leads", response_class=HTMLResponse)
def leads_page(request: Request):
    icp, cfg = load_icp(), load_outreach()
    rows = views.lead_rows(icp, cfg)
    f = _filters(request)
    data = views.leads(rows, f)
    current = next((r for r in data["shown"] if r.lead.name == request.query_params.get("lead")), None)
    files = lead_files()
    return page(request, "leads.html", nav="leads", rows=rows, f=f, d=data, current=current,
                files=files, default_file="real_leads.csv" if "real_leads.csv" in files else (files or [""])[0],
                suppressed=store.suppressed(views.DB), qualify_at=icp.settings.qualify_at,
                weights=icp.settings.weights, stages=store.STAGES, query=lambda **kw: _query(request, **kw))


@app.get("/leads.csv")
def leads_csv(request: Request):
    icp, cfg = load_icp(), load_outreach()
    shown = views.leads(views.lead_rows(icp, cfg), _filters(request))["shown"]
    body = export.leads_csv([r.lead for r in shown], {r.lead.name: r.ready.contact for r in shown})
    return Response(body, media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="leads.csv"'})


@app.get("/crm.csv")
def crm_csv(request: Request):
    cfg = load_outreach()
    verdicts = [v for v in request.query_params.getlist("verdict") if v in ("qualify", "review", "disqualify")]
    stage = request.query_params.get("stage", "")
    chosen = [lead for lead in store.load_all(views.DB) if lead.score.verdict in (verdicts or ["qualify"])
              and (stage not in store.STAGES or lead.stage == stage)]
    chosen.sort(key=lambda lead: (-lead.score.fit, lead.name))
    body = export.crm_csv(chosen, {lead.name: saved_contact(lead, cfg, views.DB) for lead in chosen})
    return Response(body, media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="crm-import.csv"'})


@app.post("/leads/score")
def score_leads(request: Request, file: str = Form(...), skip_scored: str = Form("")):
    if file not in lead_files():
        return back(request, "/leads", "That lead list is not in data/.", "error")
    icp = load_icp()
    done = {lead.name.lower() for lead in store.load_all(views.DB)} if skip_scored else set()
    todo_rows = [r for r in read_leads(file) if r["name"].strip().lower() not in done]
    if not todo_rows:
        return back(request, "/leads", "Every company in that list is already scored.")

    def work(report):
        report(f"Researching and scoring {len(todo_rows)} companies from {file}")
        score_all(todo_rows, icp, signal_rows(), report=report, db=views.DB)
        report("Done.")

    jobs.start(f"Scoring {len(todo_rows)} companies", work)
    return back(request, "/leads")


@app.post("/watch")
def watch(request: Request):
    icp, cfg = load_icp(), load_outreach()

    def work(report):
        report("Reading job boards and news pages again")
        watch_leads(icp, cfg, db=views.DB, report=report)

    jobs.start("Checking for new signals", work)
    return back(request, "/leads")


@app.get("/label", response_class=HTMLResponse)
def label_page(request: Request):
    rows = labels.read_rows(views.BLIND)
    asked = request.query_params.get("i", "")
    i = int(asked) if asked.isdigit() and int(asked) < len(rows) else labels.first_unlabelled(rows)
    done = sum(1 for r in rows if r.get("my_label", "").strip())
    return page(request, "label.html", nav="label", rows=rows, i=i, row=rows[i] if rows and i >= 0 else None,
                done=done, labels=labels.LABELS, file=views.BLIND.name)


@app.post("/label")
def label_save(request: Request, i: int = Form(...), name: str = Form(...), label: str = Form(""),
               why: str = Form("")):
    try:
        labels.save_label(i, name, label, why, views.BLIND)
    except ValueError as e:
        return back(request, f"/label?i={i}", str(e), "error")
    except OSError:
        return back(request, f"/label?i={i}", f"Could not save: close {views.BLIND.name} in Excel and try again.", "error")
    nxt = labels.first_unlabelled(labels.read_rows(views.BLIND), after=i)
    return back(request, f"/label?i={nxt}" if nxt >= 0 else "/label", f"Saved: {name} is {label.strip().lower()}.")


@app.post("/label/grade")
def label_grade(request: Request):
    rows = labels.read_rows(views.BLIND)
    if labels.first_unlabelled(rows) >= 0:
        return back(request, "/label", "Label every company first, then score against your labels.", "error")
    icp = load_icp()

    def work(report):
        report(f"Scoring {len(rows)} companies from {views.BLIND.name} with today's code, then comparing with your labels")
        results = grade_rows(rows, icp, report)
        for line in grade_summary(results):
            report(line)

    jobs.start("Scoring against your labels", work)
    return back(request, "/label")


@app.get("/jobs/{job_id}")
def job_status(job_id: str):
    job = jobs.JOBS.get(job_id)
    if job is None:
        return JSONResponse({"done": True, "lines": [], "failed": "", "title": ""})
    return {"title": job.title, "lines": job.lines[-200:], "done": job.done, "failed": job.failed}


@app.get("/logo/{name}")
def logo(name: str):
    path = logos.logo_path(name)
    if path is None:
        letter = (name.strip()[:1] or "?").upper()
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64">'
               '<rect x="1" y="1" width="62" height="62" fill="#161917" stroke="#767D78" stroke-width="2"/>'
               '<text x="32" y="43" text-anchor="middle" font-family="Georgia, serif" font-size="32" '
               f'fill="#ECEEEB">{letter if letter.isalnum() else "?"}</text></svg>')
        return Response(svg, media_type="image/svg+xml", headers=LOGO_HEADERS)
    mime = next((m for m, ext in logos.EXTENSIONS.items() if ext == path.suffix[1:]), "image/png")
    return Response(path.read_bytes(), media_type=mime, headers=LOGO_HEADERS)


def _outreach_rows(icp: ICP, cfg: OutreachConfig) -> list[views.LeadRow]:
    return [r for r in views.lead_rows(icp, cfg) if r.ready.worth_contacting or r.ready.drafted]


@app.get("/outreach", response_class=HTMLResponse)
def outreach_page(request: Request):
    icp, cfg = load_icp(), load_outreach()
    now = datetime.now().astimezone()
    in_play = _outreach_rows(icp, cfg)
    queue = request.query_params.get("queue", "All")
    queue = queue if queue in QUEUES else "All"
    matching = [r for r in in_play if in_queue(r.ready, queue)]
    pages = max(1, -(-len(matching) // PAGE_SIZE))
    asked = request.query_params.get("page", "1")
    at = min(max(int(asked) if asked.isdigit() else 1, 1), pages)
    table = matching[(at - 1) * PAGE_SIZE:at * PAGE_SIZE]
    to_do = {r.lead.name: next_step(r.lead.stage, r.emails, cfg, now).text
             if r.ready.status in ("sent", "stopped") or r.lead.stage in store.STOPPED else todo(r.ready)
             for r in table}
    ready = [r.ready for r in in_play]
    stats = {
        "ready": sum(r.ready for r in ready),
        "contact": sum(in_queue(r, "Needs a contact") for r in ready),
        "hook": sum(in_queue(r, "Needs a hook") for r in ready),
        "done": sum(r.status in ("approved", "sent") for r in ready),
        "replied": sum(r.lead.stage in ("replied", "meeting") for r in in_play),
    }
    undrafted = [r.lead for r in in_play if r.ready.worth_contacting and not r.ready.drafted
                 and r.lead.stage not in store.STOPPED]
    detail = None
    if (name := request.query_params.get("lead")) and any(r.lead.name == name for r in in_play):
        detail = _detail(name, icp, cfg, now)
    return page(request, "outreach.html", nav="outreach", in_play=in_play, table=table, queue=queue, queues=QUEUES,
                at=at, pages=pages, total=len(matching), first=(at - 1) * PAGE_SIZE + 1,
                counts={q: sum(in_queue(r.ready, q) for r in in_play) for q in QUEUES}, to_do=to_do, stats=stats,
                undrafted=undrafted, cfg=cfg, setup=setup_problems(cfg),
                suppressed=store.suppressed(views.DB), d=detail, stop_stages=STOP_STAGES, stages=store.STAGES,
                can_read=mailer.can_read(),
                query=lambda **kw: _query(request, **kw))


def _detail(name: str, icp: ICP, cfg: OutreachConfig, now: datetime) -> dict:
    lead = store.load(name, views.DB)
    emails = {e.step: e for e in store.load_emails(name, views.DB)}
    search = saved_search(lead, cfg, views.DB)
    stopped = lead.stage in store.STOPPED
    due = due_dates(list(emails.values()), cfg)
    steps = []
    for step in range(len(cfg.follow_up_days) + 1):
        email = emails.get(step)
        previous_sent = step == 0 or (step - 1 in emails and emails[step - 1].status == "sent")
        steps.append({
            "n": step,
            "label": "First email" if step == 0 else f"Follow-up {step}",
            "email": email,
            "when": (f"Due {due[step]:%d %b %Y}" if step in due else
                     f"{cfg.follow_up_days[step - 1]} days after the first email" if step else ""),
            "can_draft": not stopped and (email is None or email.status != "sent") and previous_sent,
            "problems": sending_problems(email.body, cfg) if email else [],
        })
    open_step = next((s["n"] for s in steps if s["email"] is None or s["email"].status != "sent"), 0)
    return {
        "lead": lead, "search": search, "steps": steps, "stopped": stopped, "open_step": open_step,
        "group": group(lead.score, icp.settings.qualify_at),
        "next": next_step(lead.stage, list(emails.values()), cfg, now).text,
        "news": store.load_news(name, views.DB), "first": emails.get(0), "hook_label": hook_label(emails.get(0)),
        "others": [p for p in search.people if p != search.contact] if search else [],
        "replies": store.load_replies(name, views.DB), "can_send": mailer.can_send(),
    }


def _lead_url(lead: str, step: int | None = None) -> str:
    params = {"lead": lead} | ({"step": step} if step is not None else {})
    return "/outreach?" + urlencode(params) + ("#emails" if step is not None else "#detail")


@app.post("/outreach/act")
def outreach_act(request: Request, lead: str = Form(...), do: str = Form(...), step: int = Form(0),
                 subject: str = Form(""), body: str = Form(""), reason: str = Form(""), stage: str = Form(""),
                 sure: str = Form(""), person: int = Form(-1), name: str = Form(""), title: str = Form(""),
                 address: str = Form("")):
    icp, cfg = load_icp(), load_outreach()
    try:
        current = store.load(lead, views.DB)
    except KeyError:
        return back(request, "/outreach", "That lead is no longer in the store.", "error")
    try:
        if do == "draft":
            email = draft_step(current, step, icp, cfg, views.DB)
            return back(request, _lead_url(lead, step),
                        f"Drafted. {len(email.citations)} lines kept, {len(email.rejected)} cut by the quote check.")
        if do == "edit":
            store.edit_email(lead, step, subject, body, views.DB)
            return back(request, _lead_url(lead, step), "Your edits are saved. Approve when ready.")
        if do == "approve":
            email = next((e for e in store.load_emails(lead, views.DB) if e.step == step), None)
            if email and (problems := sending_problems(email.body, cfg)):
                raise ValueError("can't approve yet: " + "; ".join(problems))
            store.approve_email(lead, step, views.DB)
            return back(request, _lead_url(lead, step), "Approved. Send it now, or send it yourself and mark it sent."
                        if mailer.can_send() else "Approved. Send it from your own mailbox, then mark it sent.")
        if do == "sent":
            store.mark_sent(lead, step, views.DB)
            return back(request, _lead_url(lead, step), "Marked sent.")
        if do == "send":
            to = mailer.send(current, step, cfg, views.DB)
            return back(request, _lead_url(lead, step), f"Sent to {to} from your mailbox.")
        if do == "address":
            search = saved_search(current, cfg, views.DB)
            if search is None or search.contact is None:
                raise ValueError("choose who to write to first")
            email = mailer.valid_address(address) if address.strip() else ""
            store.save_contacts(lead, search.model_copy(update={"contact": search.contact.model_copy(
                update={"email": email})}), views.DB)
            return back(request, _lead_url(lead), f"Email address for {search.contact.name} saved." if email
                        else f"Email address for {search.contact.name} removed.")
        if do == "stage":
            store.set_stage(lead, stage, views.DB)
            return back(request, _lead_url(lead), f"Stage set to {stage}.")
        if do == "suppress":
            store.suppress(lead, reason, views.DB)
            return back(request, _lead_url(lead), f"{lead} is on the do-not-contact list.")
        if do == "delete":
            if not sure:
                raise ValueError("tick the box to confirm the delete")
            removed = forget_lead(current, views.DB)
            return back(request, "/outreach", f"Deleted {lead} and {removed} saved pages.")
        if do == "search":
            contact_for(current, icp, cfg, views.DB, refresh=True)
            return back(request, _lead_url(lead), "Searched their website again.")
        if do == "pick":
            search = contact_for(current, icp, cfg, views.DB)
            others = [p for p in search.people if p != search.contact]
            if not 0 <= person < len(others):
                raise ValueError("pick someone from the list")
            store.save_contacts(lead, search.model_copy(update={"contact": others[person], "chosen_by": "rep"}),
                                views.DB)
            return back(request, _lead_url(lead), f"Writing to {others[person].name} instead.")
        if do == "type":
            if not name.strip():
                raise ValueError("type a name")
            search = store.load_contacts(lead, views.DB) or contact_for(current, icp, cfg, views.DB)
            store.save_contacts(lead, search.model_copy(update={"contact": typed_contact(name, title),
                                                                "chosen_by": "rep"}), views.DB)
            return back(request, _lead_url(lead), f"Writing to {name.strip()}.")
    except ValueError as e:
        return back(request, _lead_url(lead, step if do in ("draft", "edit", "approve", "sent", "send") else None),
                    str(e), "error")
    except Exception:
        log.exception("outreach action %s failed for %s", do, lead)
        return back(request, _lead_url(lead), "That did not work. The details are in the app's log.", "error")
    return back(request, _lead_url(lead), "Unknown action.", "error")


@app.post("/outreach/replies")
def check_replies(request: Request):
    if not mailer.can_read():
        return back(request, "/outreach", "Reply checking is not set up: add the SMTP and IMAP settings to .env.", "error")
    cfg = load_outreach()

    def work(report):
        report("Looking in your inbox for replies to contacted leads (read-only)")
        mailer.check_replies(cfg, views.DB, report)

    jobs.start("Checking for replies", work)
    return back(request, "/outreach")


@app.post("/outreach/unsuppress")
def unsuppress(request: Request, company: str = Form(...)):
    store.unsuppress(company, views.DB)
    return back(request, "/outreach", f"{company} is off the do-not-contact list. Its stage stays lost until you change it.")


@app.post("/outreach/draft-all")
def draft_all(request: Request):
    icp, cfg = load_icp(), load_outreach()
    undrafted = [r.lead for r in _outreach_rows(icp, cfg) if r.ready.worth_contacting and not r.ready.drafted
                 and r.lead.stage not in store.STOPPED]

    def work(report):
        for lead in undrafted:
            try:
                email = draft_step(lead, 0, icp, cfg, views.DB)
                report(f"{lead.name}: drafted, {len(email.citations)} lines kept, {len(email.rejected)} cut")
            except Exception as e:
                report(f"{lead.name}: failed ({str(e)[:90]})")
        report("Done.")

    if undrafted:
        jobs.start(f"Drafting {len(undrafted)} first emails", work)
    return back(request, "/outreach")


@app.post("/outreach/settings")
def outreach_settings(request: Request, sender_name: str = Form(""), sender_company: str = Form(""),
                      sender_address: str = Form(""), opt_out_line: str = Form(""),
                      draft_verdicts: list[str] = Form([]), follow_up_days: str = Form(""),
                      max_words: int | None = Form(None), startup_max_headcount: int | None = Form(None)):
    cfg = load_outreach()
    try:
        days = [int(d) for d in follow_up_days.split(",") if d.strip()]
        new = OutreachConfig(**(cfg.model_dump() | {
            "sender_name": sender_name.strip(), "sender_company": sender_company.strip(),
            "sender_address": sender_address.strip(), "opt_out_line": opt_out_line.strip(),
            "draft_verdicts": draft_verdicts, "follow_up_days": days,
            "max_words": max_words or cfg.max_words,
            "startup_max_headcount": startup_max_headcount or cfg.startup_max_headcount}))
    except (ValueError, ValidationError) as e:
        msg = e.errors()[0]["msg"] if isinstance(e, ValidationError) else str(e)
        return back(request, "/outreach#settings", f"Not saved: {msg}", "error")
    save_outreach(new)
    return back(request, "/outreach#settings", "Sender and follow-up settings saved.")


@app.get("/emails.csv")
def emails_csv():
    icp, cfg = load_icp(), load_outreach()
    rows = _outreach_rows(icp, cfg)
    body = export.emails_csv([r.lead for r in rows], {r.lead.name: r.emails for r in rows},
                             {r.lead.name: r.ready.contact for r in rows})
    return Response(body, media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="emails.csv"'})


def _country_names(raw: dict) -> list[str]:
    return sorted({c.name for c in pycountry.countries} | set(raw["geography_countries"]))


SETTING_FIELDS = [
    ("qualify_at", "Qualify at fit", int, 0, 100, 1),
    ("disqualify_below", "Disqualify below fit", int, 0, 100, 1),
    ("fail_cap", "A failed gate caps fit at", int, 0, 100, 1),
    ("gate_fail_max", "Gate fails at a score of", int, 0, 5, 1),
    ("min_headcount", "Minimum headcount", int, 0, None, 1),
    ("max_size_factor", "Hard fail above this many times the largest size", float, 1, None, 0.5),
    ("size_tolerance", "Size still scores 3 this far outside the range (0.1 = 10%)", float, 0, 1, 0.05),
    ("unknown_score", "Score when unknown", int, 0, 5, 1),
    ("disagree_gap", "Models disagree at a fit gap of", int, 0, 100, 1),
]


@app.get("/icp", response_class=HTMLResponse)
def icp_page(request: Request):
    raw = json.loads(ICP_PATH.read_text(encoding="utf-8"))
    s = raw["settings"]
    total = sum(s["weights"].values()) or 1
    return page(request, "icp.html", nav="icp", raw=raw, fields=TEXT_FIELDS, countries=_country_names(raw),
                weights=[(n, w, round(100 * w / total)) for n, w in s["weights"].items()],
                setting_fields=[(k, label, kind is int, lo, hi, step, s[k])
                                for k, label, kind, lo, hi, step in SETTING_FIELDS],
                facts={name: list(values) for name, values in FACT_VALUES.items()}, criteria=ALL_CRITERIA,
                labels={c: views.plain(c) for name, values in FACT_VALUES.items() for c in (name, *values)})


def _first_error(e: ValidationError) -> str:
    err = e.errors()[0]
    where = ".".join(str(part) for part in err["loc"][1:] if not isinstance(part, int))
    msg = err["msg"].removeprefix("Value error, ")
    return f"{where}: {msg}" if where else msg


def _save_icp(request: Request, new: dict, done: str):
    try:
        ICP.model_validate(new)
    except ValidationError as e:
        return back(request, "/icp", f"Not saved. {_first_error(e)}", "error")
    write_text_atomic(ICP_PATH, json.dumps(new, indent=2, ensure_ascii=False) + "\n")
    return back(request, "/icp", done)


@app.post("/icp")
def icp_save(request: Request, buyer_persona: str = Form(""), pain_solved: str = Form(""), notes: str = Form(""),
             company_size_min: int = Form(...), company_size_max: int = Form(...),
             countries: list[str] = Form([])):
    raw = json.loads(ICP_PATH.read_text(encoding="utf-8"))
    if company_size_min > company_size_max:
        return back(request, "/icp", "The smallest company size can't be bigger than the largest.", "error")
    new = raw | {"buyer_persona": buyer_persona.strip(), "pain_solved": pain_solved.strip(), "notes": notes.strip(),
                 "company_size_min": company_size_min, "company_size_max": company_size_max,
                 "geography_countries": countries}
    return _save_icp(request, new, "Saved. Rescore the leads to apply it.")


@app.post("/icp/suggest")
async def icp_suggest(request: Request):
    try:
        data = await request.json()
    except ValueError:
        data = None
    if not isinstance(data, dict):
        return JSONResponse({"error": "send a JSON object"}, status_code=400)
    field, context = data.get("field", ""), data.get("context")
    context = context if isinstance(context, dict) else {}
    if field not in TEXT_FIELDS:
        return JSONResponse({"error": "unknown field"}, status_code=400)
    try:
        texts = {f: context.get(f, "") for f in TEXT_FIELDS}
        if field == "buyer_persona":
            text, titles = await run_in_threadpool(suggest_buyer, texts[field], texts)
            return {"text": text, "titles": titles}
        text = await run_in_threadpool(suggest, field, context.get(field, ""), texts)
        return {"text": text}
    except Exception:
        log.exception("suggestion for %s failed", field)
        return JSONResponse({"error": "The local model did not answer. Is ollama serve running?"},
                            status_code=502)


@app.post("/icp/scoring")
async def icp_scoring(request: Request):
    form = await request.form()
    raw = json.loads(ICP_PATH.read_text(encoding="utf-8"))
    settings = dict(raw["settings"])
    try:
        for key, label, kind, *_ in SETTING_FIELDS:
            settings[key] = kind(form[key])
        settings["weights"] = {name: float(form[f"weight:{name}"]) for name in raw["settings"]["weights"]}
    except (KeyError, ValueError):
        return back(request, "/icp", "Not saved. Every number needs a value, and the cut-offs are whole numbers.",
                    "error")
    return _save_icp(request, raw | {"settings": settings}, "Saved. Rescore the leads to apply it.")


@app.post("/icp/rules")
async def icp_rules(request: Request):
    form = await request.form()
    raw = json.loads(ICP_PATH.read_text(encoding="utf-8"))
    rows = zip(form.getlist("rule_fact"), form.getlist("rule_value"), form.getlist("rule_label"),
               form.getlist("rule_action"))
    rules = [{"fact": f, "value": v, "label": label.strip(), "action": a} for f, v, label, a in rows]
    if any(not r["label"] for r in rules):
        return back(request, "/icp", "Not saved. Every rule needs a reason in the Why column.", "error")
    return _save_icp(request, raw | {"fact_rules": rules}, "Saved. Rescore the leads to apply it.")


@app.post("/icp/match")
async def icp_match(request: Request):
    form = await request.form()
    raw = json.loads(ICP_PATH.read_text(encoding="utf-8"))
    try:
        rows = [{k: v for k, v in (("offering", o), ("customers", c)) if v} | {"score": int(s)}
                for o, c, s in zip(form.getlist("ind_offering"), form.getlist("ind_customers"),
                                   form.getlist("ind_score"))]
        default = int(form["industry_default"])
    except (KeyError, ValueError):
        return back(request, "/icp", "Not saved. Every industry score needs a whole number from 0 to 5.", "error")
    titles = [t.strip() for t in str(form.get("sales_titles", "")).splitlines() if t.strip()]
    new = raw | {"industry_map": rows, "industry_default": default, "gates": form.getlist("gate"),
                 "sales_roles": {"definition": str(form.get("sales_definition", "")).strip(), "titles": titles}}
    return _save_icp(request, new, "Saved. Rescore the leads to apply it.")
