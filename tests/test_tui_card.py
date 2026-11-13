"""rb ui's open check card and its preview (TUI-PARITY F2.1 to F2.7, F3.4, M1, M4 to M6), on the hosted math sample:
the crumb with `⧉`, the tab row by kind with Result first, one panel at a time, the Result figure (`◆` the claim, `┆`
0, rb's `●` and both ends of its range), the Rule band, the Runs table, Used by and the folded working in Source."""
import pytest

from test_tui_parity import HOSTED, rec, run, screen_text  # noqa: F401 - rec is a fixture

pytestmark = pytest.mark.skipif(not (HOSTED["math"] / ".rb").is_dir(), reason="the hosted math sample is not here")


def _only_math(rec):
    from test_tui_parity import name_of
    if name_of(rec) != "math":
        pytest.skip("math's checks")


def open_check(cid, size, keys=()):
    async def check(app, pilot):
        from rabbit_brain.tui_card import CardView
        s = app.screen
        assert s.open_card(cid)
        await pilot.pause(0.6)
        shots = [screen_text(app, *size)]
        for k in keys:
            await pilot.press(k)
            await pilot.pause(0.4)
            shots.append(screen_text(app, *size))
        view = next(iter(app.screen.query(CardView)))
        return shots, view
    return check


def test_card_header_and_tabs(rec):
    """F2.1, F2.2: row 0 is the crumb `q1 › c2` with `⧉` at the right and no `←`, `Link` or `✕`; the tab row by kind
    with Result underlined first; `→` moves the underline, the card's first line never moves; `←` on Result wraps to
    Source."""
    _only_math(rec)
    shots, _ = run(rec, (80, 24), open_check("c2", (80, 24), keys=("right", "left", "left")))
    first = shots[0]
    assert first[0].strip().startswith("q1 › c2") and first[0].rstrip().endswith("⧉"), first[0]
    assert all(x not in "\n".join(first[:-1]) for x in ("←", "Link", "✕")), first
    tabs = next(i for i, line in enumerate(first) if line.strip().startswith("Result  Rule  Runs 6  Used by  Source"))
    assert first[tabs + 1].strip() == "━━━━━━", first[tabs + 1]
    assert shots[1][tabs + 1].strip() == "━━━━" and shots[1][0] == first[0]          # Rule, the crumb where it was
    assert shots[3][tabs + 1].strip() == "━━━━━━" and shots[3][tabs + 1].index("━") > 30   # Source, the row wrapped


def test_result_draws_the_claim_over_rbs_reading(rec):
    """F2.3: `claimed +3.6 points` with `◆`, `rb +3.60 points` with `├─●─┤` ending at +0.62 and +6.58 under its ends,
    `┆` at the 0 label's column; the dot under the claim's `◆`."""
    _only_math(rec)
    shots, _ = run(rec, (80, 24), open_check("c2", (80, 24)))
    s = shots[0]
    claimed = next(line for line in s if line.lstrip().startswith("claimed"))
    rb = next(line for line in s if line.lstrip().startswith("rb "))
    axis = s[s.index(claimed) - 1]
    ends = s[s.index(rb) + 1]
    assert "+3.6 points" in claimed and "+3.60 points" in rb, (claimed, rb)
    assert abs(claimed.index("◆") - rb.index("●")) <= 1, (claimed, rb)
    assert claimed.index("┆") == rb.index("┆") == axis.index("0"), (axis, claimed, rb)
    assert "+0.62" in ends and "+6.58" in ends and "├" in rb and "┤" in rb
    assert abs(ends.index("+0.62") + 2 - rb.index("├")) <= 3 and abs(ends.index("+6.58") + 2 - rb.index("┤")) <= 3, (rb, ends)
    assert axis.rstrip().endswith("W"), axis


def test_rule_band_and_timeline(rec):
    """F2.4: `passes ░░░░░◆░░░░░` with 3.6 over `◆` and +3.55, +3.65 under it, no `├┤`; the timeline reads `──→`."""
    _only_math(rec)
    shots, _ = run(rec, (80, 24), open_check("c2", (80, 24), keys=("right",)))
    s = shots[1]
    band = next(line for line in s if line.lstrip().startswith("passes"))
    over = s[s.index(band) - 1]
    assert "░░░░░◆░░░░░" in band and "├" not in band and "┤" not in band, band
    assert over.index("3.6") <= band.index("◆") <= over.index("3.6") + 2, (over, band)
    assert "+3.55" in s[s.index(band) + 1] and "+3.65" in s[s.index(band) + 1]
    assert any("──→" in line and line.lstrip().startswith("results ") for line in s), s


def test_runs_tab_columns(rec):
    """F2.5: `prompt  seed  accuracy  run`, numbers right-aligned, the run id last; ⏎ on a row opens the run."""
    _only_math(rec)
    shots, view = run(rec, (80, 24), open_check("c1", (80, 24), keys=("right", "right")))
    s = shots[2]
    head = next(line for line in s if line.lstrip().startswith("prompt"))
    assert head.split() == ["prompt", "seed", "accuracy", "run"], head
    rows = s[s.index(head) + 1:s.index(head) + 7]
    assert [r.split()[0] for r in rows] == ["plain"] * 3 + ["step_by_step"] * 3, rows
    ends = {len(r.split("run")[0].rstrip()) for r in rows}
    assert len(ends) == 1, rows                         # the numbers end in one column
    assert all(r.split()[-1].startswith("run") for r in rows)


def test_used_by_and_the_folded_working(rec):
    """F2.6, F2.7: Used by is `┃ q1` and its words with `›`; Source's working is folded under `▶ How rb worked it out`,
    and ⏎ unfolds it, each line starting with a mark."""
    _only_math(rec)

    async def check(app, pilot):
        s = app.screen
        s.open_card("c1")
        await pilot.pause(0.5)
        await pilot.press("right", "right", "right")
        await pilot.pause(0.3)
        used = screen_text(app, 80, 24)
        await pilot.press("right")
        await pilot.pause(0.3)
        src = screen_text(app, 80, 24)
        for _ in range(8):
            await pilot.press("down")
        await pilot.press("enter")
        await pilot.pause(0.3)
        return used, src, screen_text(app, 80, 24)

    used, src, unfolded = run(rec, (80, 24), check)
    assert any(line.lstrip().startswith("┃ q1  Does telling") and line.rstrip().endswith("›") for line in used), used
    assert any("▶ How rb worked it out" in line for line in src), src
    i = next(n for n, line in enumerate(unfolded) if "▼ How rb worked it out" in line)
    after = [line for line in unfolded[i + 1:] if line.strip()]
    assert after and after[0].split()[0] in "●░▰‾?", after


def test_preview_draws_the_focused_row(rec):
    """F3.4: at 120x40 the right pane is the focused check's preview: its crumb as the title, its title, its state, the
    figure with `W`, its chips; no field labels (CHECK, RESULT, WRITTEN)."""
    _only_math(rec)

    async def check(app, pilot):
        return screen_text(app, 120, 40), app.screen.query_one("#side-card").border_title

    s, title = run(rec, (120, 40), check)
    assert title == "q1 › c1", title
    right = "\n".join(line[84:] for line in s)
    assert "▲ failed" in right and "passes" in right and "rb" in right and "W" in right, right
    assert all(x not in right for x in ("CHECK", "RESULT", "WRITTEN")), right


def _focus_tree(app, subject):
    s = app.screen
    tree = s.query_one("#tree-list")
    tree.focus()
    ids = [tree.get_option_at_index(i).id for i in range(tree.option_count)]
    tree.highlighted = ids.index(next(o for o, r in s._object_refs.items() if r == subject))


def test_a_question_nothing_tests_previews_its_chip_outlines_and_next_step(rec):
    """F5.1, F5.2, M7: q3 focused in the tree: the crumb `q1 › q3`, `[Not tested yet]`, the dashed outlines `checks`
    and `experiment`, the Next step box with the bridge's prompt and `⧉`; `p` copies that prompt."""
    _only_math(rec)
    import rabbit_brain.tui as tui
    sent = []

    async def check(app, pilot):
        s = app.screen
        s.copy_text = lambda text: sent.append(text) or "copied"
        _focus_tree(app, "q3")
        await pilot.pause(0.6)
        side = "\n".join(line[84:] for line in screen_text(app, 120, 40))
        await pilot.press("p")
        await pilot.pause(0.3)
        flashed = "\n".join(line[84:] for line in screen_text(app, 120, 40))
        return side, s.query_one("#side-card").border_title, (s.home.ui.next_steps or {}).get("q3"), flashed

    side, title, step, flashed = run(rec, (120, 40), check)
    assert title == "q1 › q3" and "[Not tested yet]" in side and "┌╌ checks" in side and "┌╌ experiment" in side, side
    assert "╭─ Next step" in side and "⧉" in side and step and step.startswith("In this repository"), side
    words = " ".join(side.replace("│", " ").split())
    assert " ".join(step.split())[:60] in words, words
    assert sent == [step] and "[copied]" in flashed, (sent, flashed)


def test_a_resource_previews_its_get_it_and_g_opens_it(rec, monkeypatch):
    """L2, L5, Q3, M9: Qwen focused: `[checkpoint]`, `[↗ Get it · Hugging Face]` and its address, `9 runs ›`; `g`
    opens https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct on this machine and shows `[opened]`; webbrowser is never
    imported and no OSC 8 is written. Over SSH, the address is copied instead."""
    _only_math(rec)
    import sys
    import rabbit_brain.clip as clip
    opened = []
    monkeypatch.setattr(clip, "open_address", lambda url, **kw: opened.append(url) or "opened")
    monkeypatch.delitem(sys.modules, "webbrowser", raising=False)

    async def check(app, pilot):
        _focus_tree(app, "qwen25-05b-instruct")
        await pilot.pause(0.6)
        side = "\n".join(line[84:] for line in screen_text(app, 120, 40))
        footer = screen_text(app, 120, 40)[-1]
        await pilot.press("g")
        await pilot.pause(0.4)
        after = "\n".join(line[84:] for line in screen_text(app, 120, 40))
        return side, footer, after

    side, footer, after = run(rec, (120, 40), check)
    assert "[↗ Get it · Hugging Face]" in side and "huggingface.co/Qwen/" in side and "9 runs ›" in side, side
    assert "remote" not in side and "present" not in side
    assert footer.strip().startswith("⏎ open  g get it"), footer
    assert opened == ["https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct"] and "[opened]" in after, (opened, after)
    assert "webbrowser" not in sys.modules


def test_open_address_never_runs_over_ssh_and_uses_the_platforms_opener():
    from rabbit_brain.clip import open_address

    class Done:
        returncode = 0
    ran = []
    run = lambda argv, **kw: ran.append(argv) or Done()
    which = lambda name: f"/usr/bin/{name}"
    url = "https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct"
    assert open_address(url, env={"SSH_CONNECTION": "1 2 3 4"}, run=run, which=which, platform="linux") is None and ran == []
    assert open_address(url, env={}, run=run, which=which, platform="linux") is None and ran == []      # no display
    assert open_address(url, env={"DISPLAY": ":0"}, run=run, which=which, platform="linux") == "opened" and ran[-1] == ["xdg-open", url]
    assert open_address(url, env={}, run=run, which=which, platform="darwin") == "opened" and ran[-1] == ["open", url]
    got = []
    assert open_address(url, env={}, run=run, which=which, platform="win32", startfile=got.append) == "opened" and got == [url]



def _footer(lines):
    return next(line for line in reversed(lines) if line.strip())


def test_a_question_card_draws_m8(rec):
    """M8: ⏎ on q3 at 80x24: the crumb `q1 › q3` with `⧉`, the words under `┃`, `[Not tested yet]`, the tab row
    `Result  Checks 0  Experiments 0  Used by  Source`, one dim outline row each for checks and experiment, the Next
    step box with the bridge's prompt; the footer is section 3's question-with-a-Next-step footer; p copies the prompt."""
    _only_math(rec)
    sent = []

    async def check(app, pilot):
        app.screen.copy_text = lambda text: sent.append(text) or "copied"
        assert app.screen.open_card("q3")
        await pilot.pause(0.6)
        before = screen_text(app, 80, 24)
        await pilot.press("p")
        await pilot.pause(0.3)
        return before, screen_text(app, 80, 24)

    s, after = run(rec, (80, 24), check)
    assert s[0].strip().startswith("q1 › q3") and s[0].rstrip().endswith("⧉"), s[0]
    assert s[1].strip().startswith("┃ How many problems"), s[1]
    assert any(line.strip() == "[Not tested yet]" for line in s), s
    assert any(line.strip() == "Result  Checks 0  Experiments 0  Used by  Source" for line in s), s
    assert sum(1 for line in s if line.strip().startswith("╌╌ checks ╌")) == 1
    assert sum(1 for line in s if line.strip().startswith("╌╌ experiment ╌")) == 1
    assert any(line.strip().startswith("╭─ Next step") for line in s) and "In this repository" in "\n".join(s)
    assert _footer(s).strip() == "⏎ open  ←→ tabs  p copy prompt  l long form  esc back  ? keys  q quit", _footer(s)
    assert sent and sent[0].startswith("In this repository") and "[copied]" in "\n".join(after), (sent, after)


def test_an_experiment_card_draws_m20(rec):
    """M20: e1's card at 80x24: `▸ experiment` apart from `[Open]` and `[design not confirmed ›]`, the tab row
    `Result  Checks 5  Runs 9  Design  Source`, its checks by title with mark and word, ids right-aligned at the end with
    `›`, the focus `▌` on the first, `+2 more ›`, settings with `✓` and `?`; the footer has `c confirm`."""
    _only_math(rec)

    async def check(app, pilot):
        assert app.screen.open_card("e1")
        await pilot.pause(0.6)
        return screen_text(app, 80, 24)

    s = run(rec, (80, 24), check)
    chips = next(line for line in s if "▸ experiment" in line)
    assert "▸ experiment  [Open]  [design not confirmed ›]" in chips and "experimentOpen" not in chips, chips
    assert any(line.strip() == "Result  Checks 5  Runs 9  Design  Source" for line in s), s
    rows = [line for line in s if line.rstrip().endswith(" ›") and ("passed" in line or "failed" in line)]
    assert rows[0].lstrip().startswith("▌▲ failed") and rows[0].rstrip().endswith("c1 ›"), rows
    assert len({len(r.rstrip()) for r in rows}) == 1, rows                     # ids end in one column
    assert any(line.strip() == "+2 more ›" for line in s)
    assert any(line.strip().startswith("✓ 11 checked  ? 3 not checked") for line in s), s
    assert any(line.strip().startswith("? population.unit") for line in s), s
    assert _footer(s).strip() == "⏎ open  ←→ tabs  c confirm  p copy command  esc back  ? keys  q quit", _footer(s)


def test_a_resource_card_draws_m11(rec):
    """M11: GSM8K's card at 80x24: the crumb `Resources` alone, its name under `┃`, `[dataset]  [no download link]`,
    `9 runs ›   5 checks ›` focused, the Next step box; no tab row; the footer `⏎ open  p copy prompt  esc back`."""
    _only_math(rec)

    async def check(app, pilot):
        assert app.screen.open_card("gsm8k-test")
        await pilot.pause(0.6)
        return screen_text(app, 80, 24)

    s = run(rec, (80, 24), check)
    assert s[0].strip().split() == ["Resources", "⧉"], s[0]
    assert s[1].strip() == "┃ GSM8K test set", s[1]
    assert any(line.strip().startswith("[dataset]  [no download link]") for line in s), s
    assert any(line.strip() == "▌9 runs ›   5 checks ›" for line in s), s
    assert not any(line.strip().startswith("Result") for line in s) and any("╭─ Next step" in line for line in s)
    assert _footer(s).strip() == "⏎ open  p copy prompt  esc back  ? keys  q quit", _footer(s)


def test_a_card_open_at_120_swaps_as_the_tree_moves(rec):
    """Section 0 item 3: at 120x40 with q1's card open on Checks, `shift-tab` reaches the tree and `↓` swaps the card
    in place for e1's, still on its Checks tab; the tree stays drawn."""
    _only_math(rec)

    async def check(app, pilot):
        _focus_tree(app, "q1")
        await pilot.pause(0.3)
        await pilot.press("enter")
        await pilot.pause(0.5)
        await pilot.press("right")
        await pilot.pause(0.3)
        await pilot.press("shift+tab")
        await pilot.pause(0.3)
        await pilot.press("down")
        await pilot.pause(0.6)
        return screen_text(app, 120, 40)

    s = run(rec, (120, 40), check)
    card = [line[33:] for line in s]
    assert any("q1 › e1" in line for line in card[:5]), card[:5]
    tabs = next(i for i, line in enumerate(card) if "Result  Checks 5  Runs 9  Design  Source" in line)
    assert card[tabs + 1].strip(" │").startswith("━━━━━━━━") and card[tabs + 1].index("━") > card[tabs].index("Checks") - 2
    assert any("Research" in line for line in s[:4])


def test_the_long_form_is_labelled_rows(rec):
    """M19: `l` on q1's card: one block per check, its mark, title and id dim at the end; labelled rows Result, Rule,
    Written, Runs with a mark each; a claim's Rule is `◆` and its number; `↓` scrolls with the crumb kept; esc back."""
    _only_math(rec)

    async def check(app, pilot):
        assert app.screen.open_card("q1")
        await pilot.pause(0.5)
        await pilot.press("l")
        await pilot.pause(0.6)
        first = screen_text(app, 80, 24)
        for _ in range(12):
            await pilot.press("down")
        await pilot.pause(0.4)
        scrolled = screen_text(app, 80, 24)
        await pilot.press("escape")
        await pilot.pause(0.4)
        return first, scrolled, screen_text(app, 80, 24)

    first, scrolled, back = run(rec, (80, 24), check)
    assert first[0].strip().split() == ["q1", "⧉"], first[0]
    heads = [line for line in first if line.rstrip().endswith((" c1", " c2", " c-ref"))]
    assert heads and all(line.lstrip(" ▌")[0] in "■▲◧□" for line in heads), heads
    labels = [line.split()[0] for line in first if line.startswith("    ") and line.split()]
    assert {"Result", "Rule", "Written", "Runs"} <= set(labels), labels
    assert any(line.split()[:2] == ["Rule", "◆"] for line in first if line.startswith("    ")), first
    assert any(line.split()[:2] == ["Rule", "░"] for line in first if line.startswith("    ")), first
    assert scrolled[0] == first[0] and scrolled[1:] != first[1:], scrolled
    assert _footer(first).strip() == "⏎ open  p copy command  esc back  ? keys  q quit"
    assert any("Result  Checks 5" in line for line in back), back
