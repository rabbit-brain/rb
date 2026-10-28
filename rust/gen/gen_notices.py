"""Write THIRD-PARTY-NOTICES from rust/Cargo.lock: the license of every crate built into rb, for the platforms the
wheels ship for, and the crates each covers. It runs cargo-about (CARGO_ABOUT names it, else cargo-about on PATH) with
rust/gen/about.toml and about.hbs. Run it after Cargo.lock changes: python rust/gen/gen_notices.py

The file names the digest of the Cargo.lock it was written from, so tests/test_release_setup.py fails when the
dependencies change and it was not written again."""
import hashlib
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
LOCK = os.path.join(ROOT, "rust", "Cargo.lock")
OUT = os.path.join(ROOT, "THIRD-PARTY-NOTICES")
DIGEST = re.compile(r"^Written from rust/Cargo\.lock, its dependencies sha256 ([0-9a-f]{64})\.$", re.M)


def lock_digest(lock: str) -> str:
    """The sha256 of Cargo.lock's packages other than rb's own, so a new rb version alone changes nothing here."""
    blocks = "\n".join(line.rstrip() for line in lock.splitlines()).split("\n\n")
    kept = [b for b in blocks if not re.search(r'^name = "rabbit-brain"$', b, re.M)]
    return hashlib.sha256("\n\n".join(kept).encode("utf-8")).hexdigest()


def recorded_digest(notices: str):
    """The digest a THIRD-PARTY-NOTICES text names, or None."""
    m = DIGEST.search(notices)
    return m.group(1) if m else None


def text() -> str:
    about = os.environ.get("CARGO_ABOUT") or shutil.which("cargo-about")
    if not about:
        sys.exit("cargo-about is not installed: cargo install cargo-about --locked --features cli")
    listed = subprocess.run([about, "generate", "--locked", "-c", os.path.join(HERE, "about.toml"),
                             "-m", os.path.join(ROOT, "rust", "Cargo.toml"), os.path.join(HERE, "about.hbs")],
                            stdout=subprocess.PIPE, check=True).stdout.decode("utf-8")
    body = "\n".join(line.rstrip() for line in listed.splitlines()).strip("\n")
    body = re.sub(r"\n{3,}", "\n\n", body)
    with open(LOCK, encoding="utf-8") as f:
        digest = lock_digest(f.read())
    head = ["Third-party notices for Rabbit Brain (rb)", "",
            "The rb program in this package is built from these Rust crates by other authors, for Linux, macOS and",
            "Windows. Each license below is followed by the crates it covers, then its text.", "",
            f"Written from rust/Cargo.lock, its dependencies sha256 {digest}.", ""]
    return "\n".join(head) + "\n" + body + "\n"


if __name__ == "__main__":
    out = text()
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        f.write(out)
    print(len(re.findall(r"^  \S+ \S+$", out, re.M)), "crates")
