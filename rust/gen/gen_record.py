"""Generate rust/src/record_gen.rs from the pydantic models in rabbit_brain/record.py: one struct per model, with its
fields in pydantic's order, a `validate` that gives pydantic's errors (same locations, types and messages), a `dump`
that is model_dump(mode="json"), and the field metadata `stored()` and `ordered()` need. Custom validators are hooks,
written by hand in rust/src/record.rs."""
import os
import sys
import typing

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "src"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pydantic import BaseModel
from pydantic.fields import FieldInfo
from rustlit import lit

from rabbit_brain import record

MODELS = ["Retraction", "Written", "Resolution", "Source", "Cited", "Conflict", "Setting", "Variant", "Freeze", "Amendment", "Note",
          "Relation", "RepoFile", "Quote", "Check", "Indexed", "Experiment", "Metric", "QuestionLink", "CriterionRelation", "Criterion", "FileRef", "ConfigCheck", "RowRead", "Agent", "Receipt",
          "ResourceUse", "Run", "Acquisition", "Location", "Exposure", "Resource", "Comparison", "Analysis", "Question", "CallSource", "Past", "Decision",
          "Parent", "Project", "Caveat", "Reading", "Observation", "Declared", "Review", "Units", "Finding"]

SETTING_VALUE = record.SettingValue


def is_model(t):
    return isinstance(t, type) and issubclass(t, BaseModel)


def strip_annotated(t):
    if typing.get_origin(t) is typing.Annotated:
        return typing.get_args(t)[0]
    return t


def classify(ann):
    """(rust type, helper, extra args) for an annotation."""
    if ann == SETTING_VALUE:
        return "Value", "f_setting_value", None
    origin = typing.get_origin(ann)
    args = typing.get_args(ann)
    if origin is typing.Union and type(None) in args:
        inner = [a for a in args if a is not type(None)]
        assert len(inner) == 1, ann
        rt, helper, extra = classify(inner[0])
        return f"Option<{rt}>", helper.replace("f_", "f_opt_", 1), extra
    if ann is str:
        return "String", "f_str", None
    if ann is int:
        return "i64", "f_int", None
    if ann is float:
        return "f64", "f_float", None
    if ann is bool:
        return "bool", "f_bool", None
    if ann is typing.Any:
        return "Value", "f_any", None
    if ann is dict or (origin is dict and args[1] is typing.Any):
        return "Dict", "f_dict_any", None
    if origin is dict and args[1] is float:
        return "IndexMap<String, f64>", "f_dict_float", None
    if origin is dict and args[1] is str:
        return "IndexMap<String, String>", "f_dict_str", None
    if origin is typing.Literal:
        return "String", "f_lit", list(args)
    if origin is list:
        (item,) = args
        if item is str:
            return "Vec<String>", "f_list_str", None
        if item is dict:
            return "Vec<Dict>", "f_list_dict", None
        if is_model(item):
            return f"Vec<{item.__name__}>", "f_list_model", item.__name__
    if is_model(ann):
        return ann.__name__, "f_model", ann.__name__
    raise TypeError(f"unhandled annotation {ann!r}")


def constraints(f: FieldInfo):
    out = []
    for m in f.metadata:
        n = type(m).__name__
        if n == "_PydanticGeneralMetadata" and getattr(m, "pattern", None):
            out.append(f"C::Pattern({lit(m.pattern)})")
        elif n == "MinLen":
            out.append(f"C::MinLen({m.min_length})")
        elif n == "MaxLen":
            out.append(f"C::MaxLen({m.max_length})")
        elif n == "Ge":
            out.append(f"C::Ge({float(m.ge)!r}, {lit(repr(m.ge))})")
        elif n == "Gt":
            out.append(f"C::Gt({float(m.gt)!r}, {lit(repr(m.gt))})")
        elif n == "Le":
            out.append(f"C::Le({float(m.le)!r}, {lit(repr(m.le))})")
        elif n == "Lt":
            out.append(f"C::Lt({float(m.lt)!r}, {lit(repr(m.lt))})")
        else:
            raise TypeError(f"unhandled metadata {m!r}")
    return out


def default_expr(rt, f: FieldInfo, helper):
    """The Rust expression of a field's default, for a field that is not required."""
    if f.default_factory is not None:
        made = f.default_factory()
        if isinstance(made, dict):
            if made:   # Receipt.produced
                items = ", ".join(f"{lit(k)} => Value::from({('Value::Null' if v is None else lit(v))})" for k, v in made.items())
                return f"crate::dict!{{{items}}}"
            return "Default::default()"
        if isinstance(made, list):
            return "Vec::new()"
        if isinstance(made, BaseModel):
            return f"{type(made).__name__}::default_value()"
        raise TypeError(made)
    d = f.default
    if d is None:
        return "None" if rt.startswith("Option") else "Value::Null"
    if isinstance(d, bool):
        return "true" if d else "false"
    if isinstance(d, int):
        return f"{d}" if rt == "i64" else f"{float(d)!r}"
    if isinstance(d, float):
        return f"{d!r}"
    if isinstance(d, str):
        return f"{lit(d)}.to_string()"
    raise TypeError(repr(d))


def rust_ident(name):
    return {"type": "r#type", "ref": "r#ref", "move": "r#move", "where": "r#where"}.get(name, name)


def gen_model(name):
    m = getattr(record, name)
    extra = m.model_config.get("extra")
    fvals = m.__pydantic_decorators__.field_validators
    field_hooks = {}
    for vname, dec in fvals.items():
        for fld in dec.info.fields:
            field_hooks.setdefault(fld, []).append(vname)
    model_hooks = list(m.__pydantic_decorators__.model_validators)
    lines = []
    fields = list(m.model_fields.items())
    lines.append("#[derive(Debug, Clone, PartialEq)]")
    lines.append(f"pub struct {name} {{")
    for fname, f in fields:
        rt, helper, extra_arg = classify(f.annotation)
        lines.append(f"    pub {rust_ident(fname)}: {rt},")
    if extra == "allow":
        lines.append("    pub extra: Dict,")
    lines.append("}")
    lines.append("")
    lines.append(f"impl {name} {{")
    # metadata
    metas = []
    for fname, f in fields:
        rt, helper, extra_arg = classify(f.annotation)
        sub = None
        if helper in ("f_model", "f_opt_model", "f_list_model"):
            sub = extra_arg
        sub_s = f"Some(&{sub}::META)" if sub else "None"
        metas.append(f"FieldMeta {{ name: {lit(fname)}, required: {'true' if f.is_required() else 'false'}, sub: {sub_s} }}")
    written = issubclass(m, record.Written)
    lines.append(f"    pub const META: ModelMeta = ModelMeta {{ name: {lit(name)}, written: {'true' if written else 'false'}, fields: &[")
    for x in metas:
        lines.append(f"        {x},")
    lines.append("    ] };")
    lines.append("")
    # validate
    lines.append(f"    pub fn validate(v: &Value, loc: &mut Loc, errs: &mut Vec<VErr>) -> Option<{name}> {{")
    lines.append(f"        let d = model_dict(v, {lit(name)}, loc, errs)?;")
    lines.append("        let start = errs.len();")
    for fname, f in fields:
        rt, helper, extra_arg = classify(f.annotation)
        req = "true" if f.is_required() else "false"
        cons = constraints(f)
        cons_s = "&[" + ", ".join(cons) + "]"
        if f.is_required():
            dflt = {"String": "String::new()", "i64": "0", "f64": "0.0", "bool": "false", "Value": "Value::Null"}.get(rt, "Default::default()")
            if helper == "f_model":
                dflt = "None"
        else:
            dflt = default_expr(rt, f, helper)
            if helper == "f_model":
                dflt = f"Some({dflt})"
        var = f"f_{fname}"
        if helper in ("f_lit", "f_opt_lit"):
            choices = "&[" + ", ".join(lit(c) for c in extra_arg) + "]"
            call = f"{helper}(d, {lit(fname)}, {req}, {dflt}, {choices}, loc, errs)"
        elif helper in ("f_model", "f_opt_model", "f_list_model"):
            call = f"{helper}::<{extra_arg}>(d, {lit(fname)}, {req}, {dflt}, {cons_s}, loc, errs, {extra_arg}::validate)"
        else:
            call = f"{helper}(d, {lit(fname)}, {req}, {dflt}, {cons_s}, loc, errs)"
        lines.append(f"        let mut {var} = {call};")
        if field_hooks.get(fname):
            lines.append(f"        if d.contains_key({lit(fname)}) {{ if let Some(x) = {var}.as_mut() {{ if let Err(msg) = hooks::{name.lower()}_{fname}(x) {{ errs.push(value_error(loc, Some({lit(fname)}), &msg)); {var} = None; }} }} }}")
    known = ", ".join(lit(fn) for fn, _ in fields)
    if extra == "allow":
        lines.append(f"        let extra = extra_allowed(d, &[{known}]);")
    else:
        lines.append(f"        extra_forbidden(d, &[{known}], loc, errs);")
    lines.append("        if errs.len() > start {")
    lines.append("            return None;")
    lines.append("        }")
    lines.append(f"        let mut m = {name} {{")
    for fname, f in fields:
        lines.append(f"            {rust_ident(fname)}: f_{fname}.unwrap(),")
    if extra == "allow":
        lines.append("            extra,")
    lines.append("        };")
    if model_hooks:
        assert len(model_hooks) == 1, (name, model_hooks)
        lines.append(f"        if let Err(msg) = hooks::{name.lower()}_after(&mut m) {{ errs.push(value_error(loc, None, &msg)); return None; }}")
    lines.append("        Some(m)")
    lines.append("    }")
    lines.append("")
    # dump
    lines.append("    /// model_dump(mode=\"json\"), every field; exclude_none drops the ones that are None, at every level of model.")
    lines.append("    pub fn dump(&self, exclude_none: bool) -> Dict {")
    lines.append("        let mut d = Dict::new();")
    for fname, f in fields:
        rt, helper, extra_arg = classify(f.annotation)
        ident = rust_ident(fname)
        key = lit(fname)
        if rt.startswith("Option<"):
            inner = rt[len("Option<"):-1]
            if helper == "f_opt_model":
                conv = "Value::Dict(x.dump(exclude_none))"
            elif inner == "String":
                conv = "Value::Str(x.clone())"
            elif inner == "i64":
                conv = "Value::Int(*x)"
            elif inner == "f64":
                conv = "Value::Float(*x)"
            elif inner == "bool":
                conv = "Value::Bool(*x)"
            elif inner == "Dict":
                conv = "Value::Dict(x.clone())"
            else:
                raise TypeError(rt)
            lines.append(f"        match &self.{ident} {{ Some(x) => {{ d.insert({key}.into(), {conv}); }} None => {{ if !exclude_none {{ d.insert({key}.into(), Value::Null); }} }} }}")
        elif rt == "Value":
            lines.append(f"        if !(exclude_none && self.{ident}.is_null()) {{ d.insert({key}.into(), self.{ident}.clone()); }}")
        elif rt == "String":
            lines.append(f"        d.insert({key}.into(), Value::Str(self.{ident}.clone()));")
        elif rt == "i64":
            lines.append(f"        d.insert({key}.into(), Value::Int(self.{ident}));")
        elif rt == "f64":
            lines.append(f"        d.insert({key}.into(), Value::Float(self.{ident}));")
        elif rt == "bool":
            lines.append(f"        d.insert({key}.into(), Value::Bool(self.{ident}));")
        elif rt == "Dict":
            lines.append(f"        d.insert({key}.into(), Value::Dict(self.{ident}.clone()));")
        elif rt == "IndexMap<String, f64>":
            lines.append(f"        d.insert({key}.into(), Value::Dict(self.{ident}.iter().map(|(k, x)| (k.clone(), Value::Float(*x))).collect()));")
        elif rt == "IndexMap<String, String>":
            lines.append(f"        d.insert({key}.into(), Value::Dict(self.{ident}.iter().map(|(k, x)| (k.clone(), Value::Str(x.clone()))).collect()));")
        elif rt == "Vec<String>":
            lines.append(f"        d.insert({key}.into(), Value::list_of_str(self.{ident}.clone()));")
        elif rt == "Vec<Dict>":
            lines.append(f"        d.insert({key}.into(), Value::List(self.{ident}.iter().map(|x| Value::Dict(x.clone())).collect()));")
        elif rt.startswith("Vec<"):
            lines.append(f"        d.insert({key}.into(), Value::List(self.{ident}.iter().map(|x| Value::Dict(x.dump(exclude_none))).collect()));")
        elif helper == "f_model":
            lines.append(f"        d.insert({key}.into(), Value::Dict(self.{ident}.dump(exclude_none)));")
        else:
            raise TypeError(rt)
    if extra == "allow":
        lines.append("        for (k, v) in &self.extra { if !(exclude_none && v.is_null()) { d.insert(k.clone(), v.clone()); } }")
    lines.append("        d")
    lines.append("    }")
    lines.append("")
    lines.append("    pub fn to_value(&self) -> Value {")
    lines.append("        Value::Dict(self.dump(false))")
    lines.append("    }")
    # default value for models whose every field has a default
    if all(not f.is_required() for _, f in fields):
        lines.append("")
        lines.append(f"    pub fn default_value() -> {name} {{")
        lines.append(f"        {name} {{")
        for fname, f in fields:
            rt, helper, extra_arg = classify(f.annotation)
            lines.append(f"            {rust_ident(fname)}: {default_expr(rt, f, helper)},")
        if extra == "allow":
            lines.append("            extra: Dict::new(),")
        lines.append("        }")
        lines.append("    }")
    lines.append("}")
    lines.append("")
    return lines


OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "record_gen.rs")


def text() -> str:
    """The file's whole text, as the models give it now."""
    out = ["// Generated by rust/gen/gen_record.py from rabbit_brain/record.py. Do not edit: edit the generator or the hooks.",
           "#![allow(clippy::all, unused_mut, unused_variables)]", "", "use crate::model::*;", "use crate::record::hooks;", "use crate::value::{Dict, Value};",
           "use indexmap::IndexMap;", ""]
    for name in MODELS:
        out += gen_model(name)
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(text())
    print(len(MODELS), "models")
