//! Numbers the way Python writes and reads them: `repr(float)`, the format-spec mini-language (`{x:+.4g}`, `{x:.2f}`,
//! `{s:<24}`), `round`, `float(text)` and `int(text)`. Every digit rb prints goes through here, so a reading prints the
//! same digits whichever rb wrote it.

use num_bigint::BigInt;
use num_traits::{One, Zero};

/// The shortest digits that read back as `x`, and the exponent: x = 0.d1d2... * 10^decpt. `x` finite and nonzero.
/// Between two candidates of the same length equally close to x, Python (David Gay's dtoa) takes the one ending in an
/// even digit; Rust's formatter rounds the tie up. The tie is settled here the way Python settles it.
fn shortest(x: f64) -> (String, i32) {
    let s = format!("{:e}", x.abs()); // e.g. "1.2345e-5": shortest round-trip digits
    let (mant, exp) = s.split_once('e').unwrap();
    let exp: i32 = exp.parse().unwrap();
    let digits: String = mant.chars().filter(|c| *c != '.').collect();
    let digits = digits.trim_end_matches('0').to_string();
    let digits = if digits.is_empty() { "0".to_string() } else { digits };
    let decpt = exp + 1;
    if let Some(even) = tie_even(x.abs(), &digits, decpt) {
        return even;
    }
    (digits, decpt)
}

/// When x is exactly halfway between two n-digit decimals that both read back as x, the one ending in an even digit.
fn tie_even(x: f64, digits: &str, decpt: i32) -> Option<(String, i32)> {
    let n = digits.len();
    let s = format!("{:.*e}", n, x); // n + 1 significant digits
    let (mant, e2) = s.split_once('e')?;
    let longer: String = mant.chars().filter(|c| *c != '.').collect();
    if !longer.ends_with('5') || longer.len() != n + 1 {
        return None;
    }
    let decpt2: i32 = e2.parse::<i32>().ok()? + 1;
    // is x exactly that (n+1)-digit decimal?
    let (num, den) = exact_ratio(x);
    let d = BigInt::parse_bytes(longer.as_bytes(), 10)?;
    let exp10 = decpt2 - (n as i32 + 1);
    let ten = BigInt::from(10);
    let exact = if exp10 >= 0 { den == BigInt::one() && d * num_traits::pow(ten, exp10 as usize) == num } else { d * &den == num * num_traits::pow(ten, (-exp10) as usize) };
    if !exact {
        return None;
    }
    let down = &longer[..n];
    let last = down.as_bytes()[n - 1] - b'0';
    if last == 9 {
        return None;
    }
    let chosen = if last % 2 == 0 {
        down.to_string()
    } else {
        let mut up = down.as_bytes().to_vec();
        up[n - 1] += 1;
        String::from_utf8(up).ok()?
    };
    if chosen == digits && decpt2 == decpt {
        return None;
    }
    // it must read back as x
    let text = format!("0.{}e{}", chosen, decpt2);
    let back: f64 = text.parse().ok()?;
    if back != x {
        return None;
    }
    let trimmed = chosen.trim_end_matches('0');
    Some((if trimmed.is_empty() { "0".to_string() } else { trimmed.to_string() }, decpt2))
}

/// `p` significant digits, correctly rounded: (digits, decpt) with x = 0.d1..dp * 10^decpt.
fn significant(x: f64, p: usize) -> (String, i32) {
    let p = p.max(1);
    let s = format!("{:.*e}", p - 1, x.abs());
    let (mant, exp) = s.split_once('e').unwrap();
    let exp: i32 = exp.parse().unwrap();
    let digits: String = mant.chars().filter(|c| *c != '.').collect();
    (digits, exp + 1)
}

fn exp_str(e: i32) -> String {
    if e < 0 {
        format!("-{:02}", -e)
    } else {
        format!("+{:02}", e)
    }
}

/// Python's repr(float).
pub fn repr(x: f64) -> String {
    if x.is_nan() {
        return "nan".into();
    }
    if x.is_infinite() {
        return if x > 0.0 { "inf".into() } else { "-inf".into() };
    }
    let sign = if x.is_sign_negative() { "-" } else { "" };
    if x == 0.0 {
        return format!("{sign}0.0");
    }
    let (digits, decpt) = shortest(x);
    let body = if decpt <= -4 || decpt > 16 {
        let e = decpt - 1;
        let m = if digits.len() == 1 { digits.clone() } else { format!("{}.{}", &digits[..1], &digits[1..]) };
        format!("{m}e{}", exp_str(e))
    } else if decpt <= 0 {
        format!("0.{}{}", "0".repeat((-decpt) as usize), digits)
    } else if decpt as usize >= digits.len() {
        format!("{}{}.0", digits, "0".repeat(decpt as usize - digits.len()))
    } else {
        format!("{}.{}", &digits[..decpt as usize], &digits[decpt as usize..])
    };
    format!("{sign}{body}")
}

/// The exact decimal digits of an integral finite float (1e20 -> "100000000000000000000").
pub fn float_int_digits(f: f64) -> String {
    big_of_float(f.trunc()).to_string()
}

fn big_of_float(f: f64) -> BigInt {
    if f == 0.0 {
        return BigInt::zero();
    }
    let bits = f.to_bits();
    let neg = (bits >> 63) != 0;
    let exp = ((bits >> 52) & 0x7ff) as i64;
    let frac = bits & ((1u64 << 52) - 1);
    let (m, e) = if exp == 0 { (frac, -1074i64) } else { (frac | (1u64 << 52), exp - 1075) };
    let mut b = BigInt::from(m);
    if e >= 0 {
        b <<= e as usize;
    } else {
        b >>= (-e) as usize;
    }
    if neg {
        -b
    } else {
        b
    }
}

// ---------------------------------------------------------------- the format-spec mini-language

#[derive(Debug, Default, Clone)]
pub struct Spec {
    pub fill: Option<char>,
    pub align: Option<char>,
    pub sign: Option<char>,
    pub alt: bool,
    pub zero: bool,
    pub width: usize,
    pub grouping: Option<char>,
    pub precision: Option<usize>,
    pub ty: Option<char>,
}

pub fn parse_spec(spec: &str) -> Spec {
    let c: Vec<char> = spec.chars().collect();
    let mut i = 0;
    let mut s = Spec::default();
    let is_align = |ch: char| matches!(ch, '<' | '>' | '^' | '=');
    if c.len() >= 2 && is_align(c[1]) {
        s.fill = Some(c[0]);
        s.align = Some(c[1]);
        i = 2;
    } else if !c.is_empty() && is_align(c[0]) {
        s.align = Some(c[0]);
        i = 1;
    }
    if i < c.len() && matches!(c[i], '+' | '-' | ' ') {
        s.sign = Some(c[i]);
        i += 1;
    }
    if i < c.len() && c[i] == '#' {
        s.alt = true;
        i += 1;
    }
    if i < c.len() && c[i] == '0' {
        s.zero = true;
        i += 1;
    }
    let mut w = String::new();
    while i < c.len() && c[i].is_ascii_digit() {
        w.push(c[i]);
        i += 1;
    }
    s.width = w.parse().unwrap_or(0);
    if i < c.len() && (c[i] == ',' || c[i] == '_') {
        s.grouping = Some(c[i]);
        i += 1;
    }
    if i < c.len() && c[i] == '.' {
        i += 1;
        let mut p = String::new();
        while i < c.len() && c[i].is_ascii_digit() {
            p.push(c[i]);
            i += 1;
        }
        s.precision = p.parse().ok();
    }
    if i < c.len() {
        s.ty = Some(c[i]);
    }
    s
}

fn group(int_part: &str, sep: char) -> String {
    let digits: Vec<char> = int_part.chars().collect();
    let mut out = String::new();
    for (n, ch) in digits.iter().enumerate() {
        if n > 0 && (digits.len() - n) % 3 == 0 {
            out.push(sep);
        }
        out.push(*ch);
    }
    out
}

fn pad(body: String, sign: &str, spec: &Spec, default_align: char) -> String {
    let len = sign.chars().count() + body.chars().count();
    if spec.width <= len {
        return format!("{sign}{body}");
    }
    let fill = spec.fill.unwrap_or(if spec.zero && spec.align.is_none() { '0' } else { ' ' });
    let align = spec.align.unwrap_or(if spec.zero { '=' } else { default_align });
    let n = spec.width - len;
    let f = |k: usize| fill.to_string().repeat(k);
    match align {
        '<' => format!("{sign}{body}{}", f(n)),
        '^' => format!("{}{sign}{body}{}", f(n / 2), f(n - n / 2)),
        '=' => format!("{sign}{}{body}", f(n)),
        _ => format!("{}{sign}{body}", f(n)),
    }
}

fn sign_of(neg: bool, spec: &Spec) -> &'static str {
    if neg {
        "-"
    } else {
        match spec.sign {
            Some('+') => "+",
            Some(' ') => " ",
            _ => "",
        }
    }
}

/// The digits of |x| as format type `ty` with `precision`, without the sign.
fn float_body(x: f64, ty: char, precision: Option<usize>, alt: bool) -> String {
    let a = x.abs();
    if a.is_nan() {
        return if ty.is_ascii_uppercase() { "NAN".into() } else { "nan".into() };
    }
    if a.is_infinite() {
        return if ty.is_ascii_uppercase() { "INF".into() } else { "inf".into() };
    }
    match ty {
        'f' | 'F' => {
            let p = precision.unwrap_or(6);
            let s = format!("{:.*}", p, a);
            if alt && p == 0 {
                format!("{s}.")
            } else {
                s
            }
        }
        '%' => {
            let p = precision.unwrap_or(6);
            format!("{:.*}%", p, a * 100.0)
        }
        'e' | 'E' => {
            let p = precision.unwrap_or(6);
            let s = format!("{:.*e}", p, a);
            let (m, e) = s.split_once('e').unwrap();
            let e: i32 = e.parse().unwrap();
            let m = if alt && p == 0 { format!("{m}.") } else { m.to_string() };
            let out = format!("{m}e{}", exp_str(e));
            if ty == 'E' {
                out.to_uppercase()
            } else {
                out
            }
        }
        'g' | 'G' => {
            let p = precision.unwrap_or(6).max(1);
            let out = if a == 0.0 {
                if alt {
                    format!("0.{}", "0".repeat(p - 1))
                } else {
                    "0".to_string()
                }
            } else {
                let (digits, decpt) = significant(a, p);
                let digits = if alt { digits } else { digits.trim_end_matches('0').to_string() };
                let digits = if digits.is_empty() { "0".to_string() } else { digits };
                if decpt <= -4 || decpt > p as i32 {
                    let e = decpt - 1;
                    let m = if digits.len() == 1 && !alt { digits.clone() } else { format!("{}.{}", &digits[..1], &digits[1..]) };
                    format!("{m}e{}", exp_str(e))
                } else if decpt <= 0 {
                    format!("0.{}{}", "0".repeat((-decpt) as usize), digits)
                } else if decpt as usize >= digits.len() {
                    let s = format!("{}{}", digits, "0".repeat(decpt as usize - digits.len()));
                    if alt {
                        format!("{s}.")
                    } else {
                        s
                    }
                } else {
                    format!("{}.{}", &digits[..decpt as usize], &digits[decpt as usize..])
                }
            };
            if ty == 'G' {
                out.to_uppercase()
            } else {
                out
            }
        }
        _ => {
            // no type: repr when there is no precision, else like 'g' with at least one digit after the point
            match precision {
                None => {
                    let r = repr(a);
                    r
                }
                Some(p) => {
                    let p = p.max(1);
                    let (digits, decpt) = significant(a, p);
                    let digits = digits.trim_end_matches('0').to_string();
                    let digits = if digits.is_empty() { "0".to_string() } else { digits };
                    if a == 0.0 {
                        "0.0".to_string()
                    } else if decpt <= -4 || decpt > p as i32 {
                        let e = decpt - 1;
                        let m = if digits.len() == 1 { digits.clone() } else { format!("{}.{}", &digits[..1], &digits[1..]) };
                        format!("{m}e{}", exp_str(e))
                    } else if decpt <= 0 {
                        format!("0.{}{}", "0".repeat((-decpt) as usize), digits)
                    } else if decpt as usize >= digits.len() {
                        format!("{}{}.0", digits, "0".repeat(decpt as usize - digits.len()))
                    } else {
                        format!("{}.{}", &digits[..decpt as usize], &digits[decpt as usize..])
                    }
                }
            }
        }
    }
}

/// format(x, spec) for a float.
pub fn fmt_float(x: f64, spec: &str) -> String {
    let sp = parse_spec(spec);
    let ty = sp.ty.unwrap_or('\0');
    let mut body = float_body(x, ty, sp.precision, sp.alt);
    if let Some(sep) = sp.grouping {
        let (int_part, rest) = match body.find(|c: char| !c.is_ascii_digit()) {
            Some(i) => (body[..i].to_string(), body[i..].to_string()),
            None => (body.clone(), String::new()),
        };
        if !int_part.is_empty() {
            body = format!("{}{}", group(&int_part, sep), rest);
        }
    }
    let neg = x.is_sign_negative() && !x.is_nan();
    pad(body, sign_of(neg, &sp), &sp, '>')
}

/// format(i, spec) for an int (a float type converts it, as Python does).
pub fn fmt_int(i: i64, spec: &str) -> String {
    let sp = parse_spec(spec);
    match sp.ty {
        Some('e') | Some('E') | Some('f') | Some('F') | Some('g') | Some('G') | Some('%') => fmt_float(i as f64, spec),
        _ => {
            let mut body = i.unsigned_abs().to_string();
            if let Some(sep) = sp.grouping {
                body = group(&body, sep);
            }
            pad(body, sign_of(i < 0, &sp), &sp, '>')
        }
    }
}

/// format(s, spec) for text: width, fill and alignment (default left), and a precision that truncates.
pub fn fmt_str(s: &str, spec: &str) -> String {
    let sp = parse_spec(spec);
    let body: String = match sp.precision {
        Some(p) => s.chars().take(p).collect(),
        None => s.to_string(),
    };
    pad(body, "", &sp, '<')
}

/// f"{x:g}" and friends for a value that may be an int or a float.
pub fn fmt_num(v: &crate::value::Value, spec: &str) -> String {
    match v {
        crate::value::Value::Int(i) => fmt_int(*i, spec),
        crate::value::Value::Bool(b) => fmt_int(*b as i64, spec),
        crate::value::Value::Float(f) => fmt_float(*f, spec),
        crate::value::Value::BigInt(s) => s.clone(),
        crate::value::Value::Str(s) => fmt_str(s, spec),
        other => crate::pyjson::py_str(other),
    }
}

/// `{x:g}`
pub fn g(x: f64) -> String {
    fmt_float(x, "g")
}

/// Python's round(x) (to an int, ties to even), as a float.
pub fn round0(x: f64) -> f64 {
    x.round_ties_even()
}

/// Python's round(x, ndigits) for ndigits >= 0: the correctly rounded decimal, read back.
pub fn round_to(x: f64, ndigits: i32) -> f64 {
    if !x.is_finite() {
        return x;
    }
    if ndigits >= 0 {
        if ndigits > 308 {
            return x;
        }
        let s = format!("{:.*}", ndigits as usize, x);
        let r: f64 = s.parse().unwrap_or(x);
        if r == 0.0 {
            return 0.0f64.copysign(x);
        }
        r
    } else {
        let p = 10f64.powi(-ndigits);
        (x / p).round_ties_even() * p
    }
}

// ---------------------------------------------------------------- reading numbers as Python does

/// Python's float(text): whitespace around it, underscores between digits, inf/infinity/nan in any case.
pub fn parse_float(text: &str) -> Option<f64> {
    let t = text.trim_matches(|c: char| c.is_whitespace());
    if t.is_empty() {
        return None;
    }
    let lower = t.to_ascii_lowercase();
    let (neg, body) = match lower.strip_prefix('-') {
        Some(b) => (true, b.to_string()),
        None => (false, lower.strip_prefix('+').unwrap_or(&lower).to_string()),
    };
    if body == "inf" || body == "infinity" {
        return Some(if neg { f64::NEG_INFINITY } else { f64::INFINITY });
    }
    if body == "nan" {
        return Some(f64::NAN);
    }
    // digits, at most one '.', an optional exponent; '_' only between two digits
    let chars: Vec<char> = body.chars().collect();
    let mut clean = String::new();
    for (k, &c) in chars.iter().enumerate() {
        if c == '_' {
            let before = k > 0 && chars[k - 1].is_ascii_digit();
            let after = k + 1 < chars.len() && chars[k + 1].is_ascii_digit();
            if !(before && after) {
                return None;
            }
            continue;
        }
        if !(c.is_ascii_digit() || c == '.' || c == 'e' || c == '+' || c == '-') {
            return None;
        }
        clean.push(c);
    }
    let re = regex_float();
    if !re.is_match(&clean) {
        return None;
    }
    let v: f64 = clean.parse().ok()?;
    Some(if neg { -v } else { v })
}

fn regex_float() -> &'static regex::Regex {
    use std::sync::OnceLock;
    static RE: OnceLock<regex::Regex> = OnceLock::new();
    RE.get_or_init(|| regex::Regex::new(r"^(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:e[-+]?[0-9]+)?$").unwrap())
}

/// Python's int(text): whitespace around it, a sign, underscores between digits. Base 10.
pub fn parse_int(text: &str) -> Option<crate::value::Value> {
    let t = text.trim_matches(|c: char| c.is_whitespace());
    let (neg, body) = match t.strip_prefix('-') {
        Some(b) => (true, b),
        None => (false, t.strip_prefix('+').unwrap_or(t)),
    };
    if body.is_empty() {
        return None;
    }
    let chars: Vec<char> = body.chars().collect();
    let mut digits = String::new();
    for (k, &c) in chars.iter().enumerate() {
        if c == '_' {
            let before = k > 0 && chars[k - 1].is_ascii_digit();
            let after = k + 1 < chars.len() && chars[k + 1].is_ascii_digit();
            if !(before && after) {
                return None;
            }
            continue;
        }
        if !c.is_ascii_digit() {
            // Python's int() takes any Unicode decimal digit; rb only ever sees ASCII ones
            match c.to_digit(10) {
                Some(d) => digits.push(char::from_digit(d, 10).unwrap()),
                None => return None,
            }
            continue;
        }
        digits.push(c);
    }
    Some(int_value(neg, &digits))
}

/// An int value from a sign and decimal digits (leading zeros allowed).
pub fn int_value(neg: bool, digits: &str) -> crate::value::Value {
    let d = digits.trim_start_matches('0');
    let d = if d.is_empty() { "0" } else { d };
    let s = if neg && d != "0" { format!("-{d}") } else { d.to_string() };
    match s.parse::<i64>() {
        Ok(i) => crate::value::Value::Int(i),
        Err(_) => crate::value::Value::BigInt(s),
    }
}

// ---------------------------------------------------------------- exact arithmetic for the statistics module

/// A finite float as an exact fraction n / 2^k (k >= 0): (numerator, denominator).
pub fn exact_ratio(x: f64) -> (BigInt, BigInt) {
    if x == 0.0 {
        return (BigInt::zero(), BigInt::one());
    }
    let bits = x.to_bits();
    let neg = (bits >> 63) != 0;
    let exp = ((bits >> 52) & 0x7ff) as i64;
    let frac = bits & ((1u64 << 52) - 1);
    let (mut m, mut e) = if exp == 0 { (frac, -1074i64) } else { (frac | (1u64 << 52), exp - 1075) };
    while m & 1 == 0 && m != 0 {
        m >>= 1;
        e += 1;
    }
    let mut n = BigInt::from(m);
    if neg {
        n = -n;
    }
    if e >= 0 {
        (n << e as usize, BigInt::one())
    } else {
        (n, BigInt::one() << (-e) as usize)
    }
}

/// n / d as the nearest float, ties to even, for big integers (d > 0): Python's int / int.
pub fn ratio_to_f64(n: &BigInt, d: &BigInt) -> f64 {
    use num_bigint::Sign;
    if n.is_zero() {
        return 0.0;
    }
    let neg = n.sign() == Sign::Minus;
    let a = if neg { -n.clone() } else { n.clone() };
    let b = d.clone();
    // scale so the quotient has 55..56 bits, then round
    let shift = (a.bits() as i64) - (b.bits() as i64) - 55;
    let (num, den) = if shift >= 0 { (a.clone(), b.clone() << shift as usize) } else { (a.clone() << (-shift) as usize, b.clone()) };
    let q = &num / &den;
    let r = &num - &q * &den;
    let mut q = q;
    let mut e = shift;
    // q has 54..56 bits; keep 53 with round-half-even on the rest, sticky with the remainder
    let qbits = q.bits() as i64;
    let mut sticky = !r.is_zero();
    // handle subnormals: the smallest exponent of a 53-bit mantissa is -1074
    let mut drop = qbits - 53;
    if e + drop < -1074 {
        drop = -1074 - e;
    }
    if drop > 0 {
        let mask = (BigInt::one() << drop as usize) - BigInt::one();
        let low = &q & &mask;
        let half = BigInt::one() << (drop - 1) as usize;
        q >>= drop as usize;
        e += drop;
        let round_up = if low > half {
            true
        } else if low == half {
            if sticky {
                true
            } else {
                (&q & BigInt::one()) == BigInt::one()
            }
        } else {
            false
        };
        sticky = false;
        let _ = sticky;
        if round_up {
            q += BigInt::one();
        }
    }
    let qf: f64 = {
        let (_, digits) = q.to_u64_digits();
        let mut v = 0f64;
        for (k, dgt) in digits.iter().enumerate() {
            v += (*dgt as f64) * 2f64.powi(64 * k as i32);
        }
        v
    };
    let out = ldexp(qf, e as i32);
    if neg {
        -out
    } else {
        out
    }
}

pub fn ldexp(x: f64, e: i32) -> f64 {
    // x * 2^e, exactly when representable, in steps so no intermediate overflows or underflows early
    let mut x = x;
    let mut e = e;
    while e > 1000 {
        x *= 2f64.powi(1000);
        e -= 1000;
    }
    while e < -1000 {
        x *= 2f64.powi(-1000);
        e += 1000;
    }
    x * 2f64.powi(e)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reprs() {
        assert_eq!(repr(1e-5), "1e-05");
        assert_eq!(repr(1e16), "1e+16");
        assert_eq!(repr(1234567890123456.0), "1234567890123456.0");
        assert_eq!(repr(0.1), "0.1");
        assert_eq!(repr(-0.0), "-0.0");
        assert_eq!(repr(1.0), "1.0");
        assert_eq!(repr(0.0001), "0.0001");
        assert_eq!(repr(1.5e300), "1.5e+300");
        assert_eq!(repr(123456789.0), "123456789.0");
    }

    #[test]
    fn formats() {
        assert_eq!(fmt_float(0.05, "g"), "0.05");
        assert_eq!(fmt_float(1e-5, "g"), "1e-05");
        assert_eq!(fmt_float(123456.0, "g"), "123456");
        assert_eq!(fmt_float(1234567.0, "g"), "1.23457e+06");
        assert_eq!(fmt_float(0.0204, "+.4g"), "+0.0204");
        assert_eq!(fmt_float(-0.0, "g"), "-0");
        assert_eq!(fmt_float(2.5, ".0f"), "2");
        assert_eq!(fmt_float(1.0, "g"), "1");
        assert_eq!(fmt_int(42, ">5"), "   42");
        assert_eq!(fmt_str("ab", "<4"), "ab  ");
    }

    #[test]
    fn ratios() {
        let (n, d) = exact_ratio(0.1);
        assert_eq!(ratio_to_f64(&n, &d), 0.1);
        let n = BigInt::from(1);
        let d = BigInt::from(3);
        assert_eq!(ratio_to_f64(&n, &d), 1.0 / 3.0);
    }
}
