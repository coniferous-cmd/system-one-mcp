#[tokio::main]
async fn main() {
    let c = reqwest::Client::builder().timeout(std::time::Duration::from_secs(60)).build().unwrap();
    let r = c.post("https://openrouter.ai/api/alpha/decisions")
        .header("Content-Type","application/json")
        .header("Idempotency-Key","probe-rust-1")
        .json(&serde_json::json!({"model":"typesafe/jev-1.13","state":"x","questions":{"a":{"type":"noul","instructions":"?"}}}))
        .send().await;
    match r {
        Ok(resp) => { let s = resp.status(); println!("status {s}"); }
        Err(e) => {
            println!("err {e}");
            let mut s = std::error::Error::source(&e);
            while let Some(x) = s { println!("  caused by: {x}"); s = std::error::Error::source(x); }
        }
    }
}
