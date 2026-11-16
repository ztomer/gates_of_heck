//! The shape table of `goh dead-port`. The fixtures build their lines from `DEAD`, so this file,
//! itself a test the scanner reads, carries no literal ephemeral port.

use super::*;

/// The incident's port (ztools `call_osaurus`), and the bottom of Linux's range.
const DEAD: u32 = 59_999;
const LINUX_FIRST: u32 = 32_768;
const LAST: u32 = 65_535;

fn hits(rel: &str, text: &str) -> Vec<usize> {
    scan(rel, text)
        .unwrap_or_default()
        .into_iter()
        .map(|(n, _)| n)
        .collect()
}

#[test]
fn a_literal_ephemeral_port_on_a_loopback_host_is_found_in_every_spelling() {
    for bad in [
        format!("    let r = call_osaurus(\"http://127.0.0.1:{DEAD}\", &req);"),
        format!("    url = \"http://localhost:{DEAD}/v1\""),
        format!("    addr = \"[::1]:{DEAD}\""),
        format!("    bind = \"0.0.0.0:{DEAD}\""),
        format!("    s.connect((\"127.0.0.1\", {DEAD}))"),
        format!("    let a = (\"localhost\", {DEAD});"),
        format!("    Endpoint(host: \"127.0.0.1\", port: {DEAD})"),
        format!("    let a = SocketAddr::new(IpAddr::V4(Ipv4Addr::LOCALHOST), {DEAD});"),
        format!("    let a = SocketAddr::from(([127, 0, 0, 1], {DEAD}));"),
        format!("curl -s http://127.0.0.1:{LINUX_FIRST}/health"),
        format!("    const u = `http://localhost:{LAST}`;"),
    ] {
        let why = line_finding(&bad).unwrap_or_default();
        assert!(why.contains("ephemeral range"), "{bad}");
    }
}

#[test]
fn a_low_port_a_socket_read_port_and_other_hosts_pass() {
    for ok in [
        "    url = \"http://127.0.0.1:8080\"".to_string(),
        format!("    url = \"http://127.0.0.1:{}\"", LINUX_FIRST - 1),
        "    url = f\"http://127.0.0.1:{port}\"".to_string(),
        "    let u = format!(\"http://127.0.0.1:{}\", dead.port());".to_string(),
        format!("    url = \"http://10.0.0.5:{DEAD}\""),
        format!("    url = \"http://example.com:{DEAD}\""),
        format!("    # url = \"http://127.0.0.1:{DEAD}\""),
        format!("    // let u = \"http://127.0.0.1:{DEAD}\";"),
        "    v = \"127.0.0.1:99999\"".to_string(),
        format!("    let t = 127.0.0.1; let n = {DEAD};"),
    ] {
        assert_eq!(line_finding(&ok), None, "{ok}");
    }
}

#[test]
fn the_escape_needs_a_reason() {
    let reasoned =
        format!("    url = \"http://127.0.0.1:{DEAD}\"  # dead-port-ok: the fixture's live server");
    assert_eq!(line_finding(&reasoned), None);
    let bare = format!("    url = \"http://127.0.0.1:{DEAD}\"  # dead-port-ok:");
    assert!(line_finding(&bare).is_some_and(|w| w.contains("names no reason")));
}

#[test]
fn only_test_code_is_judged() {
    let site = format!("let base = \"http://127.0.0.1:{DEAD}\";\n");
    // a service's own port in non-test code is never read
    assert_eq!(scan("src/server.rs", &site), None);
    assert_eq!(scan("tools/serve.py", &site), None);
    for t in [
        "tests/test_client.py",
        "pkg/tests/client.rs",
        "test_client.py",
        "client_test.py",
        "web/src/client.test.ts",
        "web/__tests__/client.ts",
        "Tests/AppTests/ClientTests.swift",
        "ClientTests.swift",
        "tests/run.sh",
    ] {
        assert_eq!(hits(t, &site), vec![1], "{t}");
    }
    assert_eq!(scan("README.md", &site), None);
}

#[test]
fn a_rust_file_is_judged_in_its_cfg_test_regions_only() {
    let text = format!(
        "fn serve() {{\n    let base = \"http://127.0.0.1:{DEAD}\";\n}}\n\n#[cfg(test)]\nmod tests {{\n    #[test]\n    fn refused() {{\n        let r = call(\"http://127.0.0.1:{DEAD}\");\n    }}\n}}\n"
    );
    assert_eq!(hits("src/client.rs", &text), vec![9]);
    // no test region, no test path: not read at all, so not counted against the floor either
    assert_eq!(scan("src/client.rs", "fn serve() {}\n"), None);
}

#[test]
fn the_range_is_the_union_of_both_oses() {
    let at = |p: u32| line_finding(&format!("\"127.0.0.1:{p}\"")).is_some();
    assert!(!at(LINUX_FIRST - 1));
    assert!(at(LINUX_FIRST), "Linux's first ephemeral port");
    assert!(at(49_152), "macOS's first ephemeral port");
    assert!(at(65_535));
    assert!(!at(65_536), "not a port");
}
