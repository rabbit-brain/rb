//! Zip archives the way rb export writes them and rb clone reads them: deflated entries, no ZIP64 (a bundle is small),
//! and on extraction the names cleaned as Python's zipfile cleans them (no absolute paths, no `.` or `..` parts).

use flate2::write::{DeflateDecoder, DeflateEncoder};
use flate2::{Compression, Crc};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};

/// The most a bundle holds, zipped or unpacked: rb export writes none larger, and rb clone takes none larger.
pub const MAX_BUNDLE: u64 = 512 * 1024 * 1024;

#[derive(Debug)]
pub enum ZipError {
    /// not a zip, or a damaged one: Python's zipfile.BadZipFile
    Bad(String),
    Io(std::io::Error),
    /// the zip itself is over the cap
    TooBig,
    /// its entries say they unpack to more than the cap, together
    Unpacks,
    /// an entry unpacks past the size it declares: that size
    Overflows(u64),
}

impl From<std::io::Error> for ZipError {
    fn from(e: std::io::Error) -> Self {
        ZipError::Io(e)
    }
}

fn dos_time(path: &Path) -> (u16, u16) {
    let secs = std::fs::metadata(path).map(|m| crate::sys::mtime_parts(&m).0).unwrap_or(0);
    let (year, month, day, hour, minute, second) = crate::sys::local_time(secs);
    let year = year.max(1980);
    let date = (((year - 1980) as u16) << 9) | ((month as u16) << 5) | (day as u16);
    let time = ((hour as u16) << 11) | ((minute as u16) << 5) | ((second / 2) as u16);
    (time, date)
}

fn le16(out: &mut Vec<u8>, v: u16) {
    out.extend_from_slice(&v.to_le_bytes());
}

fn le32(out: &mut Vec<u8>, v: u32) {
    out.extend_from_slice(&v.to_le_bytes());
}

/// Write `entries` (a file on disk, and its name in the archive) as a deflated zip at `dest`.
pub fn write_zip(dest: &Path, entries: &[(PathBuf, String)]) -> std::io::Result<()> {
    let mut out: Vec<u8> = Vec::new();
    let mut central: Vec<u8> = Vec::new();
    for (src, name) in entries {
        let data = std::fs::read(src)?;
        let mode = crate::sys::mode(src);
        let mut crc = Crc::new();
        crc.update(&data);
        let mut enc = DeflateEncoder::new(Vec::new(), Compression::new(6));
        enc.write_all(&data)?;
        let packed = enc.finish()?;
        let (time, date) = dos_time(src);
        let flags: u16 = if name.is_ascii() { 0 } else { 0x800 };
        let offset = out.len() as u32;
        let nb = name.as_bytes();
        le32(&mut out, 0x0403_4b50);
        le16(&mut out, 20);
        le16(&mut out, flags);
        le16(&mut out, 8);
        le16(&mut out, time);
        le16(&mut out, date);
        le32(&mut out, crc.sum());
        le32(&mut out, packed.len() as u32);
        le32(&mut out, data.len() as u32);
        le16(&mut out, nb.len() as u16);
        le16(&mut out, 0);
        out.extend_from_slice(nb);
        out.extend_from_slice(&packed);
        le32(&mut central, 0x0201_4b50);
        le16(&mut central, (3 << 8) | 20);
        le16(&mut central, 20);
        le16(&mut central, flags);
        le16(&mut central, 8);
        le16(&mut central, time);
        le16(&mut central, date);
        le32(&mut central, crc.sum());
        le32(&mut central, packed.len() as u32);
        le32(&mut central, data.len() as u32);
        le16(&mut central, nb.len() as u16);
        le16(&mut central, 0);
        le16(&mut central, 0);
        le16(&mut central, 0);
        le16(&mut central, 0);
        le32(&mut central, (mode & 0xFFFF) << 16);
        le32(&mut central, offset);
        central.extend_from_slice(nb);
    }
    let cd_offset = out.len() as u32;
    let cd_size = central.len() as u32;
    out.extend_from_slice(&central);
    le32(&mut out, 0x0605_4b50);
    le16(&mut out, 0);
    le16(&mut out, 0);
    le16(&mut out, entries.len() as u16);
    le16(&mut out, entries.len() as u16);
    le32(&mut out, cd_size);
    le32(&mut out, cd_offset);
    le16(&mut out, 0);
    std::fs::write(dest, out)
}

fn u16_at(b: &[u8], i: usize) -> Option<u16> {
    Some(u16::from_le_bytes([*b.get(i)?, *b.get(i + 1)?]))
}

fn u32_at(b: &[u8], i: usize) -> Option<u32> {
    Some(u32::from_le_bytes([*b.get(i)?, *b.get(i + 1)?, *b.get(i + 2)?, *b.get(i + 3)?]))
}

/// An entry as the central directory lists it, with where its bytes start.
struct Listed {
    name: String,
    method: u16,
    crc: u32,
    packed: usize,
    size: usize,
    start: usize,
}

/// An entry's bytes as they unpack, refused the moment they would pass the size it declares.
struct Bounded {
    data: Vec<u8>,
    limit: usize,
    passed: bool,
}

impl Write for Bounded {
    fn write(&mut self, buf: &[u8]) -> std::io::Result<usize> {
        if buf.len() > self.limit - self.data.len() {
            self.passed = true;
            return Err(std::io::Error::other("past the size the entry declares"));
        }
        self.data.extend_from_slice(buf);
        Ok(buf.len())
    }

    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}

/// The entries of a zip: (name, bytes), in the order its central directory lists them. Once rb is interrupted under
/// `sys::catch_endings` it stops before the next entry, with an error of the kind Interrupted.
pub fn read_zip(src: &Path) -> Result<Vec<(String, Vec<u8>)>, ZipError> {
    read_zip_within(src, MAX_BUNDLE, &crate::sys::interrupted)
}

/// Why reading or unpacking a zip stopped before its end: `stop` said to.
fn stopped() -> std::io::Error {
    std::io::Error::from(std::io::ErrorKind::Interrupted)
}

/// `read_zip`, refusing a zip over `max` bytes before reading it, one whose entries declare more than `max` together
/// before unpacking any, and an entry the moment it unpacks past the size it declares; asking `stop` before each entry.
fn read_zip_within(src: &Path, max: u64, stop: &dyn Fn() -> bool) -> Result<Vec<(String, Vec<u8>)>, ZipError> {
    let file = std::fs::File::open(src)?;
    let len = file.metadata()?.len();
    if len > max {
        return Err(ZipError::TooBig);
    }
    let mut b = Vec::with_capacity(len as usize);
    // a file that grows while it is read is held to the same cap
    file.take(max + 1).read_to_end(&mut b)?;
    if b.len() as u64 > max {
        return Err(ZipError::TooBig);
    }
    let bad = |m: &str| ZipError::Bad(m.to_string());
    if b.len() < 22 {
        return Err(bad("File is not a zip file"));
    }
    let lo = b.len().saturating_sub(22 + 65535);
    let eocd = (lo..=b.len() - 22).rev().find(|&i| u32_at(&b, i) == Some(0x0605_4b50)).ok_or_else(|| bad("File is not a zip file"))?;
    let count = u16_at(&b, eocd + 10).ok_or_else(|| bad("truncated"))? as usize;
    let mut at = u32_at(&b, eocd + 16).ok_or_else(|| bad("truncated"))? as usize;
    let mut listed: Vec<Listed> = Vec::new();
    let mut total: u64 = 0;
    for _ in 0..count {
        if u32_at(&b, at) != Some(0x0201_4b50) {
            return Err(bad("Bad magic number for central directory"));
        }
        let method = u16_at(&b, at + 10).ok_or_else(|| bad("truncated"))?;
        let crc = u32_at(&b, at + 16).ok_or_else(|| bad("truncated"))?;
        let packed = u32_at(&b, at + 20).ok_or_else(|| bad("truncated"))? as usize;
        let size = u32_at(&b, at + 24).ok_or_else(|| bad("truncated"))?;
        let nlen = u16_at(&b, at + 28).ok_or_else(|| bad("truncated"))? as usize;
        let xlen = u16_at(&b, at + 30).ok_or_else(|| bad("truncated"))? as usize;
        let clen = u16_at(&b, at + 32).ok_or_else(|| bad("truncated"))? as usize;
        let local = u32_at(&b, at + 42).ok_or_else(|| bad("truncated"))? as usize;
        let name_bytes = b.get(at + 46..at + 46 + nlen).ok_or_else(|| bad("truncated"))?;
        let name = String::from_utf8_lossy(name_bytes).into_owned();
        at += 46 + nlen + xlen + clen;
        // ZIP64 keeps the sizes in an extra field this reader does not take (rb export never writes one)
        if size == u32::MAX || packed == u32::MAX as usize {
            return Err(bad("File is a ZIP64 archive"));
        }
        total += u64::from(size);
        if total > max {
            return Err(ZipError::Unpacks);
        }
        if u32_at(&b, local) != Some(0x0403_4b50) {
            return Err(bad("Bad magic number for file header"));
        }
        let lnlen = u16_at(&b, local + 26).ok_or_else(|| bad("truncated"))? as usize;
        let lxlen = u16_at(&b, local + 28).ok_or_else(|| bad("truncated"))? as usize;
        let start = local + 30 + lnlen + lxlen;
        listed.push(Listed { name, method, crc, packed, size: size as usize, start });
    }
    let mut out = Vec::new();
    for e in listed {
        if stop() {
            return Err(ZipError::Io(stopped()));
        }
        let raw = b.get(e.start..e.start + e.packed).ok_or_else(|| bad("truncated"))?;
        // the sizes declared are within the cap, so each entry's room is taken once, up front
        let room = Bounded { data: Vec::with_capacity(e.size), limit: e.size, passed: false };
        let data = match e.method {
            0 => {
                let mut w = room;
                w.write_all(raw).map_err(|_| ZipError::Overflows(e.size as u64))?;
                w.data
            }
            8 => {
                let mut dec = DeflateDecoder::new(room);
                let inflated = dec.write_all(raw).and_then(|()| dec.try_finish());
                if inflated.is_err() {
                    return Err(if dec.get_ref().passed { ZipError::Overflows(e.size as u64) } else { bad("Error -3 while decompressing data") });
                }
                dec.finish().map_err(|_| bad("Error -3 while decompressing data"))?.data
            }
            m => return Err(ZipError::Bad(format!("compression type {m}"))),
        };
        let mut c = Crc::new();
        c.update(&data);
        if c.sum() != e.crc || data.len() != e.size {
            return Err(ZipError::Bad(format!("Bad CRC-32 for file {}", crate::pyjson::repr_str(&e.name))));
        }
        out.push((e.name, data));
    }
    Ok(out)
}

/// Every entry of a zip (as `read_zip` gives them) written under `dest`, its name cleaned of absolute paths and
/// `.`/`..` parts (and on Windows of what a file name cannot hold there), so no entry lands outside `dest`. What fails
/// here is the writing; once rb is interrupted under `sys::catch_endings` it stops before the next entry, as `read_zip`
/// does.
pub fn unpack(entries: &[(String, Vec<u8>)], dest: &Path) -> std::io::Result<()> {
    unpack_until(entries, dest, &crate::sys::interrupted)
}

fn unpack_until(entries: &[(String, Vec<u8>)], dest: &Path, stop: &dyn Fn() -> bool) -> std::io::Result<()> {
    for (name, data) in entries {
        if stop() {
            return Err(stopped());
        }
        let parts = crate::sys::zip_parts(name);
        let target = parts.iter().fold(dest.to_path_buf(), |acc, p| acc.join(p));
        if name.ends_with('/') {
            std::fs::create_dir_all(&target)?;
            continue;
        }
        if let Some(parent) = target.parent() {
            std::fs::create_dir_all(parent)?;
        }
        std::fs::write(&target, data)?;
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A folder of the test's own, gone with it.
    struct Dir(PathBuf);

    impl Dir {
        fn new() -> Dir {
            let p = std::env::temp_dir().join(format!("rb-zip-test-{}-{:08x}", std::process::id(), crate::rng::os_randbits(32)));
            std::fs::create_dir_all(&p).unwrap();
            Dir(p)
        }

        fn file(&self, name: &str, bytes: &[u8]) -> PathBuf {
            let p = self.0.join(name);
            std::fs::write(&p, bytes).unwrap();
            p
        }
    }

    impl Drop for Dir {
        fn drop(&mut self) {
            let _ = std::fs::remove_dir_all(&self.0);
        }
    }

    fn crc_of(data: &[u8]) -> u32 {
        let mut c = Crc::new();
        c.update(data);
        c.sum()
    }

    fn deflated(data: &[u8]) -> Vec<u8> {
        let mut enc = DeflateEncoder::new(Vec::new(), Compression::new(6));
        enc.write_all(data).unwrap();
        enc.finish().unwrap()
    }

    /// A zip laid out as `write_zip` lays one out, each entry as (name, method, the size it declares, the bytes it
    /// stores, its CRC), whatever they say of each other.
    fn zip_of(entries: &[(&str, u16, u32, Vec<u8>, u32)]) -> Vec<u8> {
        let (mut out, mut central) = (Vec::new(), Vec::new());
        for (name, method, size, stored, crc) in entries {
            let nb = name.as_bytes();
            // what both headers say: version needed, flags, method, time, date, CRC, sizes, name and extra lengths
            let shared = |b: &mut Vec<u8>| {
                for v in [20, 0, *method, 0, 0] {
                    le16(b, v);
                }
                for v in [*crc, stored.len() as u32, *size] {
                    le32(b, v);
                }
                le16(b, nb.len() as u16);
                le16(b, 0);
            };
            le32(&mut central, 0x0201_4b50);
            le16(&mut central, 20);
            shared(&mut central);
            for v in [0, 0, 0] {
                le16(&mut central, v);
            }
            le32(&mut central, 0);
            le32(&mut central, out.len() as u32);
            central.extend_from_slice(nb);
            le32(&mut out, 0x0403_4b50);
            shared(&mut out);
            out.extend_from_slice(nb);
            out.extend_from_slice(stored);
        }
        let (cd_offset, cd_size) = (out.len() as u32, central.len() as u32);
        out.extend_from_slice(&central);
        le32(&mut out, 0x0605_4b50);
        for v in [0, 0, entries.len() as u16, entries.len() as u16] {
            le16(&mut out, v);
        }
        le32(&mut out, cd_size);
        le32(&mut out, cd_offset);
        le16(&mut out, 0);
        out
    }

    /// What rb export writes reads back whole, deflated or stored; what is no zip still says so.
    #[test]
    fn a_bundle_reads_back_as_it_was_written() {
        let d = Dir::new();
        let page = "<!doctype html>\n".repeat(4000);
        let entries = [
            (d.file("project.yaml", b"rb: 1\nkind: project\n"), "b/record/.rb/project.yaml".to_string()),
            (d.file("page.html", page.as_bytes()), "b/page.html".to_string()),
            (d.file("empty", b""), "b/empty".to_string()),
        ];
        let zip = d.0.join("b.zip");
        write_zip(&zip, &entries).unwrap();
        let got = read_zip(&zip).unwrap();
        assert_eq!(got.iter().map(|(n, _)| n.as_str()).collect::<Vec<_>>(), ["b/record/.rb/project.yaml", "b/page.html", "b/empty"]);
        assert_eq!((got[0].1.as_slice(), got[1].1.as_slice(), got[2].1.len()), (&b"rb: 1\nkind: project\n"[..], page.as_bytes(), 0));
        let stored = d.file("stored.zip", &zip_of(&[("s.txt", 0, 5, b"hello".to_vec(), crc_of(b"hello"))]));
        assert_eq!(read_zip(&stored).unwrap(), vec![("s.txt".to_string(), b"hello".to_vec())]);
        assert!(matches!(read_zip(&d.file("bad.zip", b"not a zip\n")), Err(ZipError::Bad(_))));
    }

    /// A zip over the cap is refused by its size, whatever it holds (this one is no zip at all), and one at the cap is
    /// read like any other. That the refusal comes before a byte is read, the clone tests hold by rb's peak memory.
    #[test]
    fn a_zip_over_the_cap_is_refused_by_its_size() {
        let d = Dir::new();
        let big = d.file("big.zip", &[7u8; 4096]);
        assert!(matches!(read_zip_within(&big, 4095, &|| false), Err(ZipError::TooBig)));
        assert!(matches!(read_zip_within(&big, 4096, &|| false), Err(ZipError::Bad(_))));
    }

    /// Entries that declare more than the cap together are refused before any is inflated or given room: these hold no
    /// deflate stream, which inflating one would have said instead.
    #[test]
    fn declared_sizes_past_the_cap_are_refused_before_anything_inflates() {
        let d = Dir::new();
        let garbage = vec![0xffu8; 64];
        let one = d.file("one.zip", &zip_of(&[("b/zeros.bin", 8, u32::MAX - 1, garbage.clone(), 0)]));
        assert!(matches!(read_zip(&one), Err(ZipError::Unpacks)));
        let zip64 = d.file("zip64.zip", &zip_of(&[("b/zeros.bin", 8, u32::MAX, garbage.clone(), 0)]));
        assert!(matches!(read_zip(&zip64), Err(ZipError::Bad(_))));
        let half = (MAX_BUNDLE / 2 + 1) as u32;
        let two = d.file("two.zip", &zip_of(&[("b/a", 8, half, garbage.clone(), 0), ("b/b", 8, half, garbage.clone(), 0)]));
        assert!(matches!(read_zip(&two), Err(ZipError::Unpacks)));
        let under = d.file("under.zip", &zip_of(&[("b/a", 8, 1000, garbage, 0)]));
        assert!(matches!(read_zip(&under), Err(ZipError::Bad(_))));
    }

    /// An entry that unpacks past the size it declares is stopped there, deflated or stored, and the room it unpacks
    /// into never holds more than that size; one that unpacks to less is damaged, as before.
    #[test]
    fn an_entry_that_unpacks_past_its_declared_size_is_stopped_there() {
        let d = Dir::new();
        let zeros = vec![0u8; 8 << 20];
        let lying = d.file("lying.zip", &zip_of(&[("b/x.bin", 8, 1000, deflated(&zeros), crc_of(&zeros))]));
        assert!(matches!(read_zip(&lying), Err(ZipError::Overflows(1000))));
        let stored = d.file("stored.zip", &zip_of(&[("b/x.bin", 0, 1000, zeros[..2000].to_vec(), crc_of(&zeros[..2000]))]));
        assert!(matches!(read_zip(&stored), Err(ZipError::Overflows(1000))));
        let mut room = Bounded { data: Vec::new(), limit: 1000, passed: false };
        room.write_all(&[1u8; 600]).unwrap();
        assert!(room.write_all(&[1u8; 600]).is_err() && room.passed && room.data.len() == 600);
        let short = d.file("short.zip", &zip_of(&[("b/x.bin", 8, 3000, deflated(&zeros[..2000]), crc_of(&zeros[..2000]))]));
        assert!(matches!(read_zip(&short), Err(ZipError::Bad(_))));
    }

    /// Reading a zip and unpacking it stop before the next entry once they are told to (rb was interrupted), so what
    /// they made is taken away within one entry.
    #[test]
    fn reading_and_unpacking_stop_within_an_entry_once_told() {
        let d = Dir::new();
        let entries: Vec<(PathBuf, String)> = ["a", "b", "c"].iter().map(|n| (d.file(n, n.repeat(1000).as_bytes()), format!("b/{n}.txt"))).collect();
        let zip = d.0.join("b.zip");
        write_zip(&zip, &entries).unwrap();
        // told to stop once the first entry is done
        let asked = std::cell::Cell::new(0);
        let after_one = || {
            asked.set(asked.get() + 1);
            asked.get() > 1
        };
        assert!(matches!(read_zip_within(&zip, MAX_BUNDLE, &after_one), Err(ZipError::Io(e)) if e.kind() == std::io::ErrorKind::Interrupted));
        assert_eq!(asked.get(), 2);
        let read = read_zip(&zip).unwrap();
        let into = d.0.join("unpacked");
        asked.set(0);
        assert_eq!(unpack_until(&read, &into, &after_one).unwrap_err().kind(), std::io::ErrorKind::Interrupted);
        assert_eq!(std::fs::read_dir(into.join("b")).unwrap().count(), 1);
    }
}
