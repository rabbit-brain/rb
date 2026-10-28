"""A Python str as a Rust string literal."""


def lit(s: str) -> str:
    out = ['"']
    for ch in s:
        o = ord(ch)
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\t":
            out.append("\\t")
        elif ch == "\r":
            out.append("\\r")
        elif o < 0x20 or o == 0x7f:
            out.append(f"\\u{{{o:x}}}")
        else:
            out.append(ch)
    out.append('"')
    return "".join(out)
