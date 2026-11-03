import json
import re
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from dataclasses import dataclass, field

from locusts.pet import Activity, Pet

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    fullness REAL NOT NULL,
    energy REAL NOT NULL,
    money REAL NOT NULL,
    activity TEXT NOT NULL,
    building TEXT,
    career TEXT,
    path TEXT,
    xp TEXT NOT NULL,
    upgrades TEXT NOT NULL,
    rest_multiplier REAL NOT NULL,
    sleep_hours REAL NOT NULL,
    day_started_at REAL NOT NULL,
    penalty_start REAL,
    penalty_end REAL,
    coffee_until REAL,
    updated_at REAL NOT NULL,
    invested REAL NOT NULL DEFAULT 0,
    experiments TEXT NOT NULL DEFAULT '[]',
    work_started_at REAL,
    billed_day REAL,
    internship TEXT NOT NULL DEFAULT 'null',
    best_internship_pay REAL NOT NULL DEFAULT 0,
    bonuses TEXT NOT NULL DEFAULT '[]',
    train_day REAL,
    train_streak INTEGER NOT NULL DEFAULT 0,
    trip TEXT NOT NULL DEFAULT 'null',
    items TEXT NOT NULL DEFAULT '[]',
    subscriptions TEXT NOT NULL DEFAULT '{}',
    swipes INTEGER NOT NULL DEFAULT 0,
    fitness REAL NOT NULL DEFAULT 0,
    house TEXT NOT NULL DEFAULT 'harnwell',
    gpa REAL NOT NULL DEFAULT 3.5,
    work_hours REAL NOT NULL DEFAULT 0,
    next_event_at REAL,
    events_seen INTEGER NOT NULL DEFAULT 0,
    event TEXT NOT NULL DEFAULT 'null',
    semester INTEGER,
    doordashes INTEGER NOT NULL DEFAULT 0,
    study_week REAL,
    study_days INTEGER NOT NULL DEFAULT 0,
    study_day REAL
);
CREATE TABLE IF NOT EXISTS fairs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    opens_at REAL NOT NULL UNIQUE,
    closes_at REAL NOT NULL,
    results_at REAL NOT NULL,
    roles TEXT NOT NULL,
    resolved INTEGER NOT NULL DEFAULT 0,
    game TEXT NOT NULL DEFAULT 'locust-walk'
);
CREATE TABLE IF NOT EXISTS fair_entries (
    fair_id INTEGER NOT NULL REFERENCES fairs(id),
    pet_id INTEGER NOT NULL REFERENCES pets(id),
    roles TEXT NOT NULL DEFAULT '[]',
    referral TEXT,
    locked_at REAL,
    late_changes INTEGER NOT NULL DEFAULT 0,
    won TEXT,
    backing INTEGER,
    short_on INTEGER,
    short_stake REAL NOT NULL DEFAULT 0,
    short_payout REAL,
    PRIMARY KEY (fair_id, pet_id)
);
CREATE TABLE IF NOT EXISTS fair_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fair_id INTEGER NOT NULL REFERENCES fairs(id),
    pet_id INTEGER NOT NULL REFERENCES pets(id),
    seed INTEGER NOT NULL,
    seconds INTEGER NOT NULL,
    max_hits INTEGER NOT NULL,
    multiplier REAL NOT NULL,
    started_at REAL NOT NULL,
    submitted_at REAL,
    raw_score INTEGER,
    score REAL,
    hits INTEGER,
    stats TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    verified_at REAL,
    created_at REAL NOT NULL
);
-- Session and email tokens are stored only as SHA-256 hashes, so a leaked database can't be used to sign in.
CREATE TABLE IF NOT EXISTS sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL
);
-- A verify token carries the password chosen with it, so verifying sets that registration's password: someone
-- who registers another person's email first can't pick the password that person's click then confirms.
CREATE TABLE IF NOT EXISTS email_tokens (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL REFERENCES users(id),
    purpose TEXT NOT NULL,
    expires_at REAL NOT NULL,
    password_hash TEXT
);
-- Whose pet is whose: one per account each semester (see PetStore.create).
CREATE TABLE IF NOT EXISTS pet_owners (
    pet_id INTEGER PRIMARY KEY REFERENCES pets(id),
    user_id INTEGER NOT NULL REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS pet_owners_user ON pet_owners (user_id);
"""
# PRAGMA user_version once every migration below has run.
_VERSION = 2

# Columns added after the first release, so older databases get them on startup.
_ADDED = {
    "pets": {
        "invested": "REAL NOT NULL DEFAULT 0",
        "experiments": "TEXT NOT NULL DEFAULT '[]'",
        "work_started_at": "REAL",
        "billed_day": "REAL",
        "internship": "TEXT NOT NULL DEFAULT 'null'",
        "best_internship_pay": "REAL NOT NULL DEFAULT 0",
        "bonuses": "TEXT NOT NULL DEFAULT '[]'",
        "train_day": "REAL",
        "train_streak": "INTEGER NOT NULL DEFAULT 0",
        "trip": "TEXT NOT NULL DEFAULT 'null'",
        "items": "TEXT NOT NULL DEFAULT '[]'",
        "subscriptions": "TEXT NOT NULL DEFAULT '{}'",
        "swipes": "INTEGER NOT NULL DEFAULT 0",
        "fitness": "REAL NOT NULL DEFAULT 0",
        # Every pet lived in Harnwell before houses (Config.default_house).
        "house": "TEXT NOT NULL DEFAULT 'harnwell'",
        # Config.starting_gpa.
        "gpa": "REAL NOT NULL DEFAULT 3.5",
        "work_hours": "REAL NOT NULL DEFAULT 0",
        "next_event_at": "REAL",
        "events_seen": "INTEGER NOT NULL DEFAULT 0",
        "event": "TEXT NOT NULL DEFAULT 'null'",
        # Pets from before semesters join the one running when the server starts (PetStore.adopt).
        "semester": "INTEGER",
        "doordashes": "INTEGER NOT NULL DEFAULT 0",
        "study_week": "REAL",
        "study_days": "INTEGER NOT NULL DEFAULT 0",
        "study_day": "REAL",
    },
    "fair_entries": {
        "backing": "INTEGER",
        "short_on": "INTEGER",
        "short_stake": "REAL NOT NULL DEFAULT 0",
        "short_payout": "REAL",
    },
    "fairs": {"game": "TEXT NOT NULL DEFAULT 'locust-walk'"},
    "fair_attempts": {"stats": "TEXT NOT NULL DEFAULT '{}'"},
}

# Table and column names interpolated into SQL. They come from this file, never from a request; the check is
# so a later edit can't put a player's string in that spot by mistake.
_IDENT = re.compile(r"^[a-z_]+$")
# What a SQL injection tries once it can run its own statement: open another file, or read and write the
# server's files through SQLite's optional functions.
_BLOCKED_FUNCTIONS = {"load_extension", "readfile", "writefile", "edit", "fts3_tokenizer"}


def _ident(name: str) -> str:
    if _IDENT.fullmatch(name) is None:
        raise RuntimeError(f"refusing to put {name!r} into SQL")
    return name


def _guard_sql(action, arg1, arg2, db_name, source):
    if action in (sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH):
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_FUNCTION and str(arg2 or "").lower() in _BLOCKED_FUNCTIONS:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


_COLUMNS = (
    "name", "fullness", "energy", "money", "activity", "building", "career", "path", "xp", "upgrades",
    "rest_multiplier", "sleep_hours", "day_started_at", "penalty_start", "penalty_end",
    "coffee_until", "updated_at", "invested", "experiments", "work_started_at", "billed_day",
    "internship", "best_internship_pay", "bonuses", "train_day", "train_streak", "trip", "items",
    "subscriptions", "swipes", "fitness", "house", "gpa", "work_hours", "next_event_at", "events_seen", "event",
    "semester", "doordashes", "study_week", "study_days", "study_day",
)
_JSON = ("xp", "upgrades", "experiments", "internship", "bonuses", "trip", "items", "subscriptions", "event")


@dataclass
class User:
    id: int
    email: str
    password_hash: str
    verified_at: float | None
    created_at: float


@dataclass
class Fair:
    id: int
    opens_at: float
    closes_at: float
    results_at: float
    # Role ids posted at this fair, across every career.
    roles: list[str]
    resolved: bool = False
    # The minigame scored runs play, from minigames.games.
    game: str = "locust-walk"


@dataclass
class Entry:
    """One pet's applications at one fair."""

    fair_id: int
    pet_id: int
    # Role ids in the pet's order of preference.
    roles: list[str] = field(default_factory=list)
    # The application made with a referral, if any.
    referral: str | None = None
    # When the pet started its first scored run, after which applications can't change.
    locked_at: float | None = None
    late_changes: int = 0
    won: str | None = None
    # The pet this one backs with Amicus Brief, and its Short Seller bet: on which pet, how much, and what
    # it paid back at results (None until then).
    backing: int | None = None
    short_on: int | None = None
    short_stake: float = 0.0
    short_payout: float | None = None


@dataclass
class Attempt:
    id: int
    fair_id: int
    pet_id: int
    seed: int
    seconds: int
    max_hits: int
    multiplier: float
    started_at: float
    submitted_at: float | None = None
    raw_score: int | None = None
    score: float | None = None
    hits: int | None = None
    # Game-specific details of the result, like distance walked or floors stacked.
    stats: dict = field(default_factory=dict)


class Tx:
    """Reads and writes inside one write transaction."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def pet(self, pet_id: int) -> Pet | None:
        row = self.conn.execute("SELECT * FROM pets WHERE id = ?", (pet_id,)).fetchone()
        return _pet(row) if row else None

    def pets(self, semester: int | None = None) -> list[Pet]:
        """Every pet, or only semester `semester`'s."""
        if semester is None:
            rows = self.conn.execute("SELECT * FROM pets ORDER BY id")
        else:
            rows = self.conn.execute("SELECT * FROM pets WHERE semester = ? ORDER BY id", (semester,))
        return [_pet(row) for row in rows]

    def save_pet(self, pet: Pet) -> None:
        assignments = ", ".join(f"{_ident(c)} = ?" for c in _COLUMNS)
        self.conn.execute(f"UPDATE pets SET {assignments} WHERE id = ?", (*_to_row(pet), pet.id))

    def latest_fair(self) -> Fair | None:
        row = self.conn.execute("SELECT * FROM fairs ORDER BY opens_at DESC LIMIT 1").fetchone()
        return _fair(row) if row else None

    def fair(self, fair_id: int) -> Fair | None:
        row = self.conn.execute("SELECT * FROM fairs WHERE id = ?", (fair_id,)).fetchone()
        return _fair(row) if row else None

    def previous_fair(self, fair: Fair) -> Fair | None:
        row = self.conn.execute(
            "SELECT * FROM fairs WHERE opens_at < ? ORDER BY opens_at DESC LIMIT 1", (fair.opens_at,)
        ).fetchone()
        return _fair(row) if row else None

    def last_resolved_fair(self) -> Fair | None:
        row = self.conn.execute(
            "SELECT * FROM fairs WHERE resolved = 1 ORDER BY opens_at DESC LIMIT 1"
        ).fetchone()
        return _fair(row) if row else None

    def add_fair(self, opens_at: float, closes_at: float, results_at: float, roles: list[str],
                 game: str = "locust-walk") -> Fair:
        cur = self.conn.execute(
            "INSERT INTO fairs (opens_at, closes_at, results_at, roles, game) VALUES (?, ?, ?, ?, ?)",
            (opens_at, closes_at, results_at, json.dumps(roles), game),
        )
        return Fair(cur.lastrowid, opens_at, closes_at, results_at, roles, game=game)

    def mark_resolved(self, fair: Fair) -> None:
        self.conn.execute("UPDATE fairs SET resolved = 1 WHERE id = ?", (fair.id,))
        fair.resolved = True

    def entry(self, fair_id: int, pet_id: int) -> Entry | None:
        row = self.conn.execute(
            "SELECT * FROM fair_entries WHERE fair_id = ? AND pet_id = ?", (fair_id, pet_id)
        ).fetchone()
        return _entry(row) if row else None

    def entries(self, fair_id: int) -> list[Entry]:
        rows = self.conn.execute("SELECT * FROM fair_entries WHERE fair_id = ? ORDER BY pet_id", (fair_id,))
        return [_entry(row) for row in rows]

    def wins(self, pet_ids: list[int]) -> dict[int, list[str]]:
        """Pet id -> the role ids it won at fairs, in fair order, for each of `pet_ids` that won any."""
        won: dict[int, list[str]] = {}
        wanted = set(pet_ids)
        for row in self.conn.execute("SELECT pet_id, won FROM fair_entries WHERE won IS NOT NULL ORDER BY fair_id"):
            if row["pet_id"] in wanted:
                won.setdefault(row["pet_id"], []).append(row["won"])
        return won

    def pet_entries(self, pet_id: int) -> list[Entry]:
        rows = self.conn.execute("SELECT * FROM fair_entries WHERE pet_id = ? ORDER BY fair_id", (pet_id,))
        return [_entry(row) for row in rows]

    def save_entry(self, e: Entry) -> None:
        self.conn.execute(
            "INSERT INTO fair_entries (fair_id, pet_id, roles, referral, locked_at, late_changes, won, backing, "
            "short_on, short_stake, short_payout) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (fair_id, pet_id) DO UPDATE SET roles = excluded.roles, "
            "referral = excluded.referral, locked_at = excluded.locked_at, "
            "late_changes = excluded.late_changes, won = excluded.won, backing = excluded.backing, "
            "short_on = excluded.short_on, short_stake = excluded.short_stake, short_payout = excluded.short_payout",
            (e.fair_id, e.pet_id, json.dumps(e.roles), e.referral, e.locked_at, e.late_changes, e.won, e.backing,
             e.short_on, e.short_stake, e.short_payout),
        )

    def attempts(self, fair_id: int, pet_id: int | None = None) -> list[Attempt]:
        if pet_id is None:
            rows = self.conn.execute("SELECT * FROM fair_attempts WHERE fair_id = ? ORDER BY id", (fair_id,))
        else:
            rows = self.conn.execute(
                "SELECT * FROM fair_attempts WHERE fair_id = ? AND pet_id = ? ORDER BY id", (fair_id, pet_id)
            )
        return [_attempt(row) for row in rows]

    def attempt(self, attempt_id: int) -> Attempt | None:
        row = self.conn.execute("SELECT * FROM fair_attempts WHERE id = ?", (attempt_id,)).fetchone()
        return _attempt(row) if row else None

    def add_attempt(self, fair_id: int, pet_id: int, seed: int, seconds: int, max_hits: int,
                    multiplier: float, started_at: float) -> Attempt:
        cur = self.conn.execute(
            "INSERT INTO fair_attempts (fair_id, pet_id, seed, seconds, max_hits, multiplier, started_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (fair_id, pet_id, seed, seconds, max_hits, multiplier, started_at),
        )
        return Attempt(cur.lastrowid, fair_id, pet_id, seed, seconds, max_hits, multiplier, started_at)

    def save_attempt(self, a: Attempt) -> None:
        self.conn.execute(
            "UPDATE fair_attempts SET submitted_at = ?, raw_score = ?, score = ?, hits = ?, stats = ? WHERE id = ?",
            (a.submitted_at, a.raw_score, a.score, a.hits, json.dumps(a.stats), a.id),
        )


class PetStore:
    def __init__(self, path: str):
        self.path = path
        with closing(self._connect()) as conn:
            conn.executescript(_SCHEMA)
            for table, columns in _ADDED.items():
                table = _ident(table)
                have = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
                for column, kind in columns.items():
                    if column not in have:
                        conn.execute(f"ALTER TABLE {table} ADD COLUMN {_ident(column)} {kind}")
                if table == "fair_attempts" and "stats" not in have and "distance" in have:
                    # Runs scored before stats existed were all Locust Walk runs.
                    conn.execute("UPDATE fair_attempts SET stats = json_object('distance', distance, 'swag', swag) "
                                 "WHERE distance IS NOT NULL")
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version < 1:
                # Pets from before accounts had no owner, so anyone could play them; everyone starts over.
                conn.executescript("DELETE FROM fair_attempts; DELETE FROM fair_entries; DELETE FROM pets;")
            if version < 2:
                # pet_owners was keyed by user (one pet per account, ever) until semesters.
                conn.executescript("""
                    BEGIN;
                    CREATE TABLE pet_owners_new (pet_id INTEGER PRIMARY KEY REFERENCES pets(id),
                                                 user_id INTEGER NOT NULL REFERENCES users(id));
                    INSERT INTO pet_owners_new (pet_id, user_id) SELECT pet_id, user_id FROM pet_owners;
                    DROP TABLE pet_owners;
                    ALTER TABLE pet_owners_new RENAME TO pet_owners;
                    CREATE INDEX IF NOT EXISTS pet_owners_user ON pet_owners (user_id);
                    COMMIT;
                """)
            conn.execute(f"PRAGMA user_version = {_VERSION}")

    def adopt(self, semester: int) -> None:
        """Put pets from before semesters in semester `semester`."""
        with closing(self._connect()) as conn:
            conn.execute("UPDATE pets SET semester = ? WHERE semester IS NULL", (semester,))

    def create(self, pet: Pet, owner: int | None = None) -> Pet:
        """Add a pet, owned by user `owner` if given. Raises sqlite3.IntegrityError if that user already has a
        pet in the same semester (checked in the same transaction, so two hatches can't race past it)."""
        with self.transaction() as tx:
            if owner is not None and tx.conn.execute(
                "SELECT 1 FROM pet_owners JOIN pets ON pets.id = pet_owners.pet_id "
                "WHERE pet_owners.user_id = ? AND pets.semester IS ?", (owner, pet.semester)
            ).fetchone():
                raise sqlite3.IntegrityError("that player already has a pet this semester")
            placeholders = ", ".join("?" for _ in _COLUMNS)
            cur = tx.conn.execute(
                f"INSERT INTO pets ({', '.join(_ident(c) for c in _COLUMNS)}) VALUES ({placeholders})",
                _to_row(pet),
            )
            pet.id = cur.lastrowid
            if owner is not None:
                tx.conn.execute("INSERT INTO pet_owners (user_id, pet_id) VALUES (?, ?)", (owner, pet.id))
        return pet

    # --- Accounts ---

    def add_user(self, email: str, password_hash: str, now: float) -> User | None:
        """A new unverified user, or None if the email is taken."""
        with closing(self._connect()) as conn:
            try:
                cur = conn.execute("INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, ?)",
                                   (email, password_hash, now))
            except sqlite3.IntegrityError:
                return None
            return User(cur.lastrowid, email, password_hash, None, now)

    def user_by_email(self, email: str) -> User | None:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
            return _user(row) if row else None

    def set_password(self, user_id: int, password_hash: str) -> None:
        with closing(self._connect()) as conn:
            conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id))

    def mark_verified(self, user_id: int, now: float) -> None:
        with closing(self._connect()) as conn:
            conn.execute("UPDATE users SET verified_at = coalesce(verified_at, ?) WHERE id = ?", (now, user_id))

    def add_session(self, user_id: int, token_hash: str, now: float, expires_at: float) -> None:
        with closing(self._connect()) as conn:
            conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (now,))
            conn.execute("INSERT INTO sessions (token_hash, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                         (token_hash, user_id, now, expires_at))

    def session_user(self, token_hash: str, now: float) -> User | None:
        """The verified user signed in with this session, if it hasn't expired."""
        with closing(self._connect()) as conn:
            row = conn.execute(
                "SELECT users.* FROM sessions JOIN users ON users.id = sessions.user_id "
                "WHERE token_hash = ? AND expires_at > ? AND verified_at IS NOT NULL", (token_hash, now)
            ).fetchone()
            return _user(row) if row else None

    def end_session(self, token_hash: str) -> None:
        with closing(self._connect()) as conn:
            conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash,))

    def end_sessions(self, user_id: int) -> None:
        with closing(self._connect()) as conn:
            conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))

    def add_email_token(self, user_id: int, token_hash: str, purpose: str, expires_at: float,
                        password_hash: str | None = None, replace: bool = True) -> None:
        """A one-time token for an emailed link, replacing any earlier one for the same purpose unless not
        `replace`."""
        with closing(self._connect()) as conn:
            if replace:
                conn.execute("DELETE FROM email_tokens WHERE user_id = ? AND purpose = ?", (user_id, purpose))
            conn.execute("INSERT INTO email_tokens (token_hash, user_id, purpose, expires_at, password_hash) "
                         "VALUES (?, ?, ?, ?, ?)", (token_hash, user_id, purpose, expires_at, password_hash))

    def take_email_token(self, token_hash: str, purpose: str, now: float) -> tuple[int, str | None] | None:
        """Use up an emailed token, returning its user and the password it carries, if it was valid and
        unexpired. Every other token of that user for the same purpose goes with it."""
        with self.transaction() as tx:
            row = tx.conn.execute("SELECT * FROM email_tokens WHERE token_hash = ? AND purpose = ?",
                                  (token_hash, purpose)).fetchone()
            tx.conn.execute("DELETE FROM email_tokens WHERE token_hash = ? OR expires_at <= ?", (token_hash, now))
            if not row or row["expires_at"] <= now:
                return None
            tx.conn.execute("DELETE FROM email_tokens WHERE user_id = ? AND purpose = ?", (row["user_id"], purpose))
        return row["user_id"], row["password_hash"]

    def user(self, user_id: int) -> User | None:
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
            return _user(row) if row else None

    def pet_of(self, user_id: int, semester: int) -> int | None:
        """The user's pet in semester `semester`, if they hatched one."""
        with closing(self._connect()) as conn:
            row = conn.execute("SELECT pet_id FROM pet_owners JOIN pets ON pets.id = pet_owners.pet_id "
                               "WHERE pet_owners.user_id = ? AND pets.semester = ?", (user_id, semester)).fetchone()
            return row["pet_id"] if row else None

    def owns(self, user_id: int, pet_id: int) -> bool:
        with closing(self._connect()) as conn:
            return conn.execute("SELECT 1 FROM pet_owners WHERE user_id = ? AND pet_id = ?",
                                (user_id, pet_id)).fetchone() is not None

    def get(self, pet_id: int) -> Pet | None:
        with closing(self._connect()) as conn:
            return Tx(conn).pet(pet_id)

    def read(self, query: Callable[[Tx], object]):
        """Run read-only queries without taking the write lock."""
        with closing(self._connect()) as conn:
            return query(Tx(conn))

    @contextmanager
    def transaction(self) -> Iterator[Tx]:
        """A write transaction: concurrent writers wait for each other instead of overwriting."""
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield Tx(conn)
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            conn.execute("COMMIT")

    def update(self, pet_id: int, change: Callable[[Pet], Pet]) -> Pet | None:
        """Load, change, and save a pet inside one write transaction, so concurrent actions on the
        same pet serialize instead of overwriting each other."""
        with self.transaction() as tx:
            pet = tx.pet(pet_id)
            if pet is None:
                return None
            pet = change(pet)
            tx.save_pet(pet)
        return pet

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, isolation_level=None, timeout=10)
        conn.row_factory = sqlite3.Row
        # Player values are bound with "?". This is the second line: no statement, injected or otherwise, can
        # attach another database or read server files, and foreign keys are actually enforced.
        conn.set_authorizer(_guard_sql)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA trusted_schema = OFF")
        return conn


def _pet(row: sqlite3.Row) -> Pet:
    data = {c: row[c] for c in _COLUMNS}
    data["activity"] = Activity(data["activity"])
    for c in _JSON:
        data[c] = json.loads(data[c])
    return Pet(id=row["id"], **data)


def _user(row: sqlite3.Row) -> User:
    return User(row["id"], row["email"], row["password_hash"], row["verified_at"], row["created_at"])


def _fair(row: sqlite3.Row) -> Fair:
    return Fair(row["id"], row["opens_at"], row["closes_at"], row["results_at"], json.loads(row["roles"]),
                bool(row["resolved"]), row["game"])


def _attempt(row: sqlite3.Row) -> Attempt:
    return Attempt(row["id"], row["fair_id"], row["pet_id"], row["seed"], row["seconds"], row["max_hits"],
                   row["multiplier"], row["started_at"], row["submitted_at"], row["raw_score"], row["score"],
                   row["hits"], json.loads(row["stats"]))


def _entry(row: sqlite3.Row) -> Entry:
    return Entry(row["fair_id"], row["pet_id"], json.loads(row["roles"]), row["referral"], row["locked_at"],
                 row["late_changes"], row["won"], row["backing"], row["short_on"], row["short_stake"],
                 row["short_payout"])


def _to_row(pet: Pet) -> tuple:
    values = {c: getattr(pet, c) for c in _COLUMNS}
    values["activity"] = pet.activity.value
    for c in _JSON:
        values[c] = json.dumps(values[c])
    return tuple(values[c] for c in _COLUMNS)
