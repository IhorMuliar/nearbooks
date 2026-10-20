//! OutLayer entrypoint (wasm32-wasip2): stdin = `{"n":"<nonce>","d":"YYYY-MM-DD","b":"<height>","b28":"<height>"}`, stdout = the snapshot JSON (see lib.rs).
#[cfg(target_os = "wasi")]
struct Wasi;

#[cfg(target_os = "wasi")]
impl nearbooks_worker::Transport for Wasi {
    fn post(&mut self, url: &str, body: &[u8]) -> Result<(u16, Vec<u8>), String> {
        let resp = wasi_http_client::Client::new()
            .post(url)
            .header("content-type", "application/json")
            .connect_timeout(std::time::Duration::from_secs(5))
            .body(body)
            .send()
            .map_err(|e| e.to_string())?;
        let status = resp.status();
        let bytes = resp.body().map_err(|e| e.to_string())?;
        Ok((status, bytes))
    }
}

#[cfg(target_os = "wasi")]
fn main() {
    use std::io::{self, Read, Write};
    let start = std::time::Instant::now();
    let mut input = String::new();
    let _ = io::stdin().read_to_string(&mut input);
    let ctx = nearbooks_worker::Ctx {
        input,
        request_id: std::env::var("NEAR_REQUEST_ID").unwrap_or_default(),
        elapsed_ms: Box::new(move || start.elapsed().as_millis() as u64),
        sleep: Box::new(|ms| std::thread::sleep(std::time::Duration::from_millis(ms))),
    };
    match nearbooks_worker::run(&mut Wasi, &ctx) {
        Ok(out) => {
            print!("{}", out);
            let _ = io::stdout().flush();
        }
        Err(e) => {
            // Non-zero exit: OutLayer reports the run as failed and the contract stores nothing.
            eprintln!("nearbooks-worker: {}", e);
            std::process::exit(1);
        }
    }
}

#[cfg(not(target_os = "wasi"))]
fn main() {
    eprintln!("build with ./build.sh (target wasm32-wasip2); the native build only exists for `cargo test`");
    std::process::exit(2);
}
