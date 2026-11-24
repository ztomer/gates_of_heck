//! The queue's SQLite store as `queue::load_db` reads it (LOOP.QUEUEARCHIVE, GOH.QUEUESCHEMA): split
//! from `tests.rs`, which had reached the file-length headroom.

/// LOOP.QUEUEARCHIVE: once the queue lives in SQLite (`loop.db` beside its path), the statuses come
/// from the store, the JSON beside it ignored; a store `sqlite3` refuses is an error, never empty.
#[test]
fn the_queue_reads_its_sqlite_store_when_one_exists() {
    let dir = std::env::temp_dir().join(format!("goh-roadmap-queue-{}", std::process::id()));
    std::fs::create_dir_all(&dir).expect("temp dir");
    let named = dir.join("queue.json");
    std::fs::write(&named, r#"{"items":[{"id":"A.1","status":"todo"}]}"#).expect("json");
    let flag = named.to_str().expect("utf-8 path");
    assert_eq!(
        crate::roadmap::queue::load(Some(flag))
            .ok()
            .and_then(|m| m.get("A.1").cloned())
            .as_deref(),
        Some("todo")
    );
    let db = dir.join(crate::roadmap::queue::DB_NAME);
    let made = std::process::Command::new("sqlite3")
        .arg(&db)
        .arg(
            "PRAGMA user_version = 1; CREATE TABLE items(id TEXT PRIMARY KEY, seq INTEGER, archived INTEGER, status TEXT, body TEXT); \
              INSERT INTO items VALUES ('A.1', 0, 0, 'done', '{}'), ('B.2', 1, 1, 'todo', '{}');",
        )
        .status();
    if !made.is_ok_and(|s| s.success()) {
        let _ = std::fs::remove_dir_all(&dir);
        assert!(
            crate::roadmap::queue::load_db(&db).is_err(),
            "no sqlite3: the store is NOT JUDGED"
        );
        return;
    }
    let got = crate::roadmap::queue::load(Some(flag)).expect("the store reads");
    assert_eq!(got.get("A.1").map(String::as_str), Some("done"));
    assert_eq!(got.len(), 2);
    let newer = std::process::Command::new("sqlite3")
        .arg(&db)
        .arg("PRAGMA user_version = 99")
        .status();
    assert!(newer.is_ok_and(|s| s.success()));
    let later =
        crate::roadmap::queue::load(Some(flag)).expect("a later schema with the layout reads");
    assert_eq!(
        later.len(),
        2,
        "GOH.QUEUESCHEMA: read by layout, not one number"
    );
    std::fs::write(&db, "not a database").expect("corrupt");
    assert!(
        crate::roadmap::queue::load(Some(flag)).is_err(),
        "a corrupt store is an error, never empty"
    );
    let _ = std::fs::remove_dir_all(&dir);
}

/// LOOP.QUEUEARCHIVE: a store with no items is an empty queue (`sqlite3 -json` prints nothing for no
/// rows), and a row whose status is not a string is an error that names the store, never a guess.
#[test]
fn an_empty_store_is_no_items_and_a_null_status_names_the_store() {
    let dir = std::env::temp_dir().join(format!("goh-roadmap-qempty-{}", std::process::id()));
    std::fs::create_dir_all(&dir).expect("temp dir");
    let db = dir.join(crate::roadmap::queue::DB_NAME);
    let made = std::process::Command::new("sqlite3")
        .arg(&db)
        .arg(
            "PRAGMA user_version = 1; CREATE TABLE items(id TEXT PRIMARY KEY, seq INTEGER, archived INTEGER, status TEXT, body TEXT);",
        )
        .status();
    if !made.is_ok_and(|s| s.success()) {
        let _ = std::fs::remove_dir_all(&dir);
        assert!(
            crate::roadmap::queue::load_db(&db).is_err(),
            "no sqlite3: the store is NOT JUDGED"
        );
        return;
    }
    let empty = crate::roadmap::queue::load_db(&db).expect("an empty store reads");
    assert!(empty.is_empty(), "{empty:?}");
    let added = std::process::Command::new("sqlite3")
        .arg(&db)
        .arg("INSERT INTO items VALUES ('A.1', 0, 0, NULL, '{}')")
        .status();
    assert!(added.is_ok_and(|s| s.success()));
    let said = crate::roadmap::queue::load_db(&db).expect_err("a NULL status is no status");
    assert!(
        said.contains("is unreadable") && said.contains(crate::roadmap::queue::DB_NAME),
        "{said}"
    );
    let _ = std::fs::remove_dir_all(&dir);
}

/// GOH.QUEUESCHEMA: the loop's versioned views (`queue_v1_items`, `queue_v1_keys`; LOOP.QUEUENORMAL)
/// are read whenever both exist, whatever the tables under them and the `user_version`; without them
/// today's `items` table is read; and a store without what is read is no queue naming the missing
/// table or column. No list of schema numbers.
#[test]
fn the_store_is_read_by_its_layout_not_one_schema_number() {
    const VIEWS: &str = "CREATE TABLE i1 AS SELECT id, seq, archived, body FROM items; \
        CREATE VIEW queue_v1_items AS SELECT id, seq, archived, body FROM i1; \
        CREATE VIEW queue_v1_keys AS SELECT 'rules' AS key, 0 AS seq, '{}' AS value;";
    let dir = std::env::temp_dir().join(format!("goh-roadmap-qlayout-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).expect("temp dir");
    let base = r#"CREATE TABLE items(id TEXT PRIMARY KEY, seq INTEGER, archived INTEGER, status TEXT, body TEXT);
        INSERT INTO items VALUES ('A.1', 0, 0, 'done', '{"id":"A.1","status":"done"}'),
                                 ('Z.9', 0, 1, 'done', '{"id":"Z.9","status":"done"}');"#;
    let drop_body = "ALTER TABLE items DROP COLUMN body; ALTER TABLE items DROP COLUMN status;";
    let cases: [(&str, String, Result<usize, &str>); 9] = [
        (
            "2",
            "CREATE TABLE state(k TEXT); ALTER TABLE items ADD COLUMN package INTEGER;".into(),
            Ok(2),
        ),
        ("3", format!("{VIEWS} {drop_body}"), Ok(2)),
        ("0", format!("{VIEWS} {drop_body}"), Ok(2)),
        ("0", String::new(), Err("schema 0")),
        (
            "1",
            "ALTER TABLE items DROP COLUMN status;".into(),
            Err("items.status"),
        ),
        (
            "1",
            "ALTER TABLE items RENAME COLUMN seq TO n;".into(),
            Err("items.seq"),
        ),
        ("1", "DROP TABLE items;".into(), Err("no items table")),
        (
            "3",
            "CREATE VIEW queue_v1_keys AS SELECT 1 AS key; ALTER TABLE items DROP COLUMN status;"
                .into(),
            Err("items.status"),
        ),
        (
            "3",
            "CREATE VIEW queue_v1_keys AS SELECT 1 AS key; \
             CREATE VIEW queue_v1_items AS SELECT id, seq, archived FROM items;"
                .into(),
            Err("queue_v1_items.body"),
        ),
    ];
    for (n, (version, change, want)) in cases.into_iter().enumerate() {
        let db = dir.join(format!("{n}-{}", crate::roadmap::queue::DB_NAME));
        let made = std::process::Command::new("sqlite3")
            .arg(&db)
            .arg(format!("PRAGMA user_version = {version}; {base} {change}"))
            .status();
        if !made.is_ok_and(|s| s.success()) {
            let _ = std::fs::remove_dir_all(&dir);
            assert!(
                crate::roadmap::queue::load_db(&db).is_err(),
                "no sqlite3: NOT JUDGED"
            );
            return;
        }
        match (crate::roadmap::queue::load_db(&db), want) {
            (Ok(got), Ok(len)) => {
                assert_eq!(got.len(), len, "case {n}: {got:?}");
                assert_eq!(got.get("A.1").map(String::as_str), Some("done"), "case {n}");
            }
            (Err(said), Err(name)) => assert!(
                said.contains(name) && said.contains(crate::roadmap::queue::DB_NAME),
                "case {n}: {said}"
            ),
            (got, want) => panic!("case {n}: got {got:?}, want {want:?}"),
        }
    }
    let _ = std::fs::remove_dir_all(&dir);
}
