"""Install one native wheel in a clean environment, then use only that installation.

Run with a wheel path or a directory containing exactly one wheel. No source build is
allowed, and the SDK, executables and their embedded assets are checked outside the repo.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import sysconfig
import tempfile
import venv


def pe_imports(path: Path) -> list[str]:
    """The DLLs a Windows executable names in its import and delay-load tables, read from its PE headers."""
    data = path.read_bytes()
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if data[:2] != b"MZ" or data[pe:pe + 4] != b"PE\0\0":
        raise ValueError(f"{path} is not a Windows executable")
    sections, optional_size = struct.unpack_from("<H", data, pe + 6)[0], struct.unpack_from("<H", data, pe + 20)[0]
    optional = pe + 24
    directories = optional + (96 if struct.unpack_from("<H", data, optional)[0] == 0x10B else 112)   # PE32, else PE32+
    count = struct.unpack_from("<I", data, directories - 4)[0]
    # each section: its size and address in memory, then its size and offset in the file
    table = [struct.unpack_from("<IIII", data, optional + optional_size + 40 * i + 8) for i in range(sections)]

    def offset(rva: int) -> int:
        for size, address, raw_size, raw in table:
            if address <= rva < address + max(size, raw_size):
                return rva - address + raw
        raise ValueError(f"{path}: address {rva:#x} is in no section")

    names = []
    for index, width, at_name in ((1, 20, 12), (13, 32, 4)):   # the import table, then the delay-load table
        rva = struct.unpack_from("<I", data, directories + 8 * index)[0] if index < count else 0
        at = offset(rva) if rva else None
        while at is not None and any(data[at:at + width]):
            start = offset(struct.unpack_from("<I", data, at + at_name)[0])
            names.append(data[start:data.index(b"\0", start)].decode("ascii"))
            at += width
    return names


def vc_runtime(executable: Path) -> list[str]:
    """The Visual C++ runtime DLLs a Windows executable imports, which a venv does not bring."""
    imported = pe_imports(executable)
    assert "kernel32.dll" in {dll.lower() for dll in imported}, f"{executable.name}: its import table was not read: {imported}"
    return [dll for dll in imported if dll.lower().startswith("vcruntime")]


def installed_smoke() -> None:
    import rabbit_brain as rb
    from rabbit_brain import _rb
    import rabbit_brain.tui  # noqa: F401 - the terminal UI's dependencies must be installed too

    prefix = Path(sys.prefix).resolve()
    assert Path(rb.__file__).resolve().is_relative_to(prefix), rb.__file__
    scripts = Path(sysconfig.get_path("scripts"))
    suffix = ".exe" if os.name == "nt" else ""
    binary = scripts / f"rb{suffix}"
    assert Path(_rb.found()).resolve() == binary.resolve()

    def command(executable: Path, *args: str, input: str | None = None) -> str:
        return subprocess.run([str(executable), *args], input=input, text=True, encoding="utf-8",
                              capture_output=True, check=True, timeout=30).stdout

    for name in ("rb", "rabbit-brain"):
        assert rb.__version__ in command(scripts / f"{name}{suffix}", "version")
    if os.name == "nt":   # the executables carry their own C runtime (.cargo/config.toml)
        for name in ("rb", "rabbit-brain"):
            runtime = vc_runtime(scripts / f"{name}.exe")
            assert not runtime, f"{name}.exe imports {', '.join(runtime)}: it would not start without the Visual C++ Redistributable"
    assert "Rabbit Brain" in command(binary, "docs")
    assert "name: rabbit-brain" in command(binary, "skill")

    state = rb.init("Installed wheel smoke check", path=Path.cwd())
    exp = state.add_experiment("Packaging check", id="package")
    artifact = Path("outputs") / "result.txt"
    artifact.parent.mkdir()
    artifact.write_text("ready\n", encoding="utf-8")
    run = exp.record({"score": 1.0}, files=[artifact], command="wheel smoke check")
    assert run is not None and run.metrics == {"score": 1.0}
    assert run.files[0].path == "outputs/result.txt"
    assert rb.open(Path.cwd()).experiment("package").id == exp.id

    messages = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "wheel-smoke", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ]
    responses = [json.loads(line) for line in command(
        binary, "mcp", input="".join(json.dumps(message) + "\n" for message in messages)).splitlines()]
    assert [message.get("id") for message in responses] == [1, 2], responses
    assert responses[0]["result"]["serverInfo"]["version"] == rb.__version__
    assert "context" in {tool["name"] for tool in responses[1]["result"]["tools"]}
    print(f"Installed wheel OK: rb {rb.__version__}, CLI aliases, SDK, terminal UI, docs, skill and MCP")


def main() -> None:
    if sys.argv[1:] == ["--installed"]:
        installed_smoke()
        return
    if len(sys.argv) != 2:
        raise SystemExit("usage: python smoke-wheel.py <wheel-or-directory>")
    supplied = Path(sys.argv[1]).resolve()
    wheels = list(supplied.glob("*.whl")) if supplied.is_dir() else [supplied]
    if len(wheels) != 1 or not wheels[0].is_file() or wheels[0].suffix != ".whl":
        raise SystemExit("expected exactly one native wheel")
    script = Path(__file__).resolve()
    with tempfile.TemporaryDirectory(prefix="rb-wheel-smoke-") as directory:
        root = Path(directory)
        environment = root / "venv"
        venv.create(environment, with_pip=True)
        python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        env = {**os.environ, "DO_NOT_TRACK": "1", "PYTHONUTF8": "1"}
        env.pop("RB_BIN", None)  # a development override must not bypass the installed wheel
        subprocess.run([str(python), "-m", "pip", "install", "--only-binary=:all:", str(wheels[0])],
                       cwd=root, env=env, check=True, timeout=300)
        project = root / "project"
        project.mkdir()
        subprocess.run([str(python), "-I", str(script), "--installed"], cwd=project, env=env,
                       check=True, timeout=120)


if __name__ == "__main__":
    main()
