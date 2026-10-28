// rb builds a record out of many small values; mimalloc hands them out faster than the system allocator
#[global_allocator]
static GLOBAL: mimalloc::MiMalloc = mimalloc::MiMalloc;

fn main() {
    std::process::exit(rb::cli::main_os(std::env::args_os().skip(1).collect()));
}
