from dataclasses import replace
from datetime import datetime, timezone
from html.parser import HTMLParser

import pytest

from rltracker.cli import main
from rltracker.coach import Severity
from rltracker.dashboard import (
    DARK,
    LIGHT,
    MARK_PAIRS,
    MAX_FOCUS,
    METRICS,
    TEXT_PAIRS,
    _nice_ticks,
    collect,
    focus_areas,
    pick_player,
    recurring_strengths,
    render,
)

from .conftest import FIXTURES

NOW = datetime(2026, 10, 25, tzinfo=timezone.utc)


class Outline(HTMLParser):
    """Collects what the accessibility checks below need from a page."""

    def __init__(self):
        super().__init__()
        self.headings: list[int] = []
        self.images: list[dict] = []
        self.charts = 0
        self.tables = 0
        self.external: list[str] = []
        self.lang = None
        self.focusable_without_label: list[dict] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "html":
            self.lang = a.get("lang")
        if len(tag) == 2 and tag[0] == "h" and tag[1].isdigit():
            self.headings.append(int(tag[1]))
        if tag == "img":
            self.images.append(a)
        if tag == "svg":
            self.charts += 1
        if tag == "table":
            self.tables += 1
        for key in ("src", "href"):
            if a.get(key, "").startswith(("http:", "https:", "//")):
                self.external.append(a[key])
        if a.get("tabindex") == "0" and not a.get("aria-label"):
            self.focusable_without_label.append(a)


def outline(page: str) -> Outline:
    parser = Outline()
    parser.feed(page)
    return parser


@pytest.fixture(scope="module")
def session(all_matches):
    return collect(all_matches)


def test_collect_orders_oldest_first_and_numbers_matches(session):
    entries, skipped = session
    assert not skipped
    times = [e.match.played_at for e in entries]
    assert times == sorted(times)
    assert [e.number for e in entries] == [1, 2, 3]
    assert [e.anchor for e in entries] == ["match-1", "match-2", "match-3"]
    for e in entries:
        assert e.player.id == e.match.recorded_by
        assert len(e.lobby) == len(e.match.players)


def test_collect_skips_matches_without_the_player(all_matches, doubles):
    name = doubles.players[0].name
    entries, skipped = collect(all_matches, name)
    assert [e.match for e in entries] == [doubles]
    assert len(skipped) == 2
    assert pick_player(doubles, "nobody") is None


def test_result_and_score_are_from_the_players_side(session):
    for e in session[0]:
        mine, theirs = e.match.score[e.player.team], e.match.score[e.player.team.opponent]
        assert e.score_text == f"{mine}–{theirs}"
        assert e.result == ("Win" if mine > theirs else "Loss" if mine < theirs else "Draw")


def test_focus_areas_rank_by_matches_then_severity(session):
    entries = session[0]
    focus = focus_areas(entries)
    assert 0 < len(focus) <= MAX_FOCUS
    keys = [(-len(f.entries), -f.severity, -len(f.moments)) for f in focus]
    assert keys == sorted(keys)
    for f in focus:
        rules = [{i.rule for i in e.report.insights} for e in f.entries]
        assert all(f.rule in r for r in rules)
        assert f.severity == max(i.severity for e in f.entries for i in e.report.insights if i.rule == f.rule)
    # Nothing left out ranks above the last one shown.
    everything = focus_areas(entries, limit=99)
    assert everything[: len(focus)] == focus


def test_recurring_strengths_counts_matches(session):
    entries = session[0]
    counts = dict(recurring_strengths(entries))
    for title, count in counts.items():
        assert count == sum(any(s.title == title for s in e.report.strengths) for e in entries)


def test_page_is_accessible_and_self_contained(session):
    entries, _ = session
    page = render(entries, heatmaps=False, now=NOW)
    o = outline(page)
    assert o.lang == "en"
    assert o.headings.count(1) == 1 and o.headings[0] == 1
    # Headings never skip a level on the way down.
    assert all(b <= a + 1 for a, b in zip(o.headings, o.headings[1:], strict=False))
    # Every chart has a table with the same numbers.
    assert o.charts == len(METRICS)
    assert o.tables >= o.charts + 1 + len(entries)
    assert not o.external, "the page must work offline"
    assert not o.focusable_without_label
    assert 'href="#main"' in page  # skip link
    assert "prefers-color-scheme:dark" in page and "prefers-reduced-motion" in page
    # Severity is never color alone: every badge carries its name.
    for sev in Severity:
        if f'class="badge sev-{sev.label}"' in page:
            assert f"{sev.label.capitalize()} priority" in page


def test_player_names_are_escaped(doubles):
    owner = doubles.player(doubles.recorded_by)
    sneaky = [replace(p, name="<script>alert(1)</script>") if p.id == owner.id else p for p in doubles.players]
    entries, _ = collect([replace(doubles, players=sneaky)])
    page = render(entries, heatmaps=False, now=NOW)
    assert "<script>alert(1)" not in page
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page


def test_single_match_shows_stat_tiles_with_heat_map(doubles):
    entries, _ = collect([doubles])
    page = render(entries, now=NOW)
    o = outline(page)
    assert "Your stats" in page and "Trends" not in page
    assert o.charts == 0
    assert len(o.images) == 1
    assert o.images[0]["src"].startswith("data:image/png;base64,")
    assert "Heat map of where" in o.images[0]["alt"]


def contrast(a: str, b: str) -> float:
    def luminance(hex_color: str) -> float:
        channels = [int(hex_color[i : i + 2], 16) / 255 for i in (1, 3, 5)]
        r, g, bl = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
        return 0.2126 * r + 0.7152 * g + 0.0722 * bl

    hi, lo = sorted((luminance(a), luminance(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


@pytest.mark.parametrize("theme", [LIGHT, DARK], ids=["light", "dark"])
def test_colors_meet_wcag_aa(theme):
    for fg, bg in TEXT_PAIRS:
        assert contrast(theme[fg], theme[bg]) >= 4.5, (fg, bg)
    for fg, bg in MARK_PAIRS:
        assert contrast(theme[fg], theme[bg]) >= 3.0, (fg, bg)
    # The tooltip swaps text and background.
    assert contrast(theme["bg"], theme["text"]) >= 4.5


def test_nice_ticks_cover_the_range():
    ticks = _nice_ticks(12.3, 47.9)
    assert ticks[0] <= 12.3 and ticks[-1] >= 47.9
    steps = {round(b - a, 6) for a, b in zip(ticks, ticks[1:], strict=False)}
    assert len(steps) == 1
    assert _nice_ticks(5.0, 5.0)  # a flat line still gets an axis


def test_cli_writes_dashboard(tmp_path):
    out = tmp_path / "review.html"
    replays = [str(FIXTURES / "duel.replay"), str(FIXTURES / "doubles.replay")]
    assert main(["dashboard", *replays, "--out", str(out), "--no-heatmaps"]) == 0
    page = out.read_text(encoding="utf-8")
    assert page.startswith("<!doctype html>") and "Match 2" in page
    assert main(["dashboard", *replays, "--out", str(out), "--player", "nobody"]) == 1
