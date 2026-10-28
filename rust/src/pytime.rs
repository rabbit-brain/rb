//! Times as rb writes them: ISO 8601, UTC, to the millisecond ("2026-10-01T08:07:38.018+00:00"), and read the way
//! Python's datetime.fromisoformat reads them.

use chrono::{DateTime, Datelike, FixedOffset, NaiveDate, NaiveDateTime, NaiveTime, TimeZone, Timelike, Utc};
use std::path::Path;

/// `datetime.isoformat(timespec="milliseconds")` of a UTC time.
pub fn iso_ms(dt: &DateTime<Utc>) -> String {
    format!(
        "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}.{:03}+00:00",
        dt.year(),
        dt.month(),
        dt.day(),
        dt.hour(),
        dt.minute(),
        dt.second(),
        dt.nanosecond() / 1_000_000
    )
}

/// The time now, as rb writes times. RB_TEST_NOW fixes it, for the golden-record harness.
pub fn now() -> String {
    if let Ok(t) = std::env::var("RB_TEST_NOW") {
        if !t.is_empty() {
            return t;
        }
    }
    let n = Utc::now();
    // datetime.now() keeps microseconds; isoformat truncates to milliseconds
    iso_ms(&n)
}

pub fn now_dt() -> DateTime<Utc> {
    if let Ok(t) = std::env::var("RB_TEST_NOW") {
        if let Some(dt) = parse_iso(&t) {
            return dt.with_timezone(&Utc);
        }
    }
    Utc::now()
}

fn digits(s: &str) -> bool {
    !s.is_empty() && s.bytes().all(|b| b.is_ascii_digit())
}

fn parse_date(s: &str) -> Option<NaiveDate> {
    let (y, m, d) = if s.len() == 10 && s.as_bytes()[4] == b'-' && s.as_bytes()[7] == b'-' {
        (&s[0..4], &s[5..7], &s[8..10])
    } else if s.len() == 8 && digits(s) {
        (&s[0..4], &s[4..6], &s[6..8])
    } else {
        return None;
    };
    if !(digits(y) && digits(m) && digits(d)) {
        return None;
    }
    NaiveDate::from_ymd_opt(y.parse().ok()?, m.parse().ok()?, d.parse().ok()?)
}

/// (hour, minute, second, microsecond) of an ISO time without its offset.
fn parse_time(s: &str) -> Option<(u32, u32, u32, u32)> {
    let (main, frac) = match s.find(|c| c == '.' || c == ',') {
        Some(i) => (&s[..i], Some(&s[i + 1..])),
        None => (s, None),
    };
    let parts: Vec<&str> = if main.contains(':') {
        main.split(':').collect()
    } else {
        // the basic form: HH, HHMM, HHMMSS
        if !digits(main) || main.len() % 2 != 0 || main.len() > 6 {
            return None;
        }
        (0..main.len() / 2).map(|k| &main[2 * k..2 * k + 2]).collect()
    };
    if parts.is_empty() || parts.len() > 3 || parts.iter().any(|p| p.len() != 2 || !digits(p)) {
        return None;
    }
    let h: u32 = parts[0].parse().ok()?;
    let m: u32 = if parts.len() > 1 { parts[1].parse().ok()? } else { 0 };
    let sec: u32 = if parts.len() > 2 { parts[2].parse().ok()? } else { 0 };
    let mut us = 0u32;
    if let Some(f) = frac {
        if parts.len() != 3 || !digits(f) {
            return None;
        }
        let take: String = f.chars().take(6).collect();
        let mut v: u32 = take.parse().ok()?;
        for _ in take.len()..6 {
            v *= 10;
        }
        us = v;
    }
    if h > 23 || m > 59 || sec > 59 {
        return None;
    }
    Some((h, m, sec, us))
}

fn parse_offset(s: &str) -> Option<i32> {
    if s == "Z" {
        return Some(0);
    }
    let sign = match s.chars().next()? {
        '+' => 1,
        '-' => -1,
        _ => return None,
    };
    let (h, m, sec, us) = parse_time(&s[1..])?;
    if us != 0 {
        return None;
    }
    Some(sign * (h as i32 * 3600 + m as i32 * 60 + sec as i32))
}

/// datetime.fromisoformat(s): (the naive date-time, its offset in seconds when it has one).
pub fn fromisoformat(s: &str) -> Option<(NaiveDateTime, Option<i32>)> {
    let s = s.trim_end_matches(|c| c == '\0');
    let date_len = if s.len() >= 10 && s.as_bytes().get(4) == Some(&b'-') { 10 } else if s.len() >= 8 { 8 } else { return None };
    let date = parse_date(s.get(..date_len)?)?;
    let rest = &s[date_len..];
    if rest.is_empty() {
        return Some((date.and_hms_opt(0, 0, 0)?, None));
    }
    // any single character separates the date from the time
    let mut it = rest.char_indices();
    it.next()?;
    let tstart = it.next().map(|(i, _)| i).unwrap_or(rest.len());
    let tpart = &rest[tstart..];
    if tpart.is_empty() {
        return None;
    }
    let tz_at = tpart.find(|c| c == '+' || c == '-' || c == 'Z');
    let (tm, tz) = match tz_at {
        Some(i) => (&tpart[..i], Some(&tpart[i..])),
        None => (tpart, None),
    };
    let (h, m, sec, us) = parse_time(tm)?;
    let time = NaiveTime::from_hms_micro_opt(h, m, sec, us)?;
    let off = match tz {
        Some(z) => Some(parse_offset(z)?),
        None => None,
    };
    Some((NaiveDateTime::new(date, time), off))
}

/// A time with an offset (none: UTC), as a fixed-offset date-time.
pub fn parse_iso(s: &str) -> Option<DateTime<FixedOffset>> {
    let s = s.trim();
    let s2;
    let s = if s.ends_with('Z') || s.ends_with('z') {
        s2 = format!("{}+00:00", &s[..s.len() - 1]);
        &s2
    } else {
        s
    };
    let (naive, off) = fromisoformat(s)?;
    let tz = FixedOffset::east_opt(off.unwrap_or(0))?;
    tz.from_local_datetime(&naive).single()
}

/// norm_time: an ISO date or date-time as rb writes times (UTC, to the millisecond). None when it is not ISO 8601.
pub fn norm_time(text: &str) -> Option<String> {
    let dt = parse_iso(text)?;
    Some(iso_ms(&dt.with_timezone(&Utc)))
}

/// Whether time a is strictly earlier than time b; false when either is missing or unreadable.
pub fn before(a: Option<&str>, b: Option<&str>) -> bool {
    let (Some(a), Some(b)) = (a, b) else { return false };
    if a.is_empty() || b.is_empty() {
        return false;
    }
    match (norm_time(a).and_then(|x| parse_iso(&x)), norm_time(b).and_then(|x| parse_iso(&x))) {
        (Some(x), Some(y)) => x < y,
        _ => false,
    }
}

/// A UTC time from a POSIX timestamp the way datetime.fromtimestamp reads a float: microseconds, rounded half to even.
pub fn from_timestamp(sec: i64, nsec: u32) -> Option<DateTime<Utc>> {
    from_float(sec as f64 + nsec as f64 * 1e-9)
}

/// datetime.fromtimestamp(d, tz=timezone.utc) for a float d.
pub fn from_float(d: f64) -> Option<DateTime<Utc>> {
    if !d.is_finite() {
        return None;
    }
    let intpart = d.trunc();
    let mut frac = ((d - intpart) * 1e6).round_ties_even();
    let mut secs = intpart as i64;
    if frac >= 1e6 {
        frac -= 1e6;
        secs += 1;
    }
    if frac < 0.0 {
        frac += 1e6;
        secs -= 1;
    }
    Utc.timestamp_opt(secs, (frac as u32) * 1000).single()
}

/// isoformat(timespec="minutes")[:16]: "2026-10-01T08:07".
pub fn iso_minutes(dt: &DateTime<Utc>) -> String {
    format!("{:04}-{:02}-{:02}T{:02}:{:02}", dt.year(), dt.month(), dt.day(), dt.hour(), dt.minute())
}

/// A file's modification time, as rb writes times.
pub fn file_time(path: &Path) -> Option<String> {
    let md = std::fs::metadata(path).ok()?;
    let (secs, nanos) = crate::sys::mtime_parts(&md);
    let dt = from_timestamp(secs, nanos)?;
    Some(iso_ms(&dt))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn norm() {
        assert_eq!(norm_time("2026-09-30").unwrap(), "2026-09-30T00:00:00.000+00:00");
        assert_eq!(norm_time("2026-09-30T10:30:00Z").unwrap(), "2026-09-30T10:30:00.000+00:00");
        assert_eq!(norm_time("2026-09-30 10:30:00.123456+02:00").unwrap(), "2026-09-30T08:30:00.123+00:00");
        assert!(norm_time("yesterday").is_none());
        assert!(before(Some("2026-09-30"), Some("2026-10-01T00:00:00Z")));
    }
}
