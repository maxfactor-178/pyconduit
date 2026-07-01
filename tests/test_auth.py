import json

import pytest

from pyconduit.auth import AuthError, AuthMapper, extract_username


@pytest.fixture
def mapper(tmp_path):
    users = tmp_path / "users.json"
    creds = tmp_path / "credentials.json"
    users.write_text(
        json.dumps({"alice": "alice@example.com", "desk1": "support@example.com",
                    "desk2": "support@example.com"}),
        encoding="utf-8",
    )
    creds.write_text(
        json.dumps({"alice@example.com": "apw", "support@example.com": "spw"}),
        encoding="utf-8",
    )
    return AuthMapper.from_files(users, creds)


def test_resolve_known_user(mapper):
    ident = mapper.resolve("alice")
    assert ident.jid == "alice@example.com"
    assert ident.password == "apw"
    assert ident.username == "alice"


def test_helpdesk_many_users_one_jid(mapper):
    # Two distinct usernames map to the same shared account.
    assert mapper.resolve("desk1").jid == "support@example.com"
    assert mapper.resolve("desk2").jid == "support@example.com"
    assert mapper.resolve("desk1").password == "spw"


def test_unknown_user(mapper):
    with pytest.raises(AuthError):
        mapper.resolve("nobody")


def test_missing_credentials(tmp_path):
    users = tmp_path / "u.json"
    creds = tmp_path / "c.json"
    users.write_text(json.dumps({"bob": "bob@example.com"}), encoding="utf-8")
    creds.write_text(json.dumps({}), encoding="utf-8")
    m = AuthMapper.from_files(users, creds)
    with pytest.raises(AuthError):
        m.resolve("bob")


def test_missing_mapping_file(tmp_path):
    with pytest.raises(AuthError):
        AuthMapper.from_files(tmp_path / "nope.json", tmp_path / "creds.json")


def test_extract_username_dev_query():
    got = extract_username(
        mode="dev", header_name="X-Remote-User", headers={},
        query_user="carol", dev_default_user="alice",
    )
    assert got == "carol"


def test_extract_username_dev_default():
    got = extract_username(
        mode="dev", header_name="X-Remote-User", headers={},
        query_user=None, dev_default_user="alice",
    )
    assert got == "alice"


def test_extract_username_proxy_header_case_insensitive():
    got = extract_username(
        mode="proxy", header_name="X-Remote-User",
        headers={"x-remote-user": "dave"}, query_user="ignored",
        dev_default_user="alice",
    )
    assert got == "dave"


def test_extract_username_proxy_missing_header():
    with pytest.raises(AuthError):
        extract_username(
            mode="proxy", header_name="X-Remote-User", headers={},
            query_user=None, dev_default_user="alice",
        )
