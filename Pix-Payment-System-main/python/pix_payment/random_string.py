"""Random string helper — Python variant of br.com.jujubaprojects.paymentsystem.Utils.RandomString."""

import secrets

CHARACTERS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"


def generate_random_string(length: int) -> str:
    """Generates a random string of the given length over the alphanumeric alphabet."""
    return "".join(secrets.choice(CHARACTERS) for _ in range(length))
