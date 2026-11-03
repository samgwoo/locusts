import re
from datetime import datetime

from fastapi.testclient import TestClient

from locusts.config import DEFAULT_CONFIG

HOUR = 3600.0
PASSWORD = "correct horse battery"


class Outbox:
    """A mailer that keeps what it sends, for the links in it."""

    def __init__(self):
        self.sent: list[dict] = []

    def send(self, to: str, subject: str, body: str) -> None:
        self.sent.append({"to": to, "subject": subject, "body": body})

    def to(self, email: str) -> list[dict]:
        return [m for m in self.sent if m["to"] == email]

    def link(self, email: str) -> str:
        """The link in the latest email to `email`."""
        return re.search(r"https?://\S+", self.to(email)[-1]["body"]).group(0)


def browser(app) -> TestClient:
    """A client that sends what the game's pages send with every API request."""
    return TestClient(app, headers={"X-Locusts": "1"})


def player(app, outbox: Outbox, name: str = "gregor") -> TestClient:
    """A browser signed in as a new player, `name`@seas.upenn.edu, who signed up and clicked the email link."""
    client = browser(app)
    email = f"{name}@seas.upenn.edu"
    assert client.post("/api/auth/register", json={"email": email, "password": PASSWORD}).status_code == 202
    assert client.get(outbox.link(email)).status_code == 200
    return client


def et(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> float:
    """Timestamp for a campus wall-clock time."""
    return datetime(year, month, day, hour, minute, tzinfo=DEFAULT_CONFIG.timezone).timestamp()
