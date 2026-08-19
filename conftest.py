"""
Shared test auth helpers for D3 Round4+.

Provides token-based authentication for all integration tests.
Client-supplied identity fields (current_user_id, user_id, role, etc.)
are NOT trusted by the backend - only X-Auth-Token is used.
"""

# Maps test user IDs to their demo tokens (must match auth.py DEMO_TOKEN_MAP)
USER_TOKEN_MAP: dict[str, str] = {
    "USER-1": "tok-user-1",
    "USER-2": "tok-user-2",
    "USER-3": "tok-user-3",
    "MANAGER-1": "tok-manager-1",
    "MANAGER-1": "tok-manager-1",
    "OPERATOR-A1": "tok-operator-a1",
    "MANAGER-A": "tok-manager-a",
    "OPERATOR-B1": "tok-operator-b1",
    "MANAGER-B": "tok-manager-b",
}


def auth_headers(user_id: str) -> dict[str, str]:
    """
    Return HTTP headers with X-Auth-Token for the given test user.
    
    Usage:
        client.get("/api/orders", headers=auth_headers("USER-1"))
        client.post("/api/tasks/xxx/complete", headers=auth_headers("MANAGER-1"), json={...})
    
    Raises KeyError if user_id is not in the known map.
    """
    token = USER_TOKEN_MAP.get(user_id)
    if not token:
        raise KeyError(
            f"Unknown test user_id '{user_id}'. "
            f"Known users: {sorted(USER_TOKEN_MAP.keys())}"
        )
    return {"X-Auth-Token": token}


def auth_header(user_id: str) -> str:
    """Return just the token string for a given user_id."""
    token = USER_TOKEN_MAP.get(user_id)
    if not token:
        raise KeyError(f"Unknown test user_id '{user_id}'")
    return token