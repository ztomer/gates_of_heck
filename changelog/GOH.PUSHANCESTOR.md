* **A release tag on an ancestor of a pushed branch is gated with it:** `tag_version` wants an unpushed release tag in
  the same push as its branch, and the push gate then rebuilt the tag's old commit with today's gates and refused it
  (ZSUS: a file that had since moved into goh read as a vendored copy), so no push shape passed. A ref whose commit is
  an ancestor of another ref's commit in the same push gains the remote nothing the gated tip does not carry; it is
  skipped with a line saying so, and a red tip is still refused.
