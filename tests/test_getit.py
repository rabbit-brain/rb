"""Where a reader gets a resource, and what leaves the machine (getit.rs). Every resource says one way to get it, from
what the record holds: a Hugging Face id is its page, a URL stays a URL, a file the repository holds is its path,
else "No download link"; a citation or a licence is a chip beside it, never the way to get it. A page anyone may be
sent, and a bundle, name no local path: /home/<name>, /Users/<name> and C:\\Users\\<name> carry a person's login.
The words "present" and "remote" are no surface's. Where rb can see a resource's origin in a path (the Hugging Face
cache, a DVC pointer, a config that names it), it records it itself, with no network, and asks only where it cannot.

The two resources of the math sample's shape: a checkpoint recorded at a Hugging Face id and at a local path outside
the repository, and a dataset recorded by a file the repository ignores, twice, with a citation and a licence as where
it comes from."""
import json
import os
import re
import shutil
import socket
import subprocess
import threading
import zipfile
from pathlib import Path

import pytest

from rbtest import AGENT_VARS, command, environment
from test_shell_contract import drawn

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="the record is played in a git repository")

WORDS = re.compile(r"\b(present|remote|reconstructible)\b")


def env_of(agent: bool = False, **more) -> dict:
    env = {k: v for k, v in environment().items() if k not in AGENT_VARS and not k.startswith("RB_TEST_SHELL")}
    env.update(RB_TELEMETRY="0", DO_NOT_TRACK="1", RB_ACTOR="human:ana", **more)
    if agent:
        env.pop("RB_ACTOR")
        env["CLAUDECODE"] = "1"
    return env


def rb(root: Path, *argv: str, code: int = 0, agent: bool = False, **env) -> subprocess.CompletedProcess:
    r = subprocess.run([*command(), *argv], cwd=root, env=env_of(agent, **env), capture_output=True, encoding="utf-8", errors="replace",
                       stdin=subprocess.DEVNULL)
    assert r.returncode == code, (argv, r.stdout, r.stderr)
    return r


def rbj(root: Path, *argv: str, code: int = 0, **kw) -> dict:
    return json.loads(rb(root, *argv, "--json", code=code, **kw).stdout)


def git(root: Path, *argv: str) -> None:
    subprocess.run(["git", *argv], cwd=root, check=True, capture_output=True,
                   env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_AUTHOR_NAME": "ana", "GIT_AUTHOR_EMAIL": "ana@example.org",
                        "GIT_COMMITTER_NAME": "ana", "GIT_COMMITTER_EMAIL": "ana@example.org"})


def repo(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    git(root, "init", "-q")
    (root / "README.md").write_text("# INT8 ResNet-50\n", encoding="utf-8")
    (root / ".gitignore").write_text("data/\n", encoding="utf-8")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "start")
    rb(root, "init", "INT8 ResNet-50", "--no-agents")
    return root


@pytest.fixture
def sample(tmp_path):
    """The math sample's two resources, in the INT8 example's names."""
    root = repo(tmp_path / "int8")
    (root / "data").mkdir()
    (root / "data" / "val.jsonl").write_text('{"image": 1}\n', encoding="utf-8")
    rb(root, "resource", "add", "resnet50-fp32", "--type", "checkpoint", "--name", "ResNet-50", "--version", "a1_in1k",
       "--at", "hf:timm/resnet50.a1_in1k", "--at", "local:/srv/lab/cache/resnet50", "--source", "configs/model.yaml; Apache 2.0")
    rb(root, "resource", "add", "imagenet-val", "--type", "dataset", "--name", "ImageNet validation images", "--file", "data/val.jsonl",
       "--at", "local:data/val.jsonl", "--source", "Deng et al. 2009, MIT license")
    return root


def test_each_resource_says_where_a_reader_gets_it_and_nothing_of_present_or_remote(sample):
    ck = rbj(sample, "show", "resnet50-fp32")["data"]["availability"]
    ds = rbj(sample, "show", "imagenet-val")["data"]["availability"]
    # the JSON keeps its fields, and says where to get it beside them (additive)
    assert ck["state"] == "remote" and ds["state"] == "present"
    assert ck["get"] == {"kind": "hf", "href": "https://huggingface.co/timm/resnet50.a1_in1k", "text": "huggingface.co/timm/resnet50.a1_in1k", "public": True}
    assert ds["get"]["kind"] == "none"
    assert ds["cite"] == ["Deng et al. 2009", "MIT"]
    # the same place recorded twice is said once, and recorded once
    assert [loc["ref"] for loc in ds["locations"]] == ["data/val.jsonl"]
    record = (sample / ".rb" / "resources" / "imagenet-val.yaml").read_text(encoding="utf-8")
    assert record.count("ref: data/val.jsonl") == 1
    show = rb(sample, "show", "imagenet-val").stdout
    assert "no download link \u00b7 Deng et al. 2009 \u00b7 MIT" in show
    assert "how to get it again: Deng" not in show
    assert "get it: https://huggingface.co/timm/resnet50.a1_in1k" in rb(sample, "show", "resnet50-fp32").stdout
    for argv in (("status", "--full"), ("show", "resnet50-fp32"), ("show", "imagenet-val"), ("show", "imagenet-val", "--long"), ("context",)):
        out = rb(sample, *argv).stdout
        assert not WORDS.search(out), (argv, WORDS.search(out))


def test_a_page_gives_one_get_it_link_and_a_chip_and_asks_its_reader_nothing(sample):
    page, err = drawn(sample, "page")
    assert page, err
    assert 'class="getit" href="https://huggingface.co/timm/resnet50.a1_in1k"' in page
    # closed form (owner, Nov 13 2026): the chip is focusable and says on hover what it means
    assert re.search(r'<span class="chip nolink"[^>]*title="[^"]+"[^>]*>No download link</span>', page)
    # closed form (owner, Nov 13 2026): what it comes from is a chip that opens its source, an element the page holds
    cite = re.search(r'<a class="chip cite" href="#([^"]+)">Deng et al. 2009 \u00b7 MIT</a>', page)
    assert cite and f' id="{cite.group(1)}"' in page, cite
    # the owner's Next step is the owner's: a reader sees only the chip
    assert "can be downloaded" not in page
    assert "no_download_link" not in page
    text = re.sub(r"<script.*?</script>|<style.*?</style>", "", page, flags=re.S)
    assert not WORDS.search(re.sub(r"<[^>]+>", " ", text)), WORDS.search(re.sub(r"<[^>]+>", " ", text))


def test_the_owners_own_screen_shows_the_copy_and_asks_for_a_link(sample):
    page, err = drawn(sample, "open")
    assert page, err
    # closed form (owner, Nov 13 2026): the Next step is a prompt for their agent, never a command to type
    assert "use Rabbit Brain to record where imagenet-val can be downloaded: its URL or its Hugging Face id." in page
    assert 'class="cpp" data-act="copy-text"' in page and "rb resource locate imagenet-val url" not in page
    assert "rb resource locate" not in page
    assert "missing here: /srv/lab/cache/resnet50 (a path on the machine that recorded it)" in page
    st = rbj(sample, "status")["data"]
    asked = [i for i in st["open"] if i["code"] == "no_download_link"]
    assert [(i["subject"], i["do"]) for i in asked] == [("imagenet-val", "rb resource locate imagenet-val url <its URL>")]


def test_an_agent_adding_a_dataset_with_no_address_is_asked_for_one_and_never_refused(tmp_path):
    root = repo(tmp_path / "r")
    out = rbj(root, "resource", "add", "calib", "--type", "dataset", "--name", "calibration images", "--source", "Deng et al. 2009", agent=True)
    assert out["ok"]
    assert any("No download link is recorded for calib" in w and "rb resource locate calib url <its URL>" in w for w in out["data"]["warnings"])
    # an address given is asked for nothing, and neither is a run's own output of another type
    out = rbj(root, "resource", "add", "holdout", "--type", "dataset", "--name", "held-out", "--url", "https://data.example.org/holdout", agent=True)
    assert not any("No download link" in w for w in out["data"].get("warnings", []))
    out = rbj(root, "resource", "add", "plot", "--type", "artifact", "--name", "a plot", agent=True)
    assert not any("No download link" in w for w in out["data"].get("warnings", []))
    assert "record where calib can be downloaded" in rb(root, "context", agent=True).stdout


HOMES = ("/home/ana/imagenet/val", "/Users/ana/imagenet/val", "C:\\Users\\ana\\imagenet\\val")


@pytest.mark.parametrize("home", HOMES)
def test_pages_and_bundles_carry_no_local_path_outside_the_repository(tmp_path, home):
    root = repo(tmp_path / "r")
    rb(root, "resource", "add", "imagenet-val", "--type", "dataset", "--name", "ImageNet validation images", "--at", f"local:{home}",
       "--url", "https://data.example.org/imagenet-val")
    held = (root / ".rb" / "resources" / "imagenet-val.yaml").read_text(encoding="utf-8")
    forms = {home, home.replace("\\", "/"), home.replace("\\", "\\\\"), "/home/ana", "/Users/ana", "C:/Users/ana", "C:\\Users\\ana"}
    assert any(f in held for f in forms)   # the owner's record keeps it, as recorded
    rb(root, "page", "--out", str(tmp_path / "p.html"))
    rb(root, "export", "--out", str(tmp_path / "plain"))
    rb(root, "export", "--with-record", "--out", str(tmp_path / "bundle"))
    rb(root, "export", "--with-record", "--zip", "--out", str(tmp_path / "bundle.zip"))
    texts = [(tmp_path / "p.html").read_text(encoding="utf-8")]
    for folder in (tmp_path / "plain", tmp_path / "bundle"):
        texts += [p.read_text(encoding="utf-8", errors="replace") for p in folder.rglob("*") if p.is_file()]
    with zipfile.ZipFile(tmp_path / "bundle.zip") as z:
        texts += [z.read(n).decode("utf-8", "replace") for n in z.namelist()]
    for t in texts:
        for f in forms:
            assert f not in t, f
    # the bundle still reads as rb wrote it, and still says where to get it
    inside = tmp_path / "bundle" / "record"
    rb(inside, "doctor")
    rb(inside, "status", "--fail-on", "integrity")
    assert rbj(inside, "show", "imagenet-val")["data"]["availability"]["get"]["href"] == "https://data.example.org/imagenet-val"
    # and the record in the repository is as it was
    assert (root / ".rb" / "resources" / "imagenet-val.yaml").read_text(encoding="utf-8") == held
    rb(root, "doctor")


@pytest.fixture
def listener():
    """A socket that counts every connection: the proxy every request of rb's would go through."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(8)
    s.settimeout(0.2)
    got = []
    stop = threading.Event()

    def serve():
        while not stop.is_set():
            try:
                c, _ = s.accept()
                got.append(1)
                c.close()
            except OSError:
                pass

    t = threading.Thread(target=serve, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{s.getsockname()[1]}", got
    stop.set()
    t.join(2)
    s.close()


def offline(proxy: str) -> dict:
    return {k: proxy for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")} | {"NO_PROXY": "", "no_proxy": ""}


def test_rb_records_the_origin_it_sees_in_the_hugging_face_cache(tmp_path, listener):
    proxy, got = listener
    root = repo(tmp_path / "r")
    snap = tmp_path / "hf" / "hub" / "models--timm--resnet50.a1_in1k" / "snapshots" / "0123abcd9f"
    snap.mkdir(parents=True)
    (snap / "model.safetensors").write_bytes(b"weights")
    ds = tmp_path / "hf" / "hub" / "datasets--ana-lab--imagenet-val"
    ds.mkdir(parents=True)
    out = rbj(root, "resource", "add", "resnet50", "--type", "checkpoint", "--name", "ResNet-50", "--at", f"local:{snap / 'model.safetensors'}", agent=True,
              **offline(proxy))
    hf = [loc for loc in out["data"]["object"]["locations"] if loc["provider"] == "hf"]
    assert [loc["ref"] for loc in hf] == ["timm/resnet50.a1_in1k@0123abcd9f"]
    assert out["data"]["availability"]["get"]["href"] == "https://huggingface.co/timm/resnet50.a1_in1k/tree/0123abcd9f"
    assert not any("No download link" in w for w in out["data"].get("warnings", []))
    out = rbj(root, "resource", "add", "val", "--type", "dataset", "--name", "val", "--at", f"local:{ds}", agent=True, **offline(proxy))
    assert out["data"]["availability"]["get"]["href"] == "https://huggingface.co/datasets/ana-lab/imagenet-val"
    assert got == []


def test_rb_records_the_origin_a_dvc_pointer_or_a_config_names(tmp_path, listener):
    proxy, got = listener
    root = repo(tmp_path / "r")
    (root / "data").mkdir()
    (root / "data" / "val.jsonl").write_text("{}\n", encoding="utf-8")
    (root / "data" / "val.jsonl.dvc").write_text("deps:\n- path: https://data.example.org/imagenet/val.jsonl\nouts:\n- path: val.jsonl\n", encoding="utf-8")
    (root / "data" / "calib.jsonl").write_text("{}\n", encoding="utf-8")
    (root / "configs").mkdir()
    (root / "configs" / "data.yaml").write_text("calib:\n  path: data/calib.jsonl\n  url: https://data.example.org/imagenet/calib.jsonl\n", encoding="utf-8")
    (root / "configs" / "model.yaml").write_text("name: timm/resnet50.a1_in1k\nrevision: 0123abcd9f\npath: models/resnet50\n", encoding="utf-8")
    (root / "models" / "resnet50").mkdir(parents=True)
    git(root, "add", "configs")
    git(root, "commit", "-q", "-m", "configs")
    env = offline(proxy)
    val = rbj(root, "resource", "add", "val", "--type", "dataset", "--name", "val", "--file", "data/val.jsonl", agent=True, **env)
    assert val["data"]["availability"]["get"] == {"kind": "url", "href": "https://data.example.org/imagenet/val.jsonl", "text": "https://data.example.org/imagenet/val.jsonl", "public": True}
    calib = rbj(root, "resource", "add", "calib", "--type", "dataset", "--name", "calib", "--file", "data/calib.jsonl", agent=True, **env)
    assert calib["data"]["availability"]["get"]["href"] == "https://data.example.org/imagenet/calib.jsonl"
    assert any(loc.get("note") == "read from configs/data.yaml" for loc in calib["data"]["object"]["locations"])
    model = rbj(root, "resource", "add", "resnet50", "--type", "checkpoint", "--name", "ResNet-50", "--at", "local:models/resnet50", agent=True, **env)
    assert model["data"]["availability"]["get"]["href"] == "https://huggingface.co/timm/resnet50.a1_in1k/tree/0123abcd9f"
    for out in (val, calib, model):
        assert not any("No download link" in w for w in out["data"].get("warnings", []))
    assert got == []


def test_a_file_the_repository_holds_is_got_with_it_a_git_lfs_pointer_too(tmp_path):
    root = repo(tmp_path / "r")
    (root / "weights").mkdir()
    (root / "weights" / "int8.pt").write_text("version https://git-lfs.github.com/spec/v1\noid sha256:" + "0" * 64 + "\nsize 12\n", encoding="utf-8")
    (root / ".gitattributes").write_text("*.pt filter=lfs diff=lfs merge=lfs -text\n", encoding="utf-8")
    git(root, "add", "weights", ".gitattributes")
    git(root, "commit", "-q", "-m", "weights")
    out = rbj(root, "resource", "add", "int8-ckpt", "--type", "checkpoint", "--name", "ResNet-50 INT8", "--file", "weights/int8.pt", agent=True)
    assert out["data"]["availability"]["get"] == {"kind": "repo", "href": "", "text": "weights/int8.pt", "public": True}
    assert not any("No download link" in w for w in out["data"].get("warnings", []))
    assert "get it: weights/int8.pt in the repository" in rb(root, "show", "int8-ckpt").stdout


def test_finding_an_origin_asks_nothing_of_the_network_by_construction():
    src = (Path(__file__).resolve().parents[1] / "rust" / "src" / "getit.rs").read_text(encoding="utf-8")
    for net in ("crate::http", "ureq", "std::net", "TcpStream", "hub::"):
        assert net not in src, net


def test_a_bundle_of_a_merged_record_names_no_local_path_and_reads_as_rb_wrote_it(tmp_path):
    """rb's copies of what git merged (merges.json) travel with a bundle: they leave the path out too."""
    root = repo(tmp_path / "r")
    rb(root, "resource", "add", "imagenet-val", "--type", "dataset", "--name", "val", "--at", "local:/home/ana/imagenet/val")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "record")
    git(root, "checkout", "-q", "-b", "side")
    rb(root, "resource", "expose", "imagenet-val", "inspected", "--why", "looked at ten images")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "side")
    git(root, "checkout", "-q", "-")
    rb(root, "resource", "locate", "imagenet-val", "url", "https://data.example.org/imagenet-val")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "main")
    merged = subprocess.run(["git", "merge", "-q", "side", "-m", "merge"], cwd=root, capture_output=True, env={**os.environ, "GIT_AUTHOR_NAME": "ana",
                            "GIT_AUTHOR_EMAIL": "ana@example.org", "GIT_COMMITTER_NAME": "ana", "GIT_COMMITTER_EMAIL": "ana@example.org"})
    if merged.returncode != 0:
        # git leaves the two sides' lines side by side: the merge keeps both, the location and the exposure
        f = ".rb/resources/imagenet-val.yaml"
        ours = subprocess.run(["git", "show", f":2:{f}"], cwd=root, capture_output=True, text=True, check=True).stdout
        theirs = subprocess.run(["git", "show", f":3:{f}"], cwd=root, capture_output=True, text=True, check=True).stdout
        exposures = theirs[theirs.index("exposures:"):theirs.index("created_by:")]
        (root / f).write_text(ours.replace("created_by:", exposures + "created_by:", 1), encoding="utf-8")
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "merge")
    rb(root, "doctor")
    rb(root, "export", "--with-record", "--out", str(tmp_path / "b"))
    for p in (tmp_path / "b").rglob("*"):
        if p.is_file():
            assert "/home/ana" not in p.read_text(encoding="utf-8", errors="replace"), p
    rb(tmp_path / "b" / "record", "doctor")
    rb(tmp_path / "b" / "record", "status", "--fail-on", "integrity")


def test_an_address_with_a_token_is_the_owners_alone(tmp_path, listener):
    proxy, got = listener
    root = repo(tmp_path / "r")
    signed = "https://data.example.org/val.tar?X-Amz-Signature=SECRET123"
    rb(root, "resource", "add", "val", "--type", "dataset", "--name", "val", "--url", signed, "--at", "url:https://ana:hunter2@data.example.org/v.tar",
       "--at", "other:/home/ana/val", "--source", "/home/ana/notes.txt; MIT license")
    # the owner sees the address recorded, and is not asked for one
    show = rb(root, "show", "val").stdout
    assert signed in show and "No download link is recorded" not in show
    assert not [i for i in rbj(root, "status")["data"]["open"] if i["code"] == "no_download_link"]
    rb(root, "page", "--out", str(tmp_path / "p.html"))
    rb(root, "export", "--with-record", "--out", str(tmp_path / "b"))
    texts = [(tmp_path / "p.html").read_text(encoding="utf-8")] + [p.read_text(encoding="utf-8", errors="replace") for p in (tmp_path / "b").rglob("*") if p.is_file()]
    for t in texts:
        for secret in ("SECRET123", "hunter2", "other:/home/ana", "ref: /home/ana"):
            assert secret not in t, secret
    # text someone wrote stays theirs in the record; a page's chip names no path
    assert "/home/ana" not in texts[0]
    # closed form (owner, Nov 13 2026): the chip is focusable and says on hover what it means
    assert re.search(r'<span class="chip nolink"[^>]*title="[^"]+"[^>]*>No download link</span>', texts[0])
    rb(tmp_path / "b" / "record", "doctor")
    # rb records no such address by itself, from a config or a DVC pointer
    (root / "data").mkdir()
    (root / "data" / "a.bin").write_text("a", encoding="utf-8")
    (root / "data" / "a.bin.dvc").write_text("deps:\n- path: https://data.example.org/a.bin?sig=TOK\n", encoding="utf-8")
    (root / "data" / "b.bin").write_text("b", encoding="utf-8")
    (root / "configs").mkdir()
    (root / "configs" / "run.yaml").write_text("b:\n  path: data/b.bin\n  url: https://wandb.ai/ana/runs/x?apiKey=K1\n", encoding="utf-8")
    git(root, "add", "configs")
    git(root, "commit", "-q", "-m", "configs")
    for rid, f in (("a", "data/a.bin"), ("b", "data/b.bin")):
        out = rbj(root, "resource", "add", rid, "--type", "dataset", "--name", rid, "--at", f"local:{f}", **offline(proxy))
        assert [loc["provider"] for loc in out["data"]["object"]["locations"]] == ["local"], out["data"]["object"]["locations"]
    assert got == []


def test_a_config_names_an_origin_only_for_the_file_itself_and_its_own_kind(tmp_path):
    root = repo(tmp_path / "r")
    (root / "data").mkdir()
    (root / "data" / "val.bin").write_text("v", encoding="utf-8")
    (root / "configs").mkdir()
    (root / "configs" / "run.yaml").write_text("out: data\nurl: https://wandb.ai/ana/runs/x\nname: data\nrevision: main\nmodel: timm/resnet50.a1_in1k\n",
                                               encoding="utf-8")
    git(root, "add", "configs")
    git(root, "commit", "-q", "-m", "configs")
    out = rbj(root, "resource", "add", "val", "--type", "dataset", "--name", "val", "--at", "local:data/val.bin")
    assert [loc["provider"] for loc in out["data"]["object"]["locations"]] == ["local"]
    assert out["data"]["availability"]["get"]["kind"] == "none"


def test_a_bundles_page_gives_a_file_the_repository_holds(tmp_path):
    root = repo(tmp_path / "r")
    (root / "calib").mkdir()
    (root / "calib" / "list.txt").write_text("img1\n", encoding="utf-8")
    git(root, "add", "calib")
    git(root, "commit", "-q", "-m", "calib")
    rb(root, "resource", "add", "calib", "--type", "dataset", "--name", "calibration list", "--file", "calib/list.txt")
    rb(root, "page", "--out", str(tmp_path / "p.html"))
    rb(root, "export", "--with-record", "--out", str(tmp_path / "b"))
    for page in ((tmp_path / "p.html").read_text(encoding="utf-8"), (tmp_path / "b" / "page.html").read_text(encoding="utf-8")):
        assert "calib/list.txt</span> in the repository" in page or "calib/list.txt</span></a> in the repository" in page
        assert '<span class="chip nolink">' not in page


def test_a_resources_ask_is_words_now_and_nothing_at_a_past_version_or_on_a_page(tmp_path, monkeypatch):
    """B1 (MP-1113-1145): a resource with no download link (asks true). rb open now, and at another place, says the
    Next step in words and draws no command to copy; at a past version it draws no ask at all; rb page and the page an
    export carries (what the hub serves to its owner and to a reader) draw no ask for any viewer."""
    from rbtest import Open, as_the_user
    as_the_user(monkeypatch)
    for k, v in env_of().items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("RB_TEST_SHELL", raising=False)
    root = repo(tmp_path / "int8")
    rb(root, "resource", "add", "calib", "--type", "dataset", "--name", "calibration images", "--source", "Deng et al. 2009")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "record v1")
    first = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, check=True).stdout.strip()
    rb(root, "note", "add", "the calibration images are 512 of the training set")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "record v2")
    git(root, "worktree", "add", "-q", "-b", "exp-b", str(tmp_path / "wt-b"))
    assert rbj(root, "status")["data"]["open"] and any(i["code"] == "no_download_link" for i in rbj(root, "status")["data"]["open"])
    ask = "record where calib can be downloaded: its URL or its Hugging Face id."   # cf-2: a prompt for their agent
    server = Open(root)
    try:
        from test_live import answer
        mine = {"Sec-Fetch-Site": "same-origin"}
        places = {p["branch"]: p["id"] for p in answer(server.url + "/state.json")[1]["places"]}
        drawn_at = {}
        for name, query in (("now", ""), ("past", "?at=" + first), ("away", "?place=" + places["exp-b"])):
            status, body = answer(server.url + "/" + query, mine)
            assert status == 200, (name, body)
            drawn_at[name] = body
    finally:
        server.close()
    assert "read only" in drawn_at["past"] and "read only" in drawn_at["away"]
    for name, body in drawn_at.items():
        assert "rb resource locate" not in body, name
    assert ask in drawn_at["now"] and ask in drawn_at["away"]
    # at a past version the resource's card asks nothing (what waited then stays a line of the record's open items)
    assert 'class="nextc"' not in drawn_at["past"] and ask not in drawn_at["past"]
    assert drawn_at["now"].count('class="nextc"') >= 1 and drawn_at["away"].count('class="nextc"') >= 1
    # a page, and the page an export carries for every viewer (none, the owner on the hub, a reader of a share link)
    page, err = drawn(root, "page")
    hosted, err2 = drawn(root, "export")
    assert page and hosted, (err, err2)
    for body in (page, hosted):
        assert "can be downloaded" not in body and "rb resource locate" not in body and "nextask" not in body
        assert re.search(r'<span class="chip nolink"[^>]*>No download link</span>', body)   # cf-2: a hover, on focus too


def test_a_local_path_typed_into_a_resources_words_stays_off_pages_and_bundles(tmp_path):
    """L1 (MP-1113-1145, the 6b's probe p3c): a path outside the repository typed into a resource's --recipe, --source or
    --subset reaches no page, export or bundle, where Reproduce's steps and scripts, the source's chip and the standing
    lines are made from those words: each says <local path> in its place. The owner's record and rb open keep them as
    typed, and the bundle still reads as rb wrote it."""
    root = repo(tmp_path / "r")
    rb(root, "resource", "add", "calib", "--type", "dataset", "--name", "calibration images", "--recipe", "python fetch.py --out /home/ana/data",
       "--source", "copied from /srv/lab/share by ana", "--subset", "the /tmp/calib split")
    rb(root, "experiment", "add", "INT8 vs FP32", "--id", "int8", "--baseline", "fp32", "--candidate", "int8", "--varies", "precision")
    rb(root, "run", "add", "int8", "int8.top1=75.4", "fp32.top1=76.1", "--input", "calib:eval")
    held = (root / ".rb" / "resources" / "calib.yaml").read_text(encoding="utf-8")
    paths = ("/home/ana/data", "/srv/lab/share", "/tmp/calib")
    assert all(p in held for p in paths)
    rb(root, "page", "--out", str(tmp_path / "p.html"))
    rb(root, "export", "--out", str(tmp_path / "plain"))
    rb(root, "export", "--with-record", "--out", str(tmp_path / "bundle"))
    rb(root, "export", "--with-record", "--zip", "--out", str(tmp_path / "bundle.zip"))
    page = (tmp_path / "p.html").read_text(encoding="utf-8")
    texts = {"page": page}
    for folder in (tmp_path / "plain", tmp_path / "bundle"):
        texts.update({str(p): p.read_text(encoding="utf-8", errors="replace") for p in folder.rglob("*") if p.is_file()})
    with zipfile.ZipFile(tmp_path / "bundle.zip") as z:
        texts.update({n: z.read(n).decode("utf-8", "replace") for n in z.namelist()})
    for name, t in texts.items():
        for p in paths:
            assert p not in t, (name, p)
    # each says the words around it, with the path's place said
    assert "python fetch.py --out &lt;local path&gt;" in page and "copied from &lt;local path&gt; by ana" in page
    inside = tmp_path / "bundle" / "record"
    kept = (inside / ".rb" / "resources" / "calib.yaml").read_text(encoding="utf-8")
    assert "the <local path> split" in kept and "python fetch.py --out <local path>" in kept
    rb(inside, "doctor")
    rb(inside, "status", "--fail-on", "integrity")
    # the owner's own record and screen keep the words as typed
    assert (root / ".rb" / "resources" / "calib.yaml").read_text(encoding="utf-8") == held
    live, err = drawn(root, "open")
    assert live, err
    assert "python fetch.py --out /home/ana/data" in live
    rb(root, "doctor")


def test_a_typed_path_is_left_out_only_where_the_resources_own_words_are_said(tmp_path):
    """L1's review: a page says <local path> only in the lines made from a resource's typed words. The same characters in
    its address, in a question, in an experiment's title or in a note stay as written there."""
    root = repo(tmp_path / "r")
    rb(root, "resource", "add", "val", "--type", "dataset", "--name", "ImageNet validation", "--url", "https://data.example.org/data",
       "--recipe", "tar xf val.tar -C /data")
    rb(root, "question", "add", "Does INT8 hold on out/data and /data?", "--id", "q1")
    rb(root, "experiment", "add", "INT8 on /data", "--id", "int8", "-q", "q1", "--baseline", "fp32", "--candidate", "int8", "--varies", "precision")
    rb(root, "note", "add", "the images live at /data on the eval host", "-e", "int8")
    rb(root, "run", "add", "int8", "int8.top1=75.4", "fp32.top1=76.1", "--input", "val:eval")
    page, err = drawn(root, "page")
    assert page, err
    assert 'href="https://data.example.org/data"' in page
    assert "Does INT8 hold on out/data and /data?" in page and "INT8 on /data" in page and "the images live at /data on the eval host" in page
    assert "tar xf val.tar -C &lt;local path&gt;" in page and "-C /data" not in page
