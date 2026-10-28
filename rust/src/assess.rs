//! Reading comparisons: the arithmetic behind a finding. It keeps the exact arithmetic of Python's statistics module
//! (statistics.fmean, stdev and variance of Python 3.11 and later) and CPython's own lgamma, so every digit matches the
//! reference (`tests_py/diff_stats.py`).

use crate::pyfloat::{exact_ratio, fmt_float, ratio_to_f64};
use crate::record::{Criterion, Reading, THRESHOLDS};
use crate::value::Value;
use num_bigint::BigInt;
use num_integer::Integer;
use num_traits::{One, Signed, Zero};

pub const MIN_UNITS_FOR_INTERVAL: usize = 3;
pub const EVAL_ROLES: &[&str] = &["eval", "test", "val", "bench", "holdout", "score"];

// ---------------------------------------------------------------- math.fsum, statistics.fmean / variance / stdev

/// math.fsum: the correctly rounded sum (Shewchuk), as CPython computes it.
pub fn fsum(xs: &[f64]) -> f64 {
    let mut partials: Vec<f64> = Vec::new();
    let mut special = 0.0f64;
    let mut inf_sum = 0.0f64;
    for &x0 in xs {
        let mut x = x0;
        if !x.is_finite() {
            if x.is_infinite() {
                inf_sum += x;
            }
            special += x;
            continue;
        }
        let mut i = 0;
        for j in 0..partials.len() {
            let mut y = partials[j];
            if x.abs() < y.abs() {
                std::mem::swap(&mut x, &mut y);
            }
            let hi = x + y;
            let lo = y - (hi - x);
            if lo != 0.0 {
                partials[i] = lo;
                i += 1;
            }
            x = hi;
        }
        partials.truncate(i);
        partials.push(x);
    }
    if special != 0.0 {
        return if inf_sum.is_nan() { f64::NAN } else { special };
    }
    let mut n = partials.len();
    let mut hi = 0.0f64;
    if n > 0 {
        n -= 1;
        hi = partials[n];
        let mut lo = 0.0f64;
        while n > 0 {
            let x = hi;
            n -= 1;
            let y = partials[n];
            hi = x + y;
            let yr = hi - x;
            lo = y - yr;
            if lo != 0.0 {
                break;
            }
        }
        if n > 0 && ((lo < 0.0 && partials[n - 1] < 0.0) || (lo > 0.0 && partials[n - 1] > 0.0)) {
            let y = lo * 2.0;
            let x = hi + y;
            let yr = x - hi;
            if y == yr {
                hi = x;
            }
        }
    }
    hi
}

/// statistics.fmean
pub fn fmean(xs: &[f64]) -> f64 {
    fsum(xs) / xs.len() as f64
}

/// sum((x - mean)^2) exactly, as a fraction (statistics._ss).
fn exact_ss(xs: &[f64]) -> (BigInt, BigInt) {
    // exact mean as a fraction
    let mut num = BigInt::zero();
    let mut den = BigInt::one();
    for &x in xs {
        let (n, d) = exact_ratio(x);
        // num/den + n/d
        num = &num * &d + &n * &den;
        den = &den * &d;
        let g = num.gcd(&den);
        if !g.is_zero() && !g.is_one() {
            num /= &g;
            den /= &g;
        }
    }
    let count = BigInt::from(xs.len());
    let mean_n = num;
    let mean_d = den * &count;
    let mut ss_n = BigInt::zero();
    let mut ss_d = BigInt::one();
    for &x in xs {
        let (n, d) = exact_ratio(x);
        // (n/d - mean_n/mean_d)^2 = (n*mean_d - d*mean_n)^2 / (d*mean_d)^2
        let diff_n = &n * &mean_d - &d * &mean_n;
        let diff_d = &d * &mean_d;
        let tn = &diff_n * &diff_n;
        let td = &diff_d * &diff_d;
        ss_n = &ss_n * &td + &tn * &ss_d;
        ss_d = &ss_d * &td;
        let g = ss_n.gcd(&ss_d);
        if !g.is_zero() && !g.is_one() {
            ss_n /= &g;
            ss_d /= &g;
        }
    }
    (ss_n, ss_d)
}

/// statistics.variance (n - 1 in the denominator), correctly rounded.
pub fn variance(xs: &[f64]) -> f64 {
    let (n, d) = exact_ss(xs);
    let d = d * BigInt::from(xs.len() - 1);
    ratio_to_f64(&n, &d)
}

fn isqrt(n: &BigInt) -> BigInt {
    n.sqrt()
}

/// Square root of n/m, rounded to the nearest integer using round-to-odd.
fn integer_sqrt_of_frac_rto(n: &BigInt, m: &BigInt) -> BigInt {
    let a = isqrt(&(n / m));
    if &(&a * &a * m) != n {
        a | BigInt::one()
    } else {
        a
    }
}

/// Square root of n/m as a float, correctly rounded (statistics._float_sqrt_of_frac).
fn float_sqrt_of_frac(n: &BigInt, m: &BigInt) -> f64 {
    const SQRT_BIT_WIDTH: i64 = 2 * 53 + 3;
    let q = num_integer::Integer::div_floor(&(n.bits() as i64 - m.bits() as i64 - SQRT_BIT_WIDTH), &2);
    let (numerator, denominator) = if q >= 0 {
        (integer_sqrt_of_frac_rto(n, &(m << (2 * q) as usize)) << q as usize, BigInt::one())
    } else {
        (integer_sqrt_of_frac_rto(&(n << (-2 * q) as usize), m), BigInt::one() << (-q) as usize)
    };
    ratio_to_f64(&numerator, &denominator)
}

/// statistics.stdev, correctly rounded (Python 3.11 and later).
pub fn stdev(xs: &[f64]) -> f64 {
    let (n, d) = exact_ss(xs);
    let d = d * BigInt::from(xs.len() - 1);
    if n.is_negative() {
        return f64::NAN;
    }
    float_sqrt_of_frac(&n, &d)
}

// ---------------------------------------------------------------- CPython's lgamma (Modules/mathmodule.c)

const LANCZOS_G: f64 = 6.024680040776729583740234375;
const LANCZOS_G_MINUS_HALF: f64 = 5.524680040776729583740234375;
const LANCZOS_N: usize = 13;
const LANCZOS_NUM: [f64; LANCZOS_N] = [
    23531376880.410759688572007674451636754734846804940,
    42919803642.649098768957899047001988850926355848959,
    35711959237.355668049440185451547166705960488635843,
    17921034426.037209699919755754458931112671403265390,
    6039542586.3520280050642916443072979210699388420708,
    1439720407.3117216736632230727949123939715485786772,
    248874557.86205415651146038641322942321632125127801,
    31426415.585400194380614231628318205362874684987640,
    2876370.6289353724412254090516208496135991145378768,
    186056.26539522349504029498971604569928220784236328,
    8071.6720023658162106380029022722506138218516325024,
    210.82427775157934587250973392071336271166969580291,
    2.5066282746310002701649081771338373386264310793408,
];
const LANCZOS_DEN: [f64; LANCZOS_N] = [
    0.0, 39916800.0, 120543840.0, 150917976.0, 105258076.0, 45995730.0, 13339535.0, 2637558.0, 357423.0, 32670.0, 1925.0, 66.0, 1.0,
];
const LOGPI: f64 = 1.144729885849400174143427351353058711647;

fn lanczos_sum(x: f64) -> f64 {
    let mut num = 0.0f64;
    let mut den = 0.0f64;
    if x < 5.0 {
        for i in (0..LANCZOS_N).rev() {
            num = num * x + LANCZOS_NUM[i];
            den = den * x + LANCZOS_DEN[i];
        }
    } else {
        for i in 0..LANCZOS_N {
            num = num / x + LANCZOS_NUM[i];
            den = den / x + LANCZOS_DEN[i];
        }
    }
    num / den
}

fn m_sinpi(x: f64) -> f64 {
    let y = x.abs() % 2.0;
    let n = (2.0 * y).round() as i64;
    let r = match n {
        0 => (std::f64::consts::PI * y).sin(),
        1 => (std::f64::consts::PI * (y - 0.5)).cos(),
        2 => (std::f64::consts::PI * (1.0 - y)).sin(),
        3 => -(std::f64::consts::PI * (y - 1.5)).cos(),
        4 => (std::f64::consts::PI * (y - 2.0)).sin(),
        _ => unreachable!(),
    };
    (1.0f64).copysign(x) * r
}

/// math.lgamma, as CPython computes it.
pub fn lgamma(x: f64) -> f64 {
    if !x.is_finite() {
        if x.is_nan() {
            return x;
        }
        return f64::INFINITY;
    }
    if x.floor() == x && x <= 2.0 {
        if x <= 0.0 {
            return f64::INFINITY; // a pole: Python raises; rb never asks for one
        }
        return 0.0;
    }
    let absx = x.abs();
    if absx < 1e-20 {
        return -absx.ln();
    }
    let mut r = lanczos_sum(absx).ln() - LANCZOS_G;
    r += (absx - 0.5) * ((absx + LANCZOS_G - 0.5).ln() - 1.0);
    if x < 0.0 {
        r = LOGPI - m_sinpi(absx).abs().ln() - absx.ln() - r;
    }
    let _ = LANCZOS_G_MINUS_HALF;
    r
}

// ---------------------------------------------------------------- Student t, without scipy

fn betacf(a: f64, b: f64, x: f64) -> f64 {
    let tiny = 1e-300;
    let (qab, qap, qam) = (a + b, a + 1.0, a - 1.0);
    let mut c = 1.0f64;
    let mut d = 1.0 - qab * x / qap;
    d = 1.0 / (if d.abs() > tiny { d } else { tiny });
    let mut h = d;
    for m in 1..400 {
        let mf = m as f64;
        let m2 = (2 * m) as f64;
        let mut aa = mf * (b - mf) * x / ((qam + m2) * (a + m2));
        d = 1.0 + aa * d;
        d = 1.0 / (if d.abs() > tiny { d } else { tiny });
        c = 1.0 + aa / c;
        c = if c.abs() > tiny { c } else { tiny };
        h *= d * c;
        aa = -(a + mf) * (qab + mf) * x / ((a + m2) * (qap + m2));
        d = 1.0 + aa * d;
        d = 1.0 / (if d.abs() > tiny { d } else { tiny });
        c = 1.0 + aa / c;
        c = if c.abs() > tiny { c } else { tiny };
        let step = d * c;
        h *= step;
        if (step - 1.0).abs() < 1e-15 {
            break;
        }
    }
    h
}

fn betai(a: f64, b: f64, x: f64) -> f64 {
    if x <= 0.0 {
        return 0.0;
    }
    if x >= 1.0 {
        return 1.0;
    }
    let front = (lgamma(a + b) - lgamma(a) - lgamma(b) + a * x.ln() + b * (-x).ln_1p()).exp();
    if x < (a + 1.0) / (a + b + 2.0) {
        return front * betacf(a, b, x) / a;
    }
    1.0 - front * betacf(b, a, 1.0 - x) / b
}

pub fn t_cdf(t: f64, df: f64) -> f64 {
    let tail = 0.5 * betai(df / 2.0, 0.5, df / (df + t * t));
    if t > 0.0 {
        1.0 - tail
    } else {
        tail
    }
}

/// The quantile of Student's t for p above 0.5.
pub fn t_ppf(p: f64, df: f64) -> f64 {
    let (mut lo, mut hi) = (0.0f64, 1.0f64);
    while t_cdf(hi, df) < p && hi < 1e12 {
        lo = hi;
        hi *= 2.0;
    }
    for _ in 0..200 {
        let mid = (lo + hi) / 2.0;
        if t_cdf(mid, df) < p {
            lo = mid;
        } else {
            hi = mid;
        }
        if hi - lo < 1e-13 * 1f64.max(hi) {
            break;
        }
    }
    (lo + hi) / 2.0
}

// ---------------------------------------------------------------- intervals

/// (mean, lo, hi, why there is no interval): a t interval over independent units, from 3 of them.
pub fn mean_interval(values: &[f64], level: f64) -> (f64, Option<f64>, Option<f64>, String) {
    let n = values.len();
    let est = fmean(values);
    if n < MIN_UNITS_FOR_INTERVAL {
        return (est, None, None, format!("{n} unit{}: rb gives an interval from {MIN_UNITS_FOR_INTERVAL}", if n == 1 { "" } else { "s" }));
    }
    let sd = stdev(values);
    if sd == 0.0 {
        return (est, Some(est), Some(est), String::new());
    }
    let half = t_ppf(1.0 - (1.0 - level) / 2.0, (n - 1) as f64) * sd / (n as f64).sqrt();
    (est, Some(est - half), Some(est + half), String::new())
}

/// Welch's interval for two arms never measured on the same unit, from 3 units in each.
pub fn welch_interval(xs: &[f64], ys: &[f64], level: f64) -> (f64, Option<f64>, Option<f64>, String) {
    let est = fmean(xs) - fmean(ys);
    if xs.len() < MIN_UNITS_FOR_INTERVAL || ys.len() < MIN_UNITS_FOR_INTERVAL {
        return (
            est,
            None,
            None,
            format!(
                "{} and {} units in the two arms, never paired: rb gives an unpaired interval from {MIN_UNITS_FOR_INTERVAL} in each",
                xs.len(),
                ys.len()
            ),
        );
    }
    let vx = variance(xs) / xs.len() as f64;
    let vy = variance(ys) / ys.len() as f64;
    let se = (vx + vy).sqrt();
    if se == 0.0 {
        return (est, Some(est), Some(est), String::new());
    }
    let df = (vx + vy).powi(2) / ((vx.powi(2)) / (xs.len() - 1) as f64 + (vy.powi(2)) / (ys.len() - 1) as f64);
    let half = t_ppf(1.0 - (1.0 - level) / 2.0, df) * se;
    (est, Some(est - half), Some(est + half), String::new())
}

// ---------------------------------------------------------------- the result and the rule

pub fn result_of(r: Option<&Reading>, direction: &str, compares: bool, equivalence: Option<f64>) -> &'static str {
    let r = match r {
        Some(r) if r.estimate.is_some() => r,
        _ => return "untested",
    };
    let (lo, hi) = match (r.lo, r.hi) {
        (Some(lo), Some(hi)) => (lo, hi),
        _ => return "point_only",
    };
    if !compares {
        return "measured";
    }
    if let Some(e) = equivalence {
        if -e < lo && hi < e {
            return "equivalent";
        }
    }
    let above = if lo > 0.0 {
        true
    } else if hi < 0.0 {
        false
    } else {
        return "unresolved";
    };
    if direction != "minimize" && direction != "maximize" {
        return if above { "higher" } else { "lower" };
    }
    if above == (direction == "maximize") {
        "gain"
    } else {
        "loss"
    }
}

pub fn close(a: f64, b: f64) -> f64 {
    1e-12 * 1f64.max(a.abs()).max(b.abs())
}

fn threshold(c: &Criterion, value: f64) -> (bool, f64) {
    let target = c.target.unwrap_or(0.0);
    let margin = if c.comparator == "equals" {
        c.tolerance.unwrap_or(0.0) - (value - target).abs()
    } else if c.comparator == "at_most" {
        target - value
    } else {
        value - target
    };
    let margin = if margin.is_finite() { margin } else { 1.7976931348623157e308f64.copysign(margin) };
    (margin >= -close(value, target), margin)
}

pub fn g(x: f64) -> String {
    fmt_float(x, "g")
}

/// A reading's number, to 4 significant digits.
pub fn n4(x: Option<f64>, signed: bool) -> String {
    match x {
        None => "?".into(),
        Some(x) => fmt_float(x, if signed { "+.4g" } else { ".4g" }),
    }
}

/// The significant digits a declared number takes to read back as itself, 6 at least as `g` says it, and 15 at most:
/// every decimal of 15 digits reads back, and more say only the residue of working a number out (5.4 * 0.01 is 0.054,
/// never 0.054000000000000006).
pub fn declared_digits(x: f64) -> usize {
    (6..=15).find(|k| fmt_float(x, &format!(".{k}g")).parse::<f64>() == Ok(x)).unwrap_or(15)
}

/// A rule's own number as it was declared: 1234.5678, never 1234.57.
pub fn declared(x: f64) -> String {
    fmt_float(x, &format!(".{}g", declared_digits(x)))
}

/// A rule's number as `declared` says it, with the 16th and 17th digits it leaves out where they are needed to read back
/// as itself: what a confirmation lists, where two numbers a key tells apart never read alike.
pub fn exact(x: f64) -> String {
    let k = (6..=17).find(|k| fmt_float(x, &format!(".{k}g")).parse::<f64>() == Ok(x)).unwrap_or(17);
    fmt_float(x, &format!(".{k}g"))
}

/// The significant digits an edge a rule's numbers make takes, 6 at least: the fewest that say it as those numbers make
/// it, without the residue of working it out (3846.6 - 0.05 is 3846.55, never 3846.5499999999997).
fn edge_digits(x: f64) -> usize {
    (6..17).find(|k| fmt_float(x, &format!(".{k}g")).parse::<f64>().map_or(false, |v| (v - x).abs() <= close(v, x))).unwrap_or(17)
}

fn rule_number(x: Option<f64>) -> String {
    x.map(declared).unwrap_or_else(|| "None".into())
}

fn rule_value(c: &Criterion) -> String {
    let t = rule_number(c.target);
    if c.comparator == "equals" {
        return format!("{t} ± {}", rule_number(c.tolerance));
    }
    t
}

fn versus(c: &Criterion, ok: bool) -> String {
    if c.comparator == "equals" {
        return format!("{} {}", if ok { "within" } else { "outside" }, rule_value(c));
    }
    if c.comparator == "at_most" {
        return format!("{} {}", if ok { "≤" } else { ">" }, rule_value(c));
    }
    format!("{} {}", if ok { "≥" } else { "<" }, rule_value(c))
}

/// What a printed number stands for: itself when it reads back as the number, else every number that rounds to it,
/// half a unit of its last digit either side.
fn stands_for(said: &str, x: f64) -> (f64, f64) {
    let Ok(v) = said.parse::<f64>() else { return (x, x) };
    if v == x || !x.is_finite() {
        return (v, v);
    }
    let body = said.trim_start_matches(['+', '-']);
    let (mantissa, exponent) = body.split_once('e').unwrap_or((body, "0"));
    let decimals = mantissa.split_once('.').map_or(0, |(_, d)| d.len()) as i32;
    let half = 0.5 * 10f64.powi(exponent.parse::<i32>().unwrap_or(0) - decimals);
    (v - half, v + half)
}

/// Whether a printed number stands only for numbers on its own number's side of every edge, each edge as it is and as
/// the rule's printed numbers make it: (true, shown).
fn keeps_side(said: &str, x: f64, edges: &[(f64, f64)]) -> bool {
    let (lo, hi) = stands_for(said, x);
    edges.iter().all(|&(m, shown)| if x < m { hi < shown } else { lo > shown })
}

/// A reading's numbers (`xs`, signed when `signed`) and a rule's own (`rule`), the rule's said as declared and the
/// reading's with the fewest digits that keep them in their true order against the edges the rule's numbers make
/// (`edges`: a band's target ∓ tolerance, a threshold itself): from 4 significant digits, as a comparison says one, more
/// while fewer would leave a reading's number standing for one on another side of an edge (3846.6046 within 3846.6 ±
/// 0.05 is 3846.60, not 3847 or 3846.6); 17 always keep it. A number the rule reads as on an edge (within `close`) is
/// said as that edge is: a threshold as declared, a band's edge with the digits the reading's numbers took, 6 at least,
/// or as many as its target and tolerance make it take.
pub fn in_order(xs: &[f64], rule: &[f64], edges: fn(&[f64]) -> Vec<f64>, signed: bool) -> (Vec<String>, Vec<String>) {
    in_order_from(xs, rule, edges, signed, 4)
}

/// in_order, from `from` significant digits rather than 4.
fn in_order_from(xs: &[f64], rule: &[f64], edges: fn(&[f64]) -> Vec<f64>, signed: bool, from: usize) -> (Vec<String>, Vec<String>) {
    let sign = if signed { "+" } else { "" };
    let true_edges = edges(rule);
    let said_rule: Vec<String> = rule.iter().map(|m| declared(*m)).collect();
    for (bound, edge) in [(4, 6), (6, 6), (8, 8), (10, 10), (12, 12), (17, 17)].map(|(bound, edge)| (bound.max(from.min(17)), edge.max(from.min(17)))) {
        let shown = edges(&said_rule.iter().map(|t| t.parse().unwrap_or(f64::NAN)).collect::<Vec<f64>>());
        let pairs: Vec<(f64, f64)> = true_edges.iter().copied().zip(shown).collect();
        let kept = pairs.iter().enumerate().all(|(i, a)| pairs[i + 1..].iter().all(|b| a.0.partial_cmp(&b.0) == a.1.partial_cmp(&b.1)));
        // the trailing zeros of a number rounded to `bound` digits say how close it was read
        let said: Vec<Option<String>> = xs
            .iter()
            .map(|x| {
                if let Some(&(_, on)) = pairs.iter().find(|(m, _)| (x - m).abs() <= close(*x, *m)) {
                    let k = if rule.iter().any(|r| r.abs() == on.abs()) { declared_digits(on) } else { edge.max(edge_digits(on)) };
                    return Some(fmt_float(on, &format!("{sign}.{k}g")));
                }
                let plain = fmt_float(*x, &format!("{sign}.{bound}g"));
                if keeps_side(&plain, *x, &pairs) {
                    return Some(plain);
                }
                let full = fmt_float(*x, &format!("{sign}#.{bound}g")).trim_end_matches('.').to_string();
                (full != plain && keeps_side(&full, *x, &pairs)).then_some(full)
            })
            .collect();
        if bound == 17 || (kept && said.iter().all(|s| s.is_some())) {
            let said = xs.iter().zip(said).map(|(x, s)| s.unwrap_or_else(|| fmt_float(*x, &format!("{sign}.17g")))).collect();
            return (said, said_rule);
        }
    }
    unreachable!("17 digits keep every order")
}

/// The numbers of a reading a rule reads against a band or threshold, as its rule sentence says them: [estimate, lo,
/// hi], None for a number it does not read.
#[allow(clippy::too_many_arguments)]
pub fn rule_numbers_of(comparator: &str, over: &str, target: Option<f64>, tolerance: Option<f64>, margin: Option<f64>, direction: &str, r: &Reading) -> [Option<String>; 3] {
    let none = [None, None, None];
    let Some(est) = r.estimate else { return none };
    let (lo, hi) = (r.lo, r.hi);
    // the rule's own numbers as its sentence says them, and the edges they make
    type Edges = fn(&[f64]) -> Vec<f64>;
    let (xs, at, rule, edges, signed): (Vec<f64>, Vec<usize>, Vec<f64>, Edges, bool) = if THRESHOLDS.contains(&comparator) {
        let t = target.unwrap_or(0.0);
        let tol = if comparator == "equals" { tolerance.unwrap_or(0.0) } else { 0.0 };
        let (rule, edges): (Vec<f64>, Edges) = if tol != 0.0 { (vec![t, tol], |n| vec![n[0] - n[1], n[0] + n[1]]) } else { (vec![t], |n| n.to_vec()) };
        let signed = !r.reference.is_empty();
        match (over, lo, hi) {
            ("each", _, _) => return none,
            ("bound", Some(_), Some(hi)) if comparator == "at_most" => (vec![hi], vec![2], rule, edges, signed),
            ("bound", Some(lo), Some(_)) if comparator == "at_least" => (vec![lo], vec![1], rule, edges, signed),
            ("bound", Some(lo), Some(hi)) => (vec![lo, hi], vec![1, 2], rule, edges, signed),
            ("bound", _, _) => return none,
            _ => (vec![est], vec![0], rule, edges, signed),
        }
    } else {
        let (Some(lo), Some(hi)) = (lo, hi) else { return none };
        let m = vec![margin.unwrap_or(0.0)];
        match (comparator, direction) {
            ("equivalent", _) => (vec![lo, hi], vec![1, 2], m, |n| vec![-n[0], n[0]], true),
            ("noninferior", "minimize") => (vec![hi], vec![2], m, |n| n.to_vec(), true),
            ("noninferior", "maximize") => (vec![lo], vec![1], m, |n| vec![-n[0]], true),
            _ => return none,
        }
    };
    let (said, _) = in_order(&xs, &rule, edges, signed);
    let mut out: [Option<String>; 3] = [None, None, None];
    for (i, s) in at.into_iter().zip(said) {
        out[i] = Some(s);
    }
    out
}

/// rule_numbers_of for a criterion.
pub fn rule_numbers(c: &Criterion, r: &Reading, direction: &str) -> [Option<String>; 3] {
    rule_numbers_of(&c.comparator, &c.over, c.target, c.tolerance, c.margin, direction, r)
}

/// The units an `--each` rule finds outside its threshold, as the observations name them.
pub fn missed_units(c: &Criterion, values: &[(String, f64)]) -> Vec<String> {
    values.iter().filter(|(_, v)| !threshold(c, *v).0).map(|(u, _)| u.clone()).collect()
}

/// An observation's value as rb show and a finding's card say it: an arm's level as `g` says it, and a run's difference
/// or level read for the criterion as `observed` says it.
pub fn observation_said(c: &Criterion, o: &crate::record::Observation) -> String {
    if o.arm.is_empty() { observed(c, o.value, c.compares()) } else { fmt_float(o.value, "g") }
}

/// A run's value as an observation says it: as `g` does, and for an `--each` rule, which judges each unit's value,
/// with the digits that keep it on its own side of the rule's edges where `g` would not (21.0000004, never 21).
pub fn observed(c: &Criterion, x: f64, signed: bool) -> String {
    let plain = fmt_float(x, if signed { "+g" } else { "g" });
    if c.over != "each" || !THRESHOLDS.contains(&c.comparator.as_str()) {
        return plain;
    }
    let t = c.target.unwrap_or(0.0);
    let tol = if c.comparator == "equals" { c.tolerance.unwrap_or(0.0) } else { 0.0 };
    type Edges = fn(&[f64]) -> Vec<f64>;
    let (rule, edges): (Vec<f64>, Edges) = if tol != 0.0 { (vec![t, tol], |n| vec![n[0] - n[1], n[0] + n[1]]) } else { (vec![t], |n| n.to_vec()) };
    let pairs: Vec<(f64, f64)> = edges(&rule).into_iter().map(|e| (e, e)).collect();
    if pairs.iter().all(|(m, _)| (x - m).abs() > close(x, *m)) && keeps_side(&plain, x, &pairs) {
        return plain;
    }
    in_order(&[x], &rule, edges, signed).0.remove(0)
}

/// Units by their values, up to three: 'seed=3, seed=7' ; 'width=512, loops=2; width=768, loops=4'.
fn units_said(units: &[String]) -> String {
    let said: Vec<String> = units.iter().map(|u| u.replace(" · ", ", ")).collect();
    first_three(&said, if said.iter().any(|u| u.contains(", ")) { "; " } else { ", " })
}

/// (outcome, why, margin) of a criterion's rule on a reading.
pub fn judge(c: &Criterion, r: Option<&Reading>, direction: &str, unit_values: Option<&[(String, f64)]>) -> (&'static str, String, Option<f64>) {
    let r = match r {
        Some(r) if r.estimate.is_some() => r,
        _ => return ("untested", "no reading yet".into(), None),
    };
    if let Some(n) = r.n {
        if n < c.min_n {
            return ("undetermined", format!("{n} unit{}; the criterion needs {}", if n == 1 { "" } else { "s" }, c.min_n), None);
        }
    }
    let iv = r.lo.is_some() && r.hi.is_some();
    let no_iv = if r.why_no_interval.is_empty() { "the reading has no interval".to_string() } else { r.why_no_interval.clone() };
    let signed = !r.reference.is_empty();
    let said = rule_numbers(c, r, direction);
    let say = |i: usize, x: f64, sign: bool| said[i].clone().unwrap_or_else(|| n4(Some(x), sign));
    if THRESHOLDS.contains(&c.comparator.as_str()) {
        if c.over == "each" {
            // across fixed design cells it judges every unit within its cell, never a cell's mean
            let cells = r.design.get("count").and_then(Value::as_i64);
            let Some(values) = unit_values else {
                // runs give each unit's value only where both arms share it
                let why = match cells {
                    Some(_) => "the arms never share a unit in a design cell, so no unit there has a difference to judge",
                    None if r.source == "runs" => "the arms never share a unit, so no unit has a difference to judge",
                    None => "each unit's value is read from runs; an analysis reports none",
                };
                return ("undetermined", why.into(), None);
            };
            let judged: Vec<(bool, f64)> = values.iter().map(|(_, v)| threshold(c, *v)).collect();
            let margin = judged.iter().map(|(_, m)| *m).fold(f64::INFINITY, f64::min);
            let held = judged.iter().filter(|(ok, _)| *ok).count();
            let within = cells.map(|n| format!(" in {n} design cells")).unwrap_or_default();
            if held == judged.len() {
                let all = match judged.len() {
                    1 => "the only unit".to_string(),
                    2 => format!("both units{within}"),
                    n => format!("all {n} units{within}"),
                };
                return ("meets", format!("{all} {}", versus(c, true)), Some(margin));
            }
            let missed = units_said(&missed_units(c, values));
            let (k, n) = (judged.len() - held, judged.len());
            // said plainly: how many units, where they are, and on which side of the rule they fall
            let of = match cells {
                Some(_) if n > 1 => format!("{},{within},", k_of_n(k, n, "unit", "units")),
                _ => k_of_n(k, n, "unit", "units"),
            };
            let side = match c.comparator.as_str() {
                "at_most" => "above",
                "at_least" => "below",
                _ => "outside",
            };
            let is = if k == 1 { "is" } else { "are" };
            return ("misses", format!("{of} {is} {side} {} ({missed})", rule_value(c)), Some(margin));
        }
        if c.over == "bound" {
            if !iv {
                return ("undetermined", format!("it reads the interval's bound: {no_iv}"), None);
            }
            let (lo, hi) = (r.lo.unwrap(), r.hi.unwrap());
            let (ok, m) = if c.comparator == "at_most" {
                threshold(c, hi)
            } else if c.comparator == "at_least" {
                threshold(c, lo)
            } else {
                let (lo_ok, lo_m) = threshold(c, lo);
                let (hi_ok, hi_m) = threshold(c, hi);
                (lo_ok && hi_ok, lo_m.min(hi_m))
            };
            let shown = if c.comparator == "equals" {
                format!("interval [{}, {}]", say(1, lo, signed), say(2, hi, signed))
            } else if c.comparator == "at_most" {
                format!("upper bound {}", say(2, hi, signed))
            } else {
                format!("lower bound {}", say(1, lo, signed))
            };
            return (if ok { "meets" } else { "misses" }, format!("{shown} {}", versus(c, ok)), Some(m));
        }
        let (ok, m) = threshold(c, r.estimate.unwrap());
        return (if ok { "meets" } else { "misses" }, format!("estimate {} {}", say(0, r.estimate.unwrap(), signed), versus(c, ok)), Some(m));
    }
    if !iv {
        return ("undetermined", format!("a {} rule reads the interval: {no_iv}", c.comparator), None);
    }
    let (lo, hi) = (r.lo.unwrap(), r.hi.unwrap());
    if c.comparator == "equivalent" {
        let m = c.margin.unwrap_or(0.0);
        let ok = -m < lo && hi < m;
        let why = format!(
            "the interval [{}, {}] {} ±{}",
            say(1, lo, true),
            say(2, hi, true),
            if ok { "inside" } else { "is not inside" },
            rule_number(Some(m))
        );
        return (if ok { "meets" } else { "misses" }, why, None);
    }
    if direction != "minimize" && direction != "maximize" {
        let base = c.metric.rsplit('.').next().unwrap_or("");
        return (
            "undetermined",
            format!("a {} rule needs the metric's direction: rb metric add {base} --minimize (or --maximize)", c.comparator),
            None,
        );
    }
    let worse = if direction == "minimize" { hi } else { -lo };
    let allowed = if c.comparator == "better" { 0.0 } else { c.margin.unwrap_or(0.0) };
    let ok = worse < allowed;
    let worse_bound = if direction == "minimize" { say(2, hi, true) } else { say(1, lo, true) };
    let why = if ok {
        if c.comparator == "better" {
            format!("the interval [{}, {}] clear of 0 on the better side", n4(Some(lo), true), n4(Some(hi), true))
        } else {
            format!("the interval's worse bound {worse_bound} inside {}", rule_number(Some(allowed)))
        }
    } else if c.comparator == "better" && lo <= 0.0 && 0.0 <= hi {
        format!("the interval [{}, {}] includes 0", n4(Some(lo), true), n4(Some(hi), true))
    } else if c.comparator == "better" {
        format!("the interval [{}, {}] is on the worse side", n4(Some(lo), true), n4(Some(hi), true))
    } else {
        format!("the interval's worse bound {worse_bound} reaches past {}", rule_number(Some(allowed)))
    };
    (if ok { "meets" } else { "misses" }, why, None)
}

/// The reading's interval and the thresholds a rule read on the estimate has strictly inside it, as a note says them:
/// [20.01, 21.26] and 21, each with the digits that keep them in order (in_order).
pub fn crossing(c: &Criterion, r: &Reading) -> Option<(String, String, Vec<String>)> {
    if !THRESHOLDS.contains(&c.comparator.as_str()) || c.over == "each" || c.over == "bound" {
        return None;
    }
    let (Some(lo), Some(hi), Some(t)) = (r.lo, r.hi, c.target) else {
        return None;
    };
    let tol = c.tolerance.unwrap_or(0.0);
    let band = c.comparator == "equals" && tol != 0.0;
    let edges = if band { vec![t - tol, t + tol] } else { vec![t] };
    let inside: Vec<f64> = edges.into_iter().filter(|e| e - lo > close(lo, *e) && hi - e > close(hi, *e)).collect();
    if inside.is_empty() {
        return None;
    }
    let (mut bounds, marks) = in_order(&[lo, hi], &inside, |n| n.to_vec(), !r.reference.is_empty());
    // a band's edges are worked out from its target and tolerance, and said as they make them
    let marks = if band { inside.iter().map(|e| fmt_float(*e, &format!(".{}g", edge_digits(*e)))).collect() } else { marks };
    let hi = bounds.pop().unwrap_or_default();
    Some((bounds.pop().unwrap_or_default(), hi, marks))
}

// ---------------------------------------------------------------- words

pub fn result_word(r: &str) -> &'static str {
    match r {
        "untested" => "untested",
        "gain" => "gain",
        "loss" => "loss",
        "unresolved" => "unresolved",
        "equivalent" => "equivalent",
        "point_only" => "point only",
        "measured" => "measured",
        "higher" => "higher",
        "lower" => "lower",
        _ => "untested",
    }
}

/// rb's result class as a verdict sentence says it after the reading (PLAIN-WORDS-PASS-4): one phrase each, so the
/// class word never stands alone; "" for none (no reading).
pub fn result_phrase(r: &str) -> &'static str {
    match r {
        "gain" => "A real gain",
        "loss" => "A real loss",
        "unresolved" => "The runs can't tell it from no difference",
        "equivalent" => "The same within the rule's margin",
        "point_only" => "There is no interval yet",
        "measured" => "One arm measured on its own",
        "higher" => "Clearly higher, though no direction is recorded as better",
        "lower" => "Clearly lower, though no direction is recorded as better",
        _ => "",
    }
}

/// A finding's timing in a few words, as timing_word says it, but for a rule written down before its subject's runs
/// after its baseline's: "declared before its runs", as its timing says it.
pub fn timing_said(f: &crate::record::Finding) -> &'static str {
    if f.timing == "before" && f.says.get("timing").map_or(false, |t| t.contains("before its runs")) {
        "declared before its runs"
    } else {
        timing_word(&f.timing)
    }
}

pub fn timing_word(t: &str) -> &'static str {
    match t {
        "before" => "declared before the evidence",
        "after_evidence" => "declared after the evidence existed",
        "after_exposure" => "declared after the data was seen",
        "unknown" => "order unknown",
        _ => "no evidence recorded yet",
    }
}

/// The rule axis as one word or two: 'meets', 'misses', 'gate missed', 'reproduced'.
pub fn rule_word(outcome: &str, role: &str, origin: &str) -> String {
    if role == "gate" {
        return match outcome {
            "meets" => "gate met",
            "misses" => "gate missed",
            "undetermined" => "gate undetermined",
            _ => "gate untested",
        }
        .into();
    }
    if (origin == "cited" || origin == "inherited") && (outcome == "meets" || outcome == "misses") {
        return if outcome == "meets" { "reproduced".into() } else { "not reproduced".into() };
    }
    outcome.to_string()
}

/// The rule axis as a criterion's finding says it: 'criterion met', or 'estimate meets criterion' ('recorded values
/// meet criterion' for an --each rule) when the reading has no interval or crosses the threshold.
pub fn rule_said(c: &Criterion, outcome: &str, reading: Option<&Reading>) -> String {
    let word = rule_word(outcome, &c.role, &c.origin);
    if word == "meets" && reading.map_or(false, |r| r.lo.is_none() || r.hi.is_none() || crossing(c, r).is_some()) {
        return if c.over == "each" { "recorded values meet criterion" } else { "estimate meets criterion" }.into();
    }
    rule_label(&word, 1)
}

/// Human wording for a rule outcome. The serialized outcome and tally keys stay stable.
pub fn rule_label(word: &str, count: i64) -> String {
    let noun = if count == 1 { "criterion" } else { "criteria" };
    match word {
        "meets" => format!("{noun} met"),
        "misses" => format!("{noun} not met"),
        "undetermined" => format!("{noun} undetermined"),
        "untested" => format!("{noun} untested"),
        _ => word.into(),
    }
}

/// '+0.020 [+0.015, +0.025] nats · paired t over 3 runs, 95% (rb)' ; 'no reading yet'.
pub fn reading_text(r: Option<&Reading>, unit_of_metric: &str) -> String {
    reading_text_as(r, unit_of_metric, &Default::default())
}

/// A reading of what a criterion reads, its numbers as the criterion's rule sentence says them.
pub fn reading_for(c: &Criterion, r: &Reading, unit_of_metric: &str, direction: &str) -> String {
    reading_text_as(Some(r), unit_of_metric, &rule_numbers(c, r, direction))
}

/// The range of a reading's fixed design cells' estimates, '12345.6 to 12349.1', '-0.025 to +0.1': its two ends said as
/// `cells_said` says a cell's estimate.
pub fn range_said(c: Option<&Criterion>, r: &Reading) -> Option<String> {
    let (min, max) = (r.design.get("min")?.as_f64()?, r.design.get("max")?.as_f64()?);
    let ends = cells_said(c, r, &[min, max]);
    Some(format!("{} to {}", ends[0], ends[1]))
}

/// Estimates of a reading's fixed design cells (`xs`), each said as a cell's estimate is: to 6 significant digits, with
/// more where the reading's estimate is said with more, where one would otherwise take an exponent the estimate is said
/// without, or where fewer would leave it standing for a number on the other side of a threshold rule's edges
/// (in_order); two that differ never read alike.
pub fn cells_said(c: Option<&Criterion>, r: &Reading, xs: &[f64]) -> Vec<String> {
    let signed = !r.reference.is_empty();
    let said = c.map(|c| rule_numbers(c, r, "")).unwrap_or_default();
    let est = said_all([r.estimate, r.lo, r.hi], &said, signed)[0].clone();
    let digits = |s: &str| s.trim_start_matches(['+', '-']).split('e').next().unwrap_or("").replace('.', "").trim_start_matches('0').len();
    // as many as a number's whole part has, where the estimate is said with no exponent
    let whole = |x: f64| if x == 0.0 || !x.is_finite() || est.contains('e') { 0 } else { x.abs().log10().floor().max(0.0) as usize + 1 };
    let mut from = [6, digits(&est)].into_iter().chain(xs.iter().map(|x| whole(*x))).max().unwrap_or(6).min(17);
    loop {
        let said: Vec<String> = match c.filter(|c| THRESHOLDS.contains(&c.comparator.as_str())) {
            Some(c) => {
                let t = c.target.unwrap_or(0.0);
                let tol = if c.comparator == "equals" { c.tolerance.unwrap_or(0.0) } else { 0.0 };
                type Edges = fn(&[f64]) -> Vec<f64>;
                let (rule, edges): (Vec<f64>, Edges) = if tol != 0.0 { (vec![t, tol], |n| vec![n[0] - n[1], n[0] + n[1]]) } else { (vec![t], |n| n.to_vec()) };
                in_order_from(xs, &rule, edges, signed, from).0
            }
            None => xs.iter().map(|x| fmt_float(*x, &format!("{}.{from}g", if signed { "+" } else { "" }))).collect(),
        };
        let apart = xs.iter().enumerate().all(|(i, a)| xs[i + 1..].iter().zip(&said[i + 1..]).all(|(b, sb)| a == b || said[i] != *sb));
        if from >= 17 || apart {
            return said;
        }
        from += 1;
    }
}

/// A reading's estimate, lo and hi, each as `said` gives it (rule_numbers), else to 4 significant digits as a reading
/// says one, or, when the rule says one with more, to as many as the most it says one with, trailing zeros kept, so no
/// number reads coarser than one the rule says (3846.60 [3846.57, 3846.64] beside an upper bound read as 3846.64, never
/// 3847 [3847, 3846.64]; 241.642 [241.640, 241.644], never [241.64, ...]); a number those digits give exactly gains no
/// zero (+0.1, never +0.1000).
pub fn said_all(xs: [Option<f64>; 3], said: &[Option<String>; 3], signed: bool) -> [String; 3] {
    let digits = |s: &String| s.trim_start_matches(['+', '-']).split('e').next().unwrap_or("").replace('.', "").trim_start_matches('0').len();
    let sign = if signed { "+" } else { "" };
    let fill = |x: f64| match said.iter().flatten().map(digits).max().filter(|k| *k > 4) {
        Some(k) => {
            let plain = fmt_float(x, &format!("{sign}.{k}g"));
            if plain.parse::<f64>() == Ok(x) {
                plain
            } else {
                fmt_float(x, &format!("{sign}#.{k}g")).trim_end_matches('.').to_string()
            }
        }
        None => fmt_float(x, &format!("{sign}.4g")),
    };
    [0, 1, 2].map(|i| said[i].clone().unwrap_or_else(|| xs[i].map_or_else(|| "?".into(), fill)))
}

/// A reading's text in its parts, each as reading_text_as says it: the estimate, the interval ("[lo, hi]", "" with
/// none), the metric's unit, how it was read ("paired difference, t interval, 3 seeds, 95%") and where from ("rb", or
/// an analysis's id). None where there is no reading yet.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct ReadingParts {
    pub estimate: String,
    pub interval: String,
    pub unit: String,
    pub how: String,
    pub source: String,
}

impl ReadingParts {
    /// The estimate, its interval and its unit, as a row says the value: "-0.1567 [-0.2192, -0.09415] %".
    pub fn head(&self) -> String {
        [self.estimate.as_str(), self.interval.as_str(), self.unit.as_str()].into_iter().filter(|x| !x.is_empty()).collect::<Vec<_>>().join(" ")
    }
}

/// reading_text_as in its parts (ReadingParts), from the one rule that says them.
pub fn reading_parts(r: Option<&Reading>, unit_of_metric: &str, said: &[Option<String>; 3]) -> Option<ReadingParts> {
    let r = r.filter(|r| r.estimate.is_some())?;
    let signed = !r.reference.is_empty();
    let [est, lo, hi] = said_all([r.estimate, r.lo, r.hi], said, signed);
    let interval = if r.lo.is_some() && r.hi.is_some() { format!("[{lo}, {hi}]") } else { String::new() };
    let mut how: Vec<String> = Vec::new();
    if !r.method.is_empty() {
        how.push(r.method.clone());
    }
    match (r.n, r.unit.is_empty()) {
        (Some(n), false) => how.push(format!("{n} {}", plural(&r.unit, n))),
        (Some(n), true) => how.push(format!("n {n}")),
        _ => {}
    }
    if let (Some(level), Some(_)) = (r.level, r.lo) {
        how.push(format!("{}%", g(crate::pyfloat::round0(level * 100.0))));
    }
    let source = if r.source == "runs" { "rb".to_string() } else { r.source.clone() };
    Some(ReadingParts { estimate: est, interval, unit: unit_of_metric.to_string(), how: how.join(", "), source })
}

/// reading_text, its estimate, lo and hi said as `said` gives them where it does (rule_numbers).
pub fn reading_text_as(r: Option<&Reading>, unit_of_metric: &str, said: &[Option<String>; 3]) -> String {
    let Some(p) = reading_parts(r, unit_of_metric, said) else { return "no reading yet".into() };
    p.head() + &(if p.how.is_empty() { String::new() } else { format!(" · {}", p.how) }) + &format!(" ({})", p.source)
}

/// Up to three items, then how many more: 'a, b, c and 2 more'.
pub fn first_three(items: &[String], sep: &str) -> String {
    first_few(items, 3, sep)
}

/// k of n things as rb counts them: the only one, both, all n, no one, neither, none of the n, else k of n. A verb after
/// it is singular for k of 0 or 1.
pub fn k_of_n(k: usize, n: usize, one: &str, many: &str) -> String {
    match (k, n) {
        (1, 1) => format!("the only {one}"),
        (2, 2) => format!("both {many}"),
        (k, n) if k == n && n > 2 => format!("all {n} {many}"),
        (0, 1) => format!("no {one}"),
        (0, 2) => format!("neither of the 2 {many}"),
        (0, n) => format!("none of the {n} {many}"),
        (k, n) => format!("{k} of {n} {many}"),
    }
}

/// Up to `k` items, then how many more: 'a, b and 2 more'; 'a; b; and 2 more'.
pub fn first_few(items: &[String], k: usize, sep: &str) -> String {
    let shown = items.iter().take(k).cloned().collect::<Vec<_>>().join(sep);
    match items.len() {
        n if n > k => format!("{shown}{} and {} more", if sep.starts_with(';') { ";" } else { "" }, n - k),
        _ => shown,
    }
}

pub fn plural(word: &str, n: i64) -> String {
    if n == 1 || word.is_empty() {
        return word.to_string();
    }
    if word.ends_with('s') && !word.ends_with("ss") {
        return word.to_string(); // given in the plural already: images, docs
    }
    if word.ends_with('s') || word.ends_with('x') || word.ends_with("ch") || word.ends_with("sh") {
        return format!("{word}es");
    }
    if word.ends_with('y') {
        let before = word.chars().rev().nth(1);
        if before.map_or(true, |c| !"aeiou".contains(c)) {
            return format!("{}ies", &word[..word.len() - 1]);
        }
    }
    format!("{word}s")
}

pub fn is_eval_role(role: &str) -> bool {
    let r = crate::rbyaml::py_strip(role).to_lowercase();
    EVAL_ROLES.iter().any(|x| r.starts_with(x))
}

/// What a decision records it was made on, for one finding.
pub fn basis_of(comparison: Option<&Reading>, result: &str, rule: &str) -> Value {
    let c = comparison;
    let mut basis = crate::dict! {
        "source" => Value::opt_str(c.map(|c| c.source.clone())),
        "estimate" => Value::opt_f64(c.and_then(|c| c.estimate)),
        "lo" => Value::opt_f64(c.and_then(|c| c.lo)),
        "hi" => Value::opt_f64(c.and_then(|c| c.hi)),
        "n" => Value::opt_i64(c.and_then(|c| c.n)),
        "result" => result,
        "rule" => rule,
    };
    if let Some(reading) = c.filter(|r| !r.design.is_empty()) {
        basis.insert("design".into(), Value::Dict(reading.design.clone()));
        basis.insert("unit".into(), Value::Str(reading.unit.clone()));
    }
    Value::Dict(basis)
}

/// basis_of(finding)
pub fn basis_of_finding(f: &crate::record::Finding) -> Value {
    basis_of(f.comparison.as_ref(), &f.result, &f.rule_outcome)
}

/// The reading a decision recorded it was made on, to say its numbers as the criterion's rule does (rule_numbers).
pub fn basis_reading(b: &Value, reference: &str) -> Reading {
    let num = |k: &str| b.get(k).and_then(|v| v.as_f64());
    let mut r = Reading::of("");
    r.reference = reference.into();
    (r.estimate, r.lo, r.hi) = (num("estimate"), num("lo"), num("hi"));
    r
}

/// What a decision was made on, as a reading says it: a level's numbers unsigned, a comparison's signed, each as `said`
/// gives it where it does (rule_numbers), and the rule as the criterion's finding words it (`rule`: criterion not met, a
/// cited number's not reproduced, a gate's gate missed, estimate meets criterion).
pub fn basis_text(b: &Value, signed: bool, rule: &str, said: &[Option<String>; 3]) -> String {
    if let Some(st) = b.get("standing") {
        if st.truthy() {
            return format!("{}, as an earlier rb read it", crate::pyjson::py_str(st).replace('_', " "));
        }
    }
    if !b.truthy() || b.get("estimate").map_or(true, |v| v.is_null()) {
        return "no reading".into();
    }
    let num = |k: &str| b.get(k).and_then(|v| v.as_f64());
    let [est, lo, hi] = said_all([num("estimate"), num("lo"), num("hi")], said, signed);
    let iv = if b.get("lo").map_or(false, |v| !v.is_null()) { format!(" [{lo}, {hi}]") } else { String::new() };
    let n = match b.get("n") {
        Some(v) if !v.is_null() => format!(", n {}", crate::pyjson::py_str(v)),
        _ => String::new(),
    };
    // rb's reading of the runs is said as rb, as a reading says it
    let source = match b.get("source").filter(|v| v.truthy()).map(crate::pyjson::py_str) {
        Some(v) if v != "runs" => v,
        _ => "rb".into(),
    };
    let s = |k: &str| b.get(k).map(crate::pyjson::py_str).unwrap_or_default();
    format!("{est}{iv}{n} ({source}): {}, {rule}", result_word(&s("result")))
}

/// The rule a decision's basis records, as the criterion's finding words it on those numbers.
pub fn basis_rule(b: &Value, c: Option<&Criterion>, reference: &str) -> String {
    let outcome = b.get("rule").map(crate::pyjson::py_str).unwrap_or_default();
    match c {
        Some(c) => rule_said(c, &outcome, Some(&basis_reading(b, reference))),
        None => rule_label(&rule_word(&outcome, "", ""), 1),
    }
}
