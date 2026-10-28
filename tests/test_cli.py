"""End to end through the command line, the way an agent would drive it."""
from rabbit_brain.models import SCHEMAS, Envelope
from rbtest import main


def run(capsys, *argv):
    code = main(list(argv))
    out = capsys.readouterr()
    return code, out.out, out.err


def run_json(capsys, *argv):
    code, out, err = run(capsys, *argv, "--json")
    env = Envelope.model_validate_json(out)  # exactly one JSON object on stdout
    return code, env, err


def test_a_planned_command_says_what_to_use_today(workdir, capsys):
    code, env, _ = run_json(capsys, "rerun")
    assert code == 2 and not env.ok and env.errors[0].code == "E_NOT_AVAILABLE" and "rb run add" in env.errors[0].fix
    code, out, err = run(capsys, "rerun")
    assert code == 2 and out == "" and "not part of" in err


def test_a_command_rb_does_not_have_is_a_usage_error(workdir, capsys):
    for gone in ("review", "plans", "workspace", "migrate", "import", "example"):
        code, env, _ = run_json(capsys, gone)
        assert code == 2 and env.errors[0].code == "E_USAGE", gone


def test_the_envelope_carries_no_run_id(workdir, capsys):
    code, env, _ = run_json(capsys, "version")
    assert code == 0 and env.ok and env.command == "version"
    assert set(env.model_dump()) == {"ok", "rb_version", "command", "data", "errors", "next", "rb_delta"}


def test_schema_and_docs(workdir, capsys):
    assert set(SCHEMAS) == {"project", "question", "experiment", "metric", "criterion", "run", "analysis", "resource", "decision",
                            "finding", "envelope"}
    for name in [*SCHEMAS, "all", "ir"]:
        code, env, _ = run_json(capsys, "schema", name)
        assert code == 0 and env.data, name
    code, out, _ = run(capsys, "docs")
    assert out.startswith("# Rabbit Brain: AGENTS.md")
    code, env, _ = run_json(capsys, "docs", "--errors")
    assert "E_NOT_AVAILABLE" in env.data["errors"]
    code, out, _ = run(capsys, "version")
    assert out.startswith("rabbit-brain 0.")
    code, out, _ = run(capsys)
    assert code == 0 and "usage: rb" in out and "review" not in out and "workspace" not in out
