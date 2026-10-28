//! Who a document is built for (SPRINT-OCT24.md section 1.5): Live, the user's own screen (rb open), or Owner, a page
//! anyone may be sent (rb page, rb export, rb sync, the hosted page); and what each shows, as data (`TABLE`): SHELL-SPEC
//! 15.4's rows and the build plan's (BP 1b), which every front end reads.

use super::Surface;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Tier {
    Live,
    Owner,
}

impl Tier {
    /// The tier a surface draws.
    pub fn of(surface: Surface) -> Tier {
        match surface {
            Surface::Open => Tier::Live,
            Surface::Page | Surface::Export => Tier::Owner,
        }
    }

    /// As the document's `tier` names it.
    pub fn name(self) -> &'static str {
        match self {
            Tier::Live => "live",
            Tier::Owner => "owner",
        }
    }
}

/// One row of the tier table: a kind of thing a document may hold.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Row {
    /// questions, criteria, findings, readings, standing
    Readings,
    /// recorded interpretations, notes, whys (text someone wrote)
    AuthorText,
    /// the user's words given with a write (`--words`)
    YourWords,
    /// a command as given, recipes, Reproduce
    Commands,
    /// cited sources (path:line, quote)
    CitedSources,
    /// the repository's commit and tree state on a receipt
    Repository,
    /// python and rb versions on a receipt
    Versions,
    /// result files' paths and hashes
    ResultFiles,
    /// who made a write (actor, how rb knew, through what; an agent's name, version, provider and model)
    Writer,
    /// a chat session's id
    Session,
    /// a branch, which often carries an agent's name
    Branch,
    /// a receipt's platform, the machine's OS string
    Platform,
    /// who the user is recorded as
    YouLine,
    /// a resource's download address
    DownloadAddress,
    /// hosts a run says it ran on (gpu and the like)
    Hosts,
    /// the place shown, its label, the places and versions listed, the scope bar
    Places,
    /// what waits elsewhere, in another place of the clone
    Elsewhere,
    /// a run or a finding's runs recorded in another place, said by place
    RecordedIn,
    /// where counted runs did not all run the same code: by place, or by commit alone
    OtherCode,
    /// what waits for the user's confirmation, listed apart
    Pending,
    /// an experiment's design standing
    DesignStanding,
    /// where rb looks and the changed files nothing in the record cites
    ScopeLines,
    /// what rb found in the repository that nobody recorded
    Found,
    /// a command said away from the place rb open started on, with `cd <path> &&`
    CdCommands,
    /// rb open's fingerprint of what it shows
    Digest,
}

/// What a tier shows of a row.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Shown {
    Yes,
    No,
    /// by commit alone, with no place named
    ByCommit,
    /// only where rb export --url names the address it is served from
    WithUrl,
}

/// The table: each row, what it is in words, and what Live and Owner show of it.
pub const TABLE: &[(Row, &str, Shown, Shown)] = &[
    (Row::Readings, "questions, criteria, findings, readings, standing", Shown::Yes, Shown::Yes),
    (Row::AuthorText, "recorded interpretations, notes, whys", Shown::Yes, Shown::Yes),
    (Row::YourWords, "the user's words (--words)", Shown::Yes, Shown::No),
    (Row::Commands, "command (as given), recipes, Reproduce", Shown::Yes, Shown::Yes),
    (Row::CitedSources, "cited sources (path:line, quote)", Shown::Yes, Shown::Yes),
    (Row::Repository, "repository commit, tree state", Shown::Yes, Shown::Yes),
    (Row::Versions, "python and rb versions", Shown::Yes, Shown::Yes),
    (Row::ResultFiles, "result file paths and hashes", Shown::Yes, Shown::Yes),
    (Row::Writer, "writer (actor, actor_via, via; an agent's name, version, provider and model)", Shown::No, Shown::No),
    (Row::Session, "session ids", Shown::No, Shown::No),
    (Row::Branch, "branch", Shown::No, Shown::No),
    (Row::Platform, "receipt platform (the OS string)", Shown::Yes, Shown::No),
    (Row::YouLine, "the \"you\" line", Shown::No, Shown::No),
    (Row::DownloadAddress, "resource download address", Shown::Yes, Shown::WithUrl),
    (Row::Hosts, "hosts (gpu and the like)", Shown::Yes, Shown::Yes),
    (Row::Places, "place label, places, versions, scope bar", Shown::Yes, Shown::No),
    (Row::Elsewhere, "Elsewhere", Shown::Yes, Shown::No),
    (Row::RecordedIn, "recorded in, observed in another place", Shown::Yes, Shown::No),
    (Row::OtherCode, "other-code notes", Shown::Yes, Shown::ByCommit),
    (Row::Pending, "waiting for confirmation", Shown::Yes, Shown::Yes),
    (Row::DesignStanding, "design standing", Shown::Yes, Shown::Yes),
    (Row::ScopeLines, "the scope and uncited lines", Shown::Yes, Shown::Yes),
    (Row::Found, "Found in the repository", Shown::Yes, Shown::Yes),
    (Row::CdCommands, "commands away from the start place, with cd <path> &&", Shown::Yes, Shown::No),
    (Row::Digest, "digest", Shown::Yes, Shown::No),
];

impl Tier {
    /// What this tier shows of `row`, as the table says.
    pub fn shows(self, row: Row) -> Shown {
        let (_, _, live, owner) = TABLE.iter().find(|(r, ..)| *r == row).copied().unwrap_or((row, "", Shown::No, Shown::No));
        match self {
            Tier::Live => live,
            Tier::Owner => owner,
        }
    }

    /// Whether this tier shows any of `row`.
    pub fn has(self, row: Row) -> bool {
        self.shows(row) != Shown::No
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_row_is_in_the_table_once_and_no_tier_names_a_writer() {
        let rows = [
            Row::Readings, Row::AuthorText, Row::YourWords, Row::Commands, Row::CitedSources, Row::Repository, Row::Versions, Row::ResultFiles,
            Row::Writer, Row::Session, Row::Branch, Row::Platform, Row::YouLine, Row::DownloadAddress, Row::Hosts, Row::Places, Row::Elsewhere,
            Row::RecordedIn, Row::OtherCode, Row::Pending, Row::DesignStanding, Row::ScopeLines, Row::Found, Row::CdCommands, Row::Digest,
        ];
        for r in rows {
            assert_eq!(TABLE.iter().filter(|(x, ..)| *x == r).count(), 1, "{r:?}");
        }
        assert_eq!(TABLE.len(), rows.len());
        for t in [Tier::Live, Tier::Owner] {
            for r in [Row::Writer, Row::Session, Row::Branch, Row::YouLine] {
                assert_eq!(t.shows(r), Shown::No);
            }
        }
        // the Owner tier shows nothing the Live tier does not
        for (r, _, live, owner) in TABLE {
            assert!(*owner == Shown::No || *live != Shown::No, "{r:?}");
        }
    }
}
