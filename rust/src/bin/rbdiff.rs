//! A line protocol for the differential tests (`rust/tests_py/diff_*.py`), which hold rb's own code to a second
//! implementation: one JSON request per line on stdin, one JSON answer per line on stdout. Development only; never
//! shipped.
use rb::pyjson::{self, Dumps};
use rb::value::Value;
use std::io::{BufRead, Write};

fn f(v: &Value, k: &str) -> f64 {
    // floats travel as their bits, in hex, so nothing is lost on the way
    let s = v.get_str(k).unwrap();
    f64::from_bits(u64::from_str_radix(s, 16).unwrap())
}

fn answer(req: &Value) -> Value {
    let op = req.get_str("op").unwrap_or("");
    match op {
        "repr" => Value::Str(rb::pyfloat::repr(f(req, "x"))),
        "fmt" => Value::Str(rb::pyfloat::fmt_float(f(req, "x"), req.get_str("spec").unwrap())),
        "round" => {
            let r = rb::pyfloat::round_to(f(req, "x"), req.get("n").and_then(|n| n.as_i64()).unwrap() as i32);
            Value::Str(format!("{:016x}", r.to_bits()))
        }
        "parse_float" => match rb::pyfloat::parse_float(req.get_str("s").unwrap()) {
            Some(x) => Value::Str(format!("{:016x}", x.to_bits())),
            None => Value::Null,
        },
        "json" => {
            let text = req.get_str("text").unwrap();
            match pyjson::loads(text) {
                Ok(v) => {
                    let mut out = rb::dict!();
                    out.insert("default".into(), Value::Str(pyjson::dumps(&v)));
                    out.insert("canonical".into(), Value::Str(pyjson::dumps_with(&v, &Dumps::canonical()).unwrap_or_default()));
                    out.insert("indent1".into(), Value::Str(pyjson::dumps_with(&v, &Dumps::indent(1)).unwrap_or_default()));
                    out.insert("str".into(), Value::Str(pyjson::py_str(&v)));
                    Value::Dict(out)
                }
                Err(e) => Value::Str(format!("ERROR {e}")),
            }
        }
        "yaml_load" => match rb::rbyaml::load(req.get_str("text").unwrap(), req.get_str("source").unwrap_or("")) {
            Ok(doc) => {
                let mut out = rb::dict!();
                out.insert("data".into(), Value::Str(pyjson::dumps(&doc.data)));
                out.insert("comments".into(), comments_value(&doc.comments));
                match rb::rbyaml::dumps(&doc.data, Some(&doc.comments)) {
                    Ok(t) => out.insert("fmt".into(), Value::Str(t)),
                    Err(e) => out.insert("fmt".into(), Value::Str(format!("ERROR {}", e.0))),
                };
                Value::Dict(out)
            }
            Err(e) => Value::Str(format!("ERROR {e}")),
        },
        "yaml_dumps" => {
            let data = pyjson::loads(req.get_str("data").unwrap()).unwrap();
            match rb::rbyaml::dumps(&data, None) {
                Ok(t) => Value::Str(t),
                Err(e) => Value::Str(format!("ERROR {}", e.0)),
            }
        }
        "argv" => {
            let argv: Vec<String> = req.get("argv").unwrap().as_list().unwrap().iter().map(|v| v.as_str().unwrap().to_string()).collect();
            match rb::argparse::rb_parser().parse_args(&argv) {
                Ok(ns) => rb::obj! {"ns" => Value::Dict(ns)},
                Err(rb::argparse::Stop::Usage(u)) => rb::obj! {"usage_error" => u.message, "usage" => u.usage},
                Err(rb::argparse::Stop::Exit(code, text)) => rb::obj! {"exit" => code as i64, "out" => text},
            }
        }
        "validate" => {
            use rb::record::*;
            let data = pyjson::loads(req.get_str("data").unwrap()).unwrap();
            let mut errs = Vec::new();
            let mut loc = Vec::new();
            let stored: Option<rb::value::Dict> = match req.get_str("model").unwrap() {
                "Project" => Project::validate(&data, &mut loc, &mut errs).map(|m| m.stored()),
                "Question" => Question::validate(&data, &mut loc, &mut errs).map(|m| m.stored()),
                "Experiment" => Experiment::validate(&data, &mut loc, &mut errs).map(|m| m.stored()),
                "Metric" => Metric::validate(&data, &mut loc, &mut errs).map(|m| m.stored()),
                "Criterion" => Criterion::validate(&data, &mut loc, &mut errs).map(|m| m.stored()),
                "Run" => Run::validate(&data, &mut loc, &mut errs).map(|m| m.stored()),
                "Analysis" => Analysis::validate(&data, &mut loc, &mut errs).map(|m| m.stored()),
                "Resource" => Resource::validate(&data, &mut loc, &mut errs).map(|m| m.stored()),
                "Decision" => Decision::validate(&data, &mut loc, &mut errs).map(|m| m.stored()),
                other => panic!("model {other}"),
            };
            match stored {
                Some(d) => rb::obj! {"stored" => pyjson::dumps(&Value::Dict(d))},
                None => Value::List(errs.iter().map(|e| rb::list![e.loc_text(), e.typ, e.msg.clone()]).collect()),
            }
        }
        "pyyaml" => {
            let text = req.get_str("text").unwrap();
            let data = match rb::pyyaml::load(text) {
                Ok(v) => Value::Str(pyjson::dumps(&v)),
                Err(_) => Value::Str("ERROR".into()),
            };
            let line = req.get_str("key").map(|k| {
                let steps: Vec<String> = k.split('.').map(|s| s.to_string()).collect();
                Value::from(rb::pyyaml::line_of(text, &steps).map(|l| l as i64))
            });
            let at = req.get("line").and_then(|l| l.as_i64()).map(|l| Value::from(rb::pyyaml::key_at(text, l as usize)));
            rb::obj! {"data" => data, "line" => line.unwrap_or(Value::Null), "key_at" => at.unwrap_or(Value::Null)}
        }
        "stats" => {
            let xs: Vec<f64> = req.get("xs").unwrap().as_list().unwrap().iter().map(|v| f64::from_bits(u64::from_str_radix(v.as_str().unwrap(), 16).unwrap())).collect();
            let ys: Vec<f64> = req.get("ys").unwrap().as_list().unwrap().iter().map(|v| f64::from_bits(u64::from_str_radix(v.as_str().unwrap(), 16).unwrap())).collect();
            let b = |x: f64| Value::Str(format!("{:016x}", x.to_bits()));
            let ob = |x: Option<f64>| x.map(|v| Value::Str(format!("{:016x}", v.to_bits()))).unwrap_or(Value::Null);
            let (e, lo, hi, why) = rb::assess::mean_interval(&xs, 0.95);
            let mut out = rb::obj! {"fmean" => b(rb::assess::fmean(&xs)), "mi" => rb::list![b(e), ob(lo), ob(hi), why]};
            if xs.len() >= 2 {
                out.as_dict_mut().unwrap().insert("stdev".into(), b(rb::assess::stdev(&xs)));
                out.as_dict_mut().unwrap().insert("variance".into(), b(rb::assess::variance(&xs)));
            }
            if !ys.is_empty() {
                let (e, lo, hi, why) = rb::assess::welch_interval(&xs, &ys, 0.95);
                out.as_dict_mut().unwrap().insert("wi".into(), rb::list![b(e), ob(lo), ob(hi), why]);
            }
            let df = req.get("df").and_then(|v| v.as_f64()).unwrap_or(3.0);
            out.as_dict_mut().unwrap().insert("tppf".into(), b(rb::assess::t_ppf(0.975, df)));
            out.as_dict_mut().unwrap().insert("lgamma".into(), b(rb::assess::lgamma(df / 2.0 + 0.5)));
            out
        }
        _ => Value::Str(format!("unknown op {op}")),
    }
}

fn step_value(s: &rb::rbyaml::Step) -> Value {
    use rb::rbyaml::Step;
    match s {
        Step::Key(k) => rb::list!["k", k.clone()],
        Step::Index(i) => rb::list!["i", *i],
        Step::Named(sc, v) => rb::list!["n", *sc, v.clone()],
    }
}

fn comments_value(c: &rb::rbyaml::Comments) -> Value {
    let before: Vec<Value> = c
        .before
        .iter()
        .map(|(p, ls)| Value::List(vec![Value::List(p.iter().map(step_value).collect()), Value::list_of_str(ls.clone())]))
        .collect();
    let after: Vec<Value> = c.after.iter().map(|(p, t)| Value::List(vec![Value::List(p.iter().map(step_value).collect()), Value::Str(t.clone())])).collect();
    rb::obj! {"before" => before, "after" => after, "footer" => Value::list_of_str(c.footer.clone())}
}

fn main() {
    let stdin = std::io::stdin();
    let mut out = std::io::BufWriter::new(std::io::stdout());
    for line in stdin.lock().lines() {
        let line = line.unwrap();
        if line.trim().is_empty() {
            continue;
        }
        let req = pyjson::loads(&line).unwrap();
        let a = answer(&req);
        writeln!(out, "{}", pyjson::dumps(&a)).unwrap();
    }
}
