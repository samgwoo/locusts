import dataclasses
import math

import pytest

from locusts import auth, houses, pet
from locusts.config import DEFAULT_CONFIG


@pytest.fixture(autouse=True)
def cheap_passwords(monkeypatch):
    """Real password hashing is slow on purpose; tests sign up a lot of players."""
    monkeypatch.setattr(auth, "SCRYPT_N", 2**4)


@pytest.fixture(autouse=True)
def instant_travel(request):
    """Most tests are about what a pet does once it's somewhere, so unless a test is marked `travel`,
    pets arrive the moment they're sent. Actions take DEFAULT_CONFIG as a default argument, so the
    frozen instance itself has to change."""
    if request.node.get_closest_marker("travel"):
        yield
        return
    speed = DEFAULT_CONFIG.travel_px_per_second
    object.__setattr__(DEFAULT_CONFIG, "travel_px_per_second", math.inf)
    yield
    object.__setattr__(DEFAULT_CONFIG, "travel_px_per_second", speed)


@pytest.fixture(autouse=True)
def plain_houses(request, monkeypatch):
    """Every house has a small perk, which would shift the numbers of tests about everything else, so
    unless a test is marked `houses`, houses keep their places but lose their perks."""
    if request.node.get_closest_marker("houses"):
        return
    plain = {h.id: dataclasses.replace(h, effects={}) for h in houses.houses().values()}
    monkeypatch.setattr(pet, "houses", lambda: plain)
