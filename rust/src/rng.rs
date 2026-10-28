//! Randomness for ids. RB_TEST_SEED makes it a deterministic stream, so the golden-record harness can hold what rb
//! writes byte for byte; otherwise it is the operating system's.

use std::sync::atomic::{AtomicU64, Ordering};

static COUNTER: AtomicU64 = AtomicU64::new(0);

fn os_bytes(buf: &mut [u8]) {
    if crate::sys::random_bytes(buf) {
        return;
    }
    // a last resort that is still unpredictable enough for ids: time and address entropy through sha256
    let t = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_nanos()).unwrap_or(0);
    let seed = format!("{t}:{:p}:{}", buf, std::process::id());
    let h = crate::rbyaml::sha256_hex(seed.as_bytes());
    for (k, b) in buf.iter_mut().enumerate() {
        *b = u8::from_str_radix(&h[(2 * k) % 64..(2 * k) % 64 + 2], 16).unwrap_or(0);
    }
}

/// `bits` random bits (at most 128), as secrets.randbits gives them.
pub fn randbits(bits: u32) -> u128 {
    let bytes = ((bits + 7) / 8) as usize;
    let mut buf = vec![0u8; bytes];
    match std::env::var("RB_TEST_SEED") {
        Ok(seed) if !seed.is_empty() => {
            let n = COUNTER.fetch_add(1, Ordering::Relaxed);
            let h = crate::rbyaml::sha256_hex(format!("{seed}:{n}").as_bytes());
            for (k, b) in buf.iter_mut().enumerate() {
                *b = u8::from_str_radix(&h[2 * k..2 * k + 2], 16).unwrap_or(0);
            }
        }
        _ => os_bytes(&mut buf),
    }
    let mut v: u128 = 0;
    for b in &buf {
        v = (v << 8) | (*b as u128);
    }
    let extra = (bytes as u32) * 8 - bits;
    v >> extra
}

/// Random bits from the operating system, whatever RB_TEST_SEED says: for names nobody keeps (temporary files), so they
/// never shift the deterministic stream ids are drawn from.
pub fn os_randbits(bits: u32) -> u128 {
    let bytes = ((bits + 7) / 8) as usize;
    let mut buf = vec![0u8; bytes];
    os_bytes(&mut buf);
    let mut v: u128 = 0;
    for b in &buf {
        v = (v << 8) | (*b as u128);
    }
    v >> ((bytes as u32) * 8 - bits)
}

/// secrets.choice(seq) for a sequence of at most 256 items: uniform, by rejection.
pub fn choice<T: Clone>(items: &[T]) -> T {
    let n = items.len() as u128;
    let bits = 128 - (n - 1).leading_zeros().min(127);
    loop {
        let r = randbits(bits.max(1));
        if r < n {
            return items[r as usize].clone();
        }
    }
}
