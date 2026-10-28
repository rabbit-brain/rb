//! The finding's figure (SHELL-SPEC section 5.1.1): server-drawn SVG, so it shows in every host, with no script and in
//! print. The shaded band is what the rule accepts, read from the criterion (`check_band`); the interval is a bar with
//! caps and the estimate a dot, or, across fixed design cells, a dot per cell and a tick at the mean. Neutral ink only:
//! no mark carries a standing's colour. The positions are geometry computed here; the browser computes nothing.

use crate::shell::nodes;
use crate::value::Value;

/// The figure of a finding's reading, with rb's legend line under it when `legend` is true; nothing where the reading
/// has no number to place.
pub fn draw(f: &Value, legend: bool) -> String {
    let num = |key: &str| at(f, key).and_then(Value::as_f64).filter(|v| v.is_finite());
    let (est, lo, hi) = (num("reading.estimate"), num("reading.lo"), num("reading.hi"));
    let cells: Vec<(String, f64, bool)> = list(f, "design_cells")
        .iter()
        .filter_map(|c| {
            // rb writes a cell's estimate as a number; a typographic minus reads as the minus it is
            let v = text(c, "estimate_text").trim().replace('\u{2212}', "-").parse::<f64>().ok().filter(|v| v.is_finite())?;
            Some((text(c, "key").to_string(), v, at(c, "outside").and_then(Value::as_bool).unwrap_or(false)))
        })
        .collect();
    // the figure computes nothing: across cells it marks each cell, and rb's own estimate where rb gives one
    if est.is_none() && cells.is_empty() {
        return String::new();
    }
    let band = Band::of(f);
    // a difference has its 0 on the axis, where "no difference" is (plain words pass 4)
    let compared = nodes::compared(f);
    let mut dom: Vec<f64> = cells.iter().map(|c| c.1).chain([est, lo, hi].into_iter().flatten()).collect();
    if let Some(b) = &band {
        dom.extend([b.lo, b.hi].into_iter().filter(|v| v.is_finite()));
        dom.push(b.tick);
    }
    if compared {
        dom.push(0.0);
    }
    let (mut a, mut b) = dom.iter().fold((f64::INFINITY, f64::NEG_INFINITY), |(a, b), v| (a.min(*v), b.max(*v)));
    let span = if b > a { b - a } else if a != 0.0 { a.abs() } else { 1.0 };
    a -= span * 0.14;
    b += span * 0.14;
    if !(b - a).is_finite() || b <= a {
        return String::new();
    }
    const W: f64 = 400.0;
    const X0: f64 = 10.0;
    const X1: f64 = 390.0;
    // each label's width, near enough for the figure's 11px text, so a label stays inside the figure
    let wide = |t: &str| t.chars().count() as f64 * 6.2;
    let x = |v: f64| X0 + (v - a) / (b - a) * (X1 - X0);
    // rows above the band: the rule's label, then (across design cells) the cell outside the rule
    let rule_row = if band.is_some() { 14.0 } else { 0.0 };
    let cells_row = if cells.is_empty() { 0.0 } else { 14.0 };
    let top = rule_row + cells_row;
    let y = 22.0 + top;
    let mut p = vec![format!("<line class=\"ax\" x1=\"{X0}\" y1=\"{y}\" x2=\"{X1}\" y2=\"{y}\"/>")];
    // the axis's marks: the rule's number, and 0 for a difference, each a tick with its number under it
    let said = |v: f64| nodes::fixed(v, nodes::digits(v), false);
    let mut ticks: Vec<(f64, String)> = Vec::new();
    if let Some(band) = &band {
        let (ra, rb) = (band.lo.max(a), band.hi.min(b));
        p.push(format!(
            "<rect class=\"rg\" x=\"{:.1}\" y=\"{}\" width=\"{:.1}\" height=\"32\" rx=\"3\"/>",
            x(ra),
            6.0 + top,
            (x(rb) - x(ra)).max(1.0)
        ));
        for edge in [band.lo, band.hi].into_iter().filter(|v| v.is_finite()) {
            p.push(format!("<line class=\"rge\" x1=\"{0:.1}\" y1=\"{1}\" x2=\"{0:.1}\" y2=\"{2}\"/>", x(edge), 6.0 + top, 38.0 + top));
        }
        ticks.push((band.tick, said(band.tick)));
        // the shaded part says what it is, its numbers with the unit the card's verdict says them in: "the rule: 5
        // points or more" (FIX4)
        let rule = |v: f64| nodes::rule_number_said(f, v);
        let words = match band.side {
            Side::AtLeast => format!("the rule: {} or more", rule(band.tick)),
            Side::AtMost => format!("the rule: {} or less", rule(band.tick)),
            Side::Within(e) => format!("the rule: {} \u{b1} {}", rule(band.tick), rule(e)),
        };
        let tx = x(band.tick);
        let wd = wide(&words);
        let lx = match band.side {
            Side::AtLeast => (tx + 4.0).min(W - 4.0 - wd).max(4.0),
            Side::AtMost => (tx - 4.0 - wd).max(4.0).min(W - 4.0 - wd),
            Side::Within(_) => (tx - wd / 2.0).max(4.0).min(W - 4.0 - wd),
        };
        p.push(format!("<text class=\"bl\" x=\"{lx:.1}\" y=\"10\" text-anchor=\"start\">{}</text>", esc(&words)));
    }
    if compared && !ticks.iter().any(|(v, _)| *v == 0.0) {
        ticks.push((0.0, "0".to_string()));
    }
    let placed: Vec<f64> = Vec::new();
    let mut placed = placed;
    for (v, words) in &ticks {
        let tx = x(*v);
        p.push(format!("<line class=\"tk\" x1=\"{0:.1}\" y1=\"{1}\" x2=\"{0:.1}\" y2=\"{2}\"/>", tx, 34.0 + top, 40.0 + top));
        // a number too close to one already said is left to its tick: the rule's number is said first
        if placed.iter().any(|q| (q - tx).abs() < 28.0) {
            continue;
        }
        placed.push(tx);
        let anchor = if tx < 30.0 { "start" } else if tx > 370.0 { "end" } else { "middle" };
        p.push(format!("<text x=\"{tx:.1}\" y=\"{}\" text-anchor=\"{anchor}\">{}</text>", 52.0 + top, esc(words)));
    }
    if cells.is_empty() {
        if let (Some(lo), Some(hi)) = (lo, hi) {
            p.push(format!("<line class=\"iv\" x1=\"{:.1}\" y1=\"{y}\" x2=\"{:.1}\" y2=\"{y}\"/>", x(lo), x(hi)));
            for v in [lo, hi] {
                p.push(format!("<line class=\"cap\" x1=\"{0:.1}\" y1=\"{1}\" x2=\"{0:.1}\" y2=\"{2}\"/>", x(v), y - 5.0, y + 5.0));
            }
        }
        if let Some(est) = est {
            p.push(format!("<circle class=\"est\" cx=\"{:.1}\" cy=\"{y}\" r=\"5\"/>", x(est)));
            // the dot says its value, in the digits the card's sentence says it in
            let v = nodes::fixed(est, nodes::reading_digits(f), false);
            let ex = x(est).max(4.0 + wide(&v) / 2.0).min(W - 4.0 - wide(&v) / 2.0);
            p.push(format!("<text class=\"k\" x=\"{ex:.1}\" y=\"{}\" text-anchor=\"middle\">{}</text>", y - 9.0, esc(&v)));
        }
    } else {
        for (i, (_, v, out)) in cells.iter().enumerate() {
            let cy = if i % 2 == 1 { y - 6.0 } else { y + 6.0 };
            p.push(format!(
                "<circle class=\"cell{}\" cx=\"{:.1}\" cy=\"{cy}\" r=\"{}\"/>",
                if *out { " out" } else { "" },
                x(*v),
                if *out { 4.4 } else { 3.3 }
            ));
            if *out {
                p.push(format!("<line class=\"tk\" x1=\"{0:.1}\" y1=\"{1}\" x2=\"{0:.1}\" y2=\"{2}\"/>", x(*v), y - 12.0, rule_row + 11.0));
            }
        }
        // one label over the cells outside the rule, kept inside the figure: the cell's key where one is outside, else
        // how many are (each cell's own key and value are in the line beside the figure)
        let outside: Vec<&(String, f64, bool)> = cells.iter().filter(|c| c.2).collect();
        if let Some(first) = outside.first() {
            let said = if outside.len() == 1 { nodes::cell_said(&first.0) } else { format!("{} cells outside the rule", outside.len()) };
            let width = said.chars().count() as f64 * 6.8;
            let lx = (x(first.1) + 4.0).min(W - 4.0 - width).max(4.0);
            p.push(format!("<text class=\"k\" x=\"{lx:.1}\" y=\"{}\" text-anchor=\"start\">{}</text>", rule_row + 9.0, esc(&said)));
        }
        if let Some(est) = est {
            p.push(format!(
                "<line class=\"tk\" x1=\"{0:.1}\" y1=\"{1}\" x2=\"{0:.1}\" y2=\"{2}\"/>",
                x(est),
                y - 12.0,
                y + 12.0
            ));
        }
    }
    let shaded = if band.is_some() { "shaded: what the rule accepts \u{b7} " } else { "" };
    let leg = if !cells.is_empty() {
        let tick = if est.is_some() { " \u{b7} tick: the estimate" } else { "" };
        format!("{shaded}dots: design cells \u{b7} filled: a cell outside the rule{tick}")
    } else if lo.is_some() && hi.is_some() {
        format!("{shaded}dot: estimate \u{b7} bar: interval")
    } else {
        format!("{shaded}dot: estimate (no interval)")
    };
    // what each mark is, in words beside the SVG, where a reader and a screen reader both get it: no raw-text element
    // (a title) goes inside the SVG, which the hub's gate refuses. A reading's estimate and interval are on its card
    // already; across cells, each cell's key and value, and rb's estimate where it gives one, are said here.
    let values = if cells.is_empty() {
        String::new()
    } else {
        let shown: Vec<&Value> = list(f, "design_cells").iter().filter(|c| !text(c, "key").is_empty() && !text(c, "estimate_text").is_empty()).collect();
        // in words and in two decimals said alike across the cells and the estimate (DESIGN-PASS-1 P5, P8)
        let mut texts: Vec<&str> = shown.iter().map(|c| text(c, "estimate_text")).collect();
        if est.is_some() {
            texts.push(text(f, "reading.estimate_text"));
        }
        // signed only where the reading is a difference of two arms; a level is a number as it is
        let nums = nodes::two_decimals(&texts, nodes::compared(f));
        let mut said: Vec<String> = shown
            .iter()
            .zip(&nums)
            .map(|(c, n)| {
                let out = if at(c, "outside").and_then(Value::as_bool).unwrap_or(false) { " (outside the rule)" } else { "" };
                format!("{}: {}{out}", esc(&nodes::cell_said(text(c, "key"))), esc(n))
            })
            .collect();
        if est.is_some() {
            said.push(format!("estimate: {}", esc(nums.last().map_or("", String::as_str))));
        }
        format!("<p class=\"figleg figv\">{}</p>", said.join(" \u{b7} "))
    };
    // the rule's verdict, in the one sentence the card says it in (FIX2): never rb's rule line
    let aria = nodes::sentence_text(&nodes::verdict(f));
    format!(
        "<div class=\"figw\"><svg class=\"fig\" viewBox=\"0 0 {W} {}\" role=\"img\" aria-label=\"{}\">{}</svg>{values}{}</div>",
        58.0 + top,
        esc(&aria),
        p.concat(),
        if legend { format!("<p class=\"figleg\">{leg}</p>") } else { String::new() }
    )
}

/// What the rule accepts, on the reading's axis: from `lo` to `hi` (either may be unbounded), with its tick.
struct Band {
    lo: f64,
    hi: f64,
    tick: f64,
    side: Side,
}

/// Which side of its number a rule accepts: at least it, at most it, or within a tolerance of it.
#[derive(Clone, Copy)]
enum Side {
    AtLeast,
    AtMost,
    Within(f64),
}

impl Band {
    /// The band of a finding's criterion (`check_band`): "at least" a target, "at most" one, "within" a tolerance of
    /// one. A comparator whose side rb does not hand the figure (a direction rule) draws no band: the figure never
    /// guesses which side is better.
    fn of(f: &Value) -> Option<Band> {
        let target = at(f, "check_band.target").and_then(Value::as_f64).filter(|v| v.is_finite())?;
        let tolerance = at(f, "check_band.tolerance").and_then(Value::as_f64).filter(|v| v.is_finite());
        match text(f, "check_band.comparator") {
            "at least" => Some(Band { lo: target, hi: f64::INFINITY, tick: target, side: Side::AtLeast }),
            "at most" => Some(Band { lo: f64::NEG_INFINITY, hi: target, tick: target, side: Side::AtMost }),
            "within" | "equals" => tolerance.map(|t| Band { lo: target - t.abs(), hi: target + t.abs(), tick: target, side: Side::Within(t.abs()) }),
            _ => None,
        }
    }
}

fn at<'a>(v: &'a Value, key: &str) -> Option<&'a Value> {
    key.split('.').try_fold(v, |v, k| v.get(k)).filter(|v| !v.is_null())
}

fn text<'a>(v: &'a Value, key: &str) -> &'a str {
    at(v, key).and_then(Value::as_str).unwrap_or("")
}

fn list<'a>(v: &'a Value, key: &str) -> &'a [Value] {
    at(v, key).and_then(Value::as_list).map_or(&[], Vec::as_slice)
}

fn esc(t: &str) -> String {
    t.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;").replace('"', "&quot;").replace('\'', "&#x27;")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn finding(id: &str) -> Value {
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../tests/fixtures/shell/int8-demo.owner.view.json");
        let doc = crate::pyjson::loads(&std::fs::read_to_string(path).unwrap()).unwrap();
        doc.get("findings").unwrap().get(id).unwrap().clone()
    }

    /// The x of each mark of `class` in `svg`: a circle's cx, a line's x1 (and x2), a rect's x and x+width.
    fn xs(svg: &str, class: &str) -> Vec<f64> {
        let mut out = Vec::new();
        for el in svg.split('<').filter(|e| e.contains(&format!("class=\"{class}\""))) {
            let attr = |name: &str| el.split(&format!(" {name}=\"")).nth(1).and_then(|r| r.split('"').next()).and_then(|v| v.parse::<f64>().ok());
            if let Some(cx) = attr("cx") {
                out.push(cx);
            }
            if let (Some(x), Some(w)) = (attr("x"), attr("width")) {
                out.extend([x, x + w]);
            }
            if let Some(x1) = attr("x1") {
                out.push(x1);
                out.extend(attr("x2"));
            }
        }
        out
    }

    /// c1, "at least -0.01" with its interval clear above it: the band starts at the target and runs right past the
    /// interval; the interval's bar spans lo to hi with the estimate inside, in that order on the axis.
    #[test]
    fn the_band_is_what_the_rule_accepts() {
        let svg = draw(&finding("c1"), true);
        let band = xs(&svg, "rg");
        let iv = xs(&svg, "iv");
        let est = xs(&svg, "est");
        assert_eq!((band.len(), iv.len(), est.len()), (2, 2, 1), "{svg}");
        assert!(band[0] < iv[0] && iv[0] < est[0] && est[0] < iv[1] && iv[1] <= band[1], "{band:?} {iv:?} {est:?}");
        // the figure is described by the card's own status sentence, never rb's rule line (DESIGN-PASS-1 P8)
        assert!(svg.contains("aria-label=\"Met: INT8 is 0.0029 lower than the FP32 baseline (-0.0046 to -0.0012), 0.0071 past the -0.01 it asks for.\""), "{svg}");
        // the axis says the rule's number and 0, the shaded part what the rule is, the dot its value (plain words pass 4)
        assert!(svg.contains(">the rule: -0.01 or more</text>") && svg.contains(">-0.01</text>") && svg.contains(">0</text>"), "{svg}");
        assert!(svg.contains("class=\"k\"") && svg.contains(">-0.0029</text>"), "{svg}");
        assert!(svg.contains("shaded: what the rule accepts \u{b7} dot: estimate \u{b7} bar: interval"));
        // c6, "at most 0.6" with its one run above it: the band ends at the target, left of the estimate, and no bar
        let svg = draw(&finding("c6"), true);
        assert!(xs(&svg, "rg")[1] < xs(&svg, "est")[0] && xs(&svg, "iv").is_empty(), "{svg}");
        assert!(svg.contains("dot: estimate (no interval)"));
        // c2, "within 0.001 of 0.7613": a band around the target, the estimate outside it
        let svg = draw(&finding("c2"), false);
        let (band, est) = (xs(&svg, "rg"), xs(&svg, "est")[0]);
        assert!(est < band[0], "{svg}");
        assert_eq!(xs(&svg, "rge").len(), 4, "both edges dashed");
        assert!(!svg.contains("figleg"));
    }

    /// The rule's band says what it is in its number as declared ("the rule: 5 or more", "0.6 or less", "0.7613 ± 0.001"),
    /// and the axis marks the number; 0 is on the axis of a difference alone, never a level's.
    #[test]
    fn the_axis_says_the_rule_and_its_number() {
        let mut f = finding("c1");
        for (cmp, target, tol, label) in [("at least", 5.0, None, "the rule: 5 or more"), ("at most", 0.6, None, "the rule: 0.6 or less"), ("within", 0.7613, Some(0.001), "the rule: 0.7613 \u{b1} 0.001")] {
            let band = f.as_dict_mut().unwrap().get_mut("check_band").unwrap().as_dict_mut().unwrap();
            band.insert("comparator".into(), Value::str(cmp));
            band.insert("target".into(), Value::Float(target));
            band.insert("tolerance".into(), tol.map_or(Value::Null, Value::Float));
            let svg = draw(&f, false);
            assert!(svg.contains(&format!(">{label}</text>")) && svg.contains(&format!(">{}</text>", nodes::fixed(target, nodes::digits(target), false))), "{label}: {svg}");
            // every label stays inside the figure
            for el in svg.split("<text ").skip(1) {
                let x: f64 = el.split("x=\"").nth(1).unwrap().split('"').next().unwrap().parse().unwrap();
                assert!((0.0..=400.0).contains(&x), "{el}");
            }
        }
        // c2 reads a level, fp32's own top1: no 0 on its axis
        assert!(!draw(&finding("c2"), false).contains(">0</text>"));
    }

    /// Across design cells: a dot per cell, the one outside the rule filled and named, a tick at the mean.
    #[test]
    fn design_cells_are_dots_and_the_one_outside_is_named() {
        let svg = draw(&finding("c5"), true);
        assert_eq!(svg.matches("class=\"cell\"").count(), 3, "{svg}");
        assert_eq!(svg.matches("class=\"cell out\"").count(), 1);
        // in words, never rb's key (DESIGN-PASS-1 P8)
        assert!(svg.contains(">calib images 128</text>"), "{svg}");
        // each cell's key and value, beside the SVG, the one outside the rule said so, the values in digits said alike
        let beside = svg.split("</svg>").nth(1).unwrap();
        assert!(beside.contains("calib images 128: -0.0184 (outside the rule)") && beside.contains("calib images 512: -0.0043"), "{beside}");
        assert!(draw(&finding("c5"), false).contains("calib images 128: -0.0184 (outside the rule)"), "the call's figure says them too");
        let out = xs(&svg, "cell out")[0];
        assert!(out < xs(&svg, "rg")[0], "the cell outside sits left of the band at -0.01");
        // rb gives c5 no one estimate across its cells: the figure draws no tick and computes no mean
        assert!(!svg.contains("tick:") && !svg.contains("the mean"), "{svg}");
    }

    fn with_cells(cells: &[(&str, &str, bool)], estimate: Option<f64>) -> Value {
        let mut f = finding("c5");
        let d = f.as_dict_mut().unwrap();
        let list: Vec<Value> = cells
            .iter()
            .map(|(k, e, out)| crate::obj! {"key" => *k, "estimate_text" => *e, "runs" => Vec::<Value>::new(), "outside" => *out})
            .collect();
        d.insert("design_cells".into(), Value::List(list));
        d.get_mut("reading").unwrap().as_dict_mut().unwrap().insert("estimate".into(), estimate.map_or(Value::Null, Value::Float));
        d.get_mut("reading").unwrap().as_dict_mut().unwrap().insert("estimate_text".into(), Value::str(estimate.map_or(String::new(), |e| e.to_string())));
        f
    }

    /// Where rb gives an estimate across cells, the tick sits at it, said beside the figure; a typographic minus is a minus;
    /// labels of cells outside the rule never overlap or leave the figure.
    #[test]
    fn cells_mark_rbs_estimate_and_their_labels_stay_inside() {
        let svg = draw(&with_cells(&[("a=1", "1", false), ("a=2", "3", false)], Some(2.5)), true);
        let tick = xs(&svg, "tk");
        let (cells, est_x) = (xs(&svg, "cell"), tick[tick.len() - 1]);
        // 2.5 sits three quarters of the way from the cell at 1 to the cell at 3, not at their mean, 2
        assert!((est_x - (cells[0] + 0.75 * (cells[1] - cells[0]))).abs() < 0.2, "{svg}");
        assert!(svg.split("</svg>").nth(1).unwrap().contains("a 1: +1.00 \u{b7} a 2: +3.00 \u{b7} estimate: +2.50") && svg.contains("tick: the estimate"), "{svg}");
        let svg = draw(&with_cells(&[("a=1", "\u{2212}1", true), ("a=2", "3", false)], None), true);
        assert_eq!(svg.matches("class=\"cell out\"").count(), 1, "{svg}");
        let long = "calib_images=2048 \u{b7} seed_group=dddd";
        let svg = draw(&with_cells(&[(long, "-1", true), ("b", "-1.01", true), ("c", "-0.99", true), ("d", "5", false)], None), true);
        assert_eq!(svg.matches("<text class=\"k\"").count(), 1, "{svg}");
        assert!(svg.contains(">3 cells outside the rule</text>"));
        let svg = draw(&with_cells(&[("d", "-5", false), (long, "9", true)], None), true);
        let label = svg.split("<text class=\"k\" x=\"").nth(1).unwrap();
        let lx: f64 = label.split('"').next().unwrap().parse().unwrap();
        let said = nodes::cell_said(long);
        assert!(svg.contains(&format!(">{said}</text>")), "{svg}");
        assert!(lx >= 4.0 && lx + said.chars().count() as f64 * 6.8 <= 396.0, "{lx}");
    }

    /// No raw-text element inside the SVG (the hub's gate refuses a title, a style or a script there, Oct 21): what a
    /// mark's title said is on the card or in the line beside the figure, and the SVG keeps its role and label.
    #[test]
    fn nothing_inside_the_svg_is_raw_text() {
        let raw = ["style", "script", "title", "textarea", "xmp", "iframe", "noembed", "noframes", "noscript", "plaintext"];
        let mut figures: Vec<String> = ["c1", "c2", "c3", "c4", "c5", "c6"].iter().flat_map(|id| [draw(&finding(id), true), draw(&finding(id), false)]).collect();
        figures.push(draw(&with_cells(&[("a=1", "1", true), ("a=2", "3", false)], Some(2.5)), true));
        for fig in figures.iter().filter(|f| !f.is_empty()) {
            let svg = fig.split("<svg").nth(1).unwrap().split("</svg>").next().unwrap();
            for name in raw {
                assert!(!svg.to_lowercase().contains(&format!("<{name}")), "<{name}> in {fig}");
            }
            assert!(svg.contains("role=\"img\" aria-label=\""), "{fig}");
        }
    }

    /// Numbers too large to place draw no figure, never NaN.
    #[test]
    fn a_reading_too_large_to_place_draws_nothing() {
        let mut f = finding("c1");
        let r = f.as_dict_mut().unwrap().get_mut("reading").unwrap().as_dict_mut().unwrap();
        r.insert("estimate".into(), Value::Float(1e308));
        r.insert("lo".into(), Value::Float(-1e308));
        r.insert("hi".into(), Value::Float(1.5e308));
        assert_eq!(draw(&f, true), "");
    }

    /// The figure is neutral and draws nothing it cannot place: no standing colour, no style, no script; no number,
    /// no figure; a comparator with no side handed over, no band.
    #[test]
    fn the_figure_is_neutral_and_never_guesses() {
        for id in ["c1", "c2", "c3", "c4", "c5", "c6"] {
            let svg = draw(&finding(id), true);
            for banned in ["good", "bad", "warn", "style=", "fill=", "stroke=", "<script", "\u{2014}"] {
                assert!(!svg.contains(banned), "{id}: {banned}");
            }
        }
        let mut f = finding("c1");
        f.as_dict_mut().unwrap().get_mut("check_band").unwrap().as_dict_mut().unwrap().insert("comparator".into(), Value::str("better"));
        let svg = draw(&f, true);
        assert!(!svg.contains("class=\"rg\"") && svg.contains(">dot: estimate \u{b7} bar: interval<"), "{svg}");
        let mut f = finding("c1");
        f.as_dict_mut().unwrap().insert("reading".into(), Value::Null);
        assert_eq!(draw(&f, true), "");
    }
}
