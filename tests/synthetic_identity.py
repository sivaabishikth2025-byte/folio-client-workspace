"""Ephemeral identities for mocked authentication; never provisioned in a service."""
from functools import lru_cache
from secrets import token_hex, token_urlsafe


@lru_cache(maxsize=None)
def synthetic_email(label):
    return token_hex(12) + '@' + 'example.invalid'


def synthetic_password():
    return token_urlsafe(32) + 'aA1!'
