//! The goal a JavaScript source is judged with, and how the house decides it.
//!
//! WHAT A GOAL IS. A parser is asked "is this source valid?" only once it has been told
//! WHAT KIND of source it is. The same text is a syntax error in one goal and an ordinary
//! statement in another: `return 1;` is a syntax error in a browser's classic script and an
//! ordinary statement in `CommonJS`; `await x;` is a syntax error in a classic script and legal at
//! the top level of a module. So a gate that does not name the goal it judges with is a gate
//! whose answer depends on which engine it happened to ask -- and the engines disagree.
//!
//! THE THREE GOALS, and who gives each one:
//!
//!   * CLASSIC SCRIPT -- what a browser's `<script src>` is. Sloppy, and it refuses a
//!     top-level `return`, a top-level `await`, `import`, `export` and `super`.
//!   * MODULE -- what a browser's `<script type="module">` is. `import`, `export` and a
//!     top-level `await` are legal; a top-level `return` is not.
//!   * COMMONJS -- what node's `.cjs` is. Sloppy, a top-level `return` is legal, `import` and
//!     `export` are not. No browser loads it.
//!
//! WHY THE HOUSE'S RULE IS THE FILE'S NAME. A browser's goal is decided by the HTML that loads
//! the file -- `<script>` or `<script type="module">` -- and the HTML is not in the repository
//! this gate reads, so a file cannot know how a page loads it. The NAME is the one thing in a
//! file that every loader agrees with, and the estate already uses it:
//!
//!   * `.js` -- a classic script. This is the goal the estate cares about, because a classic
//!     script that does not parse is a PAGE THAT RENDERS EMPTY: the browser refuses the file and
//!     says nothing, and no test sees it.
//!   * `.mjs` -- a module. `media_server`'s `fetch_youtube_cookies.mjs` is one, and it carries a
//!     top-level `await`, which a classic script may not.
//!   * `.cjs` -- `CommonJS`, which is node's and no browser's.
//!
//! WHAT A `.js` FILE THAT IS REALLY A MODULE GETS. A finding, and the finding says what to do
//! about it: a browser loads a `.js` file with `<script src>`, so a module in a `.js` file is a
//! file no classic script can load -- and a module is named `.mjs`. The house does not sniff the
//! source to decide: a file that carries `import` is a module to every tool that reads it, and a
//! gate that guessed from the text would call a file a module because a REGEX held the word
//! `import`, and would then judge it with the goal that ALLOWS a top-level `await` -- the very
//! defect this rule exists to close. The name is a fact; the text is a guess.
//!
//! THE COST, stated plainly: a repository that keeps a module in a `.js` file is told to rename
//! it. That is a real cost, and it is the price of one rule every loader agrees with.

/// The goal a source is parsed with.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Goal {
    /// The browser's `<script src>`: a classic script.
    Classic,
    /// The browser's `<script type="module">`: a module.
    Module,
    /// node's `.cjs`: `CommonJS`.
    CommonJs,
}

impl Goal {
    /// How the report names this goal.
    #[must_use]
    pub const fn label(self) -> &'static str {
        match self {
            Self::Classic => "classic script",
            Self::Module => "module",
            Self::CommonJs => "CommonJS",
        }
    }

    /// The word the engines read on the wire.
    #[must_use]
    pub const fn wire(self) -> &'static str {
        match self {
            Self::Classic => "classic",
            Self::Module => "module",
            Self::CommonJs => "commonjs",
        }
    }
}

/// The goal for one source, by the name it has.
///
/// The extension is read case-insensitively, because the scope's own rule reads it that way: a
/// file named `A.JS` is in scope, so it has a goal, and the goal is the one its name gives it.
#[must_use]
pub fn goal_for(path: &str) -> Goal {
    let name = path.rsplit('/').next().unwrap_or(path);
    let extension = name.rsplit_once('.').map_or("", |(_, ext)| ext);
    if extension.eq_ignore_ascii_case("mjs") {
        Goal::Module
    } else if extension.eq_ignore_ascii_case("cjs") {
        Goal::CommonJs
    } else {
        Goal::Classic
    }
}

#[cfg(test)]
mod tests {
    use super::{goal_for, Goal};

    #[test]
    fn a_javascript_file_is_a_classic_script() {
        // The browser's `<script src>`: the goal the estate cares about, and the
        // one a top-level `return` is a syntax error in.
        assert_eq!(goal_for("web/app.js"), Goal::Classic);
        assert_eq!(goal_for("web/app.JS"), Goal::Classic);
    }

    #[test]
    fn a_module_is_named_mjs_and_a_commonjs_is_named_cjs() {
        assert_eq!(goal_for("scripts/fetch.mjs"), Goal::Module);
        assert_eq!(goal_for("scripts/fetch.MJS"), Goal::Module);
        assert_eq!(goal_for("scripts/legacy.cjs"), Goal::CommonJs);
    }

    #[test]
    fn the_goal_does_not_depend_on_the_text() {
        // A file that carries module syntax is still judged with the goal its
        // NAME gives it: the name is a fact every loader agrees with, and the
        // text is a guess.
        assert_eq!(goal_for("web/app.js"), Goal::Classic);
        assert_eq!(goal_for("eslint.config.js"), Goal::Classic);
    }
}
