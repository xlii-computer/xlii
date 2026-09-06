"""Vector F — /tasks parser, classification, and {{prev}} substitution."""

from __future__ import annotations

import pytest

from xlii import tasks as T


def test_classify_by_prefix():
    assert T.classify_step("pytest -q").kind == T.KIND_SHELL
    assert T.classify_step("?summarize this").kind == T.KIND_AGENT
    assert T.classify_step("/doc add x").kind == T.KIND_SLASH


def test_agent_strips_question_prefix():
    s = T.classify_step("?  uppercase this  ")
    assert s.kind == T.KIND_AGENT
    assert s.body == "uppercase this"


def test_slash_keeps_leading_slash():
    s = T.classify_step("/post-on-x --draft {{prev}}")
    assert s.kind == T.KIND_SLASH
    assert s.body == "/post-on-x --draft {{prev}}"


def test_parse_inline_three_steps():
    p = T.parse_inline("git diff --stat |> ?summarize |> /post-on-x --draft {{prev}}")
    assert [s.kind for s in p.steps] == [T.KIND_SHELL, T.KIND_AGENT, T.KIND_SLASH]
    assert p.steps[0].body == "git diff --stat"


def test_inline_step_may_contain_a_shell_pipe():
    # `|>` is the fat pipe; a normal shell `|` inside a step is left intact.
    p = T.parse_inline("ps aux | grep python |> ?which pids matter")
    assert len(p.steps) == 2
    assert p.steps[0].body == "ps aux | grep python"


def test_empty_and_malformed_pipes_raise():
    with pytest.raises(T.TaskParseError):
        T.parse_inline("")
    with pytest.raises(T.TaskParseError):
        T.parse_inline("echo a |> |> echo b")
    with pytest.raises(T.TaskParseError):
        T.parse_inline("echo a |>")


def test_substitute_shell_quotes_single_token():
    # Argument-injection must be blunted: the whole carry becomes ONE shell token.
    out = T.substitute_prev("tail -50 {{prev}}", "a b; rm -rf /", kind=T.KIND_SHELL)
    assert out == "tail -50 'a b; rm -rf /'"


def test_substitute_raw_opts_into_word_splitting():
    out = T.substitute_prev("tail {{prev:raw}}", "a b", kind=T.KIND_SHELL)
    assert out == "tail a b"


def test_substitute_agent_and_slash_are_raw():
    assert T.substitute_prev("about {{prev}}", "a b", kind=T.KIND_AGENT) == "about a b"
    assert T.substitute_prev("/doc add n {{prev}}", "a b", kind=T.KIND_SLASH) == "/doc add n a b"


def test_references_prev():
    assert T.references_prev("x {{prev}}")
    assert T.references_prev("x {{prev:raw}}")
    assert not T.references_prev("no token here")


def test_truncate_for_inject_head_tail_marker():
    carry = "A" * 100 + "B" * 100
    out = T.truncate_for_inject(carry, 40)
    assert "chars elided" in out
    assert out.startswith("A")
    assert out.rstrip().endswith("B")
    assert len(out) < len(carry)


def test_truncate_noop_when_under_budget():
    assert T.truncate_for_inject("short", 1000) == "short"
    assert T.truncate_for_inject("short", None) == "short"


def test_truncate_for_inject_tiny_budget_hard_cuts():
    assert T.truncate_for_inject("abcdef", 1) == "a"
    assert T.truncate_for_inject("abcdef", 2) == "ab"


def test_render_plan_lists_steps_with_kinds():
    p = T.parse_inline("echo hi |> ?go |> /doc add x")
    text = "\n".join(T.render_plan(p))
    assert "3 steps" in text
    assert "[shell]" in text and "[agent]" in text and "[slash]" in text
