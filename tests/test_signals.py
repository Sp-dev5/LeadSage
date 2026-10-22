"""Sales-hiring evidence. No network and no model: the job list and the model are faked."""

import json

import pytest

from agent import signals
from agent.fetch import Page
from agent.score_lead import ICP
from tests.test_scoring import BASE_ICP

ICP_ = ICP.model_validate(BASE_ICP)
API = "https://api.ashbyhq.com/posting-api/job-board/acme"


def job_list(*titles: str, unlisted: tuple[str, ...] = ()) -> Page:
    jobs = [{"title": t, "isListed": True} for t in titles]
    jobs += [{"title": t, "isListed": False} for t in unlisted]
    return Page(url=API, text=json.dumps({"jobs": jobs}), fetched_at="2026-10-23T00:00:00+00:00", ok=True)


@pytest.fixture
def board(monkeypatch):
    """Fake the download; returns the URLs asked for. Set `.page` to change the answer."""
    class Calls(list):
        page = job_list()

    calls = Calls()

    def fake(url):
        calls.append(url)
        return calls.page

    monkeypatch.setattr(signals, "fetch_json", fake)
    return calls


@pytest.fixture
def model(monkeypatch):
    """Fake the model; set `.answers` to (quote, yes/no/unsure) pairs. Records what it was shown."""
    class Seen(list):
        answers: list = []

    seen = Seen()

    def fake(system, user_prompt, schema, provider=None):
        seen.append(user_prompt)
        return schema(titles=[signals.TitleCall(quote=q, is_sales_role=a) for q, a in seen.answers])

    monkeypatch.setattr(signals, "generate_structured", fake)
    return seen


@pytest.mark.parametrize("title, expected", [
    ("Account Executive, Mid-Market - EMEA", True),
    ("SDR (Bengaluru)", True),
    ("Senior Account Executive", True),
    ("Sales Engineer", False),
    ("Senior Product Designer", False),
    ("Business Analyst - SDRs team", False),
])
def test_exact_match_needs_whole_words(title, expected):
    assert signals.exact_match(title, ["account executive", "sdr"]) is expected


def test_exact_and_inferred_roles(tmp_path, board, model):
    board.page = job_list("Account Executive", "Growth Sales Lead", "Product Designer")
    model.answers = [("Growth Sales Lead", "yes"), ("Product Designer", "no")]
    h = signals.sales_hiring("Acme", "ashby:acme", ICP_, tmp_path)
    assert h.ok and h.jobs_seen == 3
    assert [(r.title, r.match) for r in h.roles] == [("Account Executive", "exact"),
                                                     ("Growth Sales Lead", "inferred")]
    assert h.roles[0].source == API
    assert "Account Executive" not in model[0]


def test_model_answer_that_is_not_a_listed_title_is_dropped(tmp_path, board, model):
    board.page = job_list("Partner Manager")
    model.answers = [("Chief Revenue Officer", "yes"), ("Partner", "yes")]
    assert signals.sales_hiring("Acme", "ashby:acme", ICP_, tmp_path).roles == []


def test_unsure_does_not_count(tmp_path, board, model):
    board.page = job_list("Customer Success Manager")
    model.answers = [("Customer Success Manager", "unsure")]
    assert signals.sales_hiring("Acme", "ashby:acme", ICP_, tmp_path).roles == []


def test_model_is_not_called_when_every_title_matched(tmp_path, board, model):
    board.page = job_list("Account Executive", "SDR")
    h = signals.sales_hiring("Acme", "ashby:acme", ICP_, tmp_path)
    assert len(h.roles) == 2 and model == []


def test_unlisted_and_duplicate_jobs_are_skipped(tmp_path, board, model):
    board.page = job_list("SDR", "SDR", unlisted=("Account Executive",))
    h = signals.sales_hiring("Acme", "ashby:acme", ICP_, tmp_path)
    assert h.jobs_seen == 1 and [r.title for r in h.roles] == ["SDR"]


@pytest.mark.parametrize("job_board", ["kula:rocketlane", "ashby", "ashby:", ""])
def test_unsupported_board_is_not_fetched(tmp_path, board, job_board):
    h = signals.sales_hiring("Acme", job_board, ICP_, tmp_path)
    assert not h.ok and "unsupported" in h.error
    assert board == []


def test_failed_download_is_not_ok(tmp_path, board):
    board.page = Page(url=API, fetched_at="2026-10-23T00:00:00+00:00", ok=False, error="HTTP 404")
    h = signals.sales_hiring("Acme", "ashby:acme", ICP_, tmp_path)
    assert not h.ok and h.error == "HTTP 404" and h.roles == []


def test_job_list_is_saved_and_reused(tmp_path, board, model):
    board.page = job_list("SDR")
    signals.sales_hiring("Acme Co.", "ashby:acme", ICP_, tmp_path)
    signals.sales_hiring("Acme Co.", "ashby:acme", ICP_, tmp_path)
    assert board == [API]
    assert (tmp_path / "acme-co-jobs.json").exists()


TRAKSTAR_HTML = """<div class="js-card"><a href="/jobs/a/"><h3 class="rb-h3 js-job-list-opening-name cut-text"
    title=" Sales Manager-Mid Market">
        Sales Manager-Mid Market
    </h3></a></div>
<div class="js-card"><a href="/jobs/b/"><h3 class="js-job-list-opening-name"
    title="x">
        Integration &amp; Customisation Engineer-3
    </h3></a></div>"""


@pytest.mark.parametrize("board, text, expected", [
    ("ashby", json.dumps({"jobs": [{"title": "SDR"}, {"title": "Hidden", "isListed": False}]}), ["SDR"]),
    ("greenhouse", json.dumps({"jobs": [{"title": "Account Executive"}], "meta": {}}), ["Account Executive"]),
    ("lever", json.dumps([{"text": "Sales Manager", "id": "1"}]), ["Sales Manager"]),
    ("keka", json.dumps([{"id": 1, "title": "Technical Support Engineer"}]), ["Technical Support Engineer"]),
    ("trakstar", TRAKSTAR_HTML, ["Sales Manager-Mid Market", "Integration & Customisation Engineer-3"]),
])
def test_each_board_reads_its_titles(board, text, expected):
    assert signals.BOARDS[board].titles(text) == expected


@pytest.mark.parametrize("board, board_id, url", [
    ("ashby", "atlan", "https://api.ashbyhq.com/posting-api/job-board/atlan"),
    ("greenhouse", "airbnb", "https://boards-api.greenhouse.io/v1/boards/airbnb/jobs"),
    ("lever", "palantir", "https://api.lever.co/v0/postings/palantir?mode=json"),
    ("keka", "zluri/ed2b6b25-be74-43f1-9a38-c3bf27b9146c",
     "https://zluri.keka.com/careers/api/embedjobs/default/active/ed2b6b25-be74-43f1-9a38-c3bf27b9146c"),
    ("trakstar", "exotel", "https://exotel.hire.trakstar.com/"),
])
def test_each_board_builds_its_list_address(board, board_id, url):
    assert signals.BOARDS[board].list_url(board_id) == url


@pytest.mark.parametrize("url, page_html, expected", [
    ("https://atlan.com/careers/", '<a href="https://jobs.ashbyhq.com/atlan">Jobs</a>', "ashby:atlan"),
    ("https://acme.test/careers", '<iframe src="https://boards.greenhouse.io/acme">', "greenhouse:acme"),
    ("https://acme.test/jobs", '<a href="https://jobs.lever.co/acme/123">', "lever:acme"),
    ("https://www.zluri.com/careers",
     '<script src="https://zluri.keka.com/careers/api/embedjobs/js/ed2b6b25-be74-43f1-9a38-c3bf27b9146c">',
     "keka:zluri/ed2b6b25-be74-43f1-9a38-c3bf27b9146c"),
    ("https://exotel.hire.trakstar.com/", "<html></html>", "trakstar:exotel"),
    ("https://acme.test/careers", "<html>We are hiring, see below</html>", ""),
])
def test_board_is_spotted_on_the_careers_page(url, page_html, expected):
    assert signals.detect_board(url, page_html) == expected


@pytest.fixture
def careers_page(monkeypatch):
    """Fake the careers page download; set `.page` to change it, `.calls` lists URLs fetched."""
    class Calls(list):
        page = Page(url="https://acme.test/careers", text="", fetched_at="2026-10-23T00:00:00+00:00", ok=True)

    calls = Calls()

    def fake(url):
        calls.append(url)
        return calls.page

    monkeypatch.setattr(signals, "fetch_html", fake)
    return calls


def test_typed_job_board_wins_and_nothing_is_fetched(tmp_path, careers_page):
    assert signals.job_board_for("Acme", "lever:acme", "https://acme.test/careers", tmp_path) == "lever:acme"
    assert careers_page == []


def test_blank_job_board_is_found_on_the_careers_page(tmp_path, careers_page):
    careers_page.page = Page(url="https://acme.test/careers", text="see https://jobs.lever.co/acme",
                             fetched_at="2026-10-23T00:00:00+00:00", ok=True)
    assert signals.job_board_for("Acme", "", "https://acme.test/careers", tmp_path) == "lever:acme"


def test_careers_page_that_cannot_be_read_gives_no_board(tmp_path, careers_page):
    careers_page.page = Page(url="u", fetched_at="2026-10-23T00:00:00+00:00", ok=False, error="HTTP 429")
    assert signals.job_board_for("Acme", "", "https://acme.test/careers", tmp_path) == ""


def test_row_with_no_board_gives_no_evidence_not_no_sales(tmp_path, careers_page):
    h = signals.hiring_for({"name": "Acme", "careers_url": "https://acme.test/careers", "job_board": ""},
                           ICP_, tmp_path)
    assert not h.ok and h.roles == [] and "no supported job board" in h.error


def test_trakstar_titles_go_through_the_same_matching(tmp_path, board, model, monkeypatch):
    html_page = Page(url="https://exotel.hire.trakstar.com/", text=TRAKSTAR_HTML,
                     fetched_at="2026-10-23T00:00:00+00:00", ok=True)
    monkeypatch.setattr(signals, "fetch_html", lambda url: html_page)
    model.answers = []
    h = signals.sales_hiring("Exotel", "trakstar:exotel", ICP_, tmp_path)
    assert h.ok and h.jobs_seen == 2 and h.board == "trakstar:exotel"
    assert board == []
