import textwrap

from pyconduit.config import AuthMode, load_config


def test_defaults_when_no_file():
    cfg = load_config(None)
    assert cfg.server.port == 8080
    assert cfg.auth.mode is AuthMode.proxy
    assert cfg.xmpp.reconnect.factor == 2.0


def test_loads_yaml(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(
        textwrap.dedent(
            """
            server:
              port: 9999
              brand_title: Acme Chat
            auth:
              mode: dev
            xmpp:
              idle_timeout_seconds: 42
            """
        ),
        encoding="utf-8",
    )
    cfg = load_config(p)
    assert cfg.server.port == 9999
    assert cfg.server.brand_title == "Acme Chat"
    assert cfg.auth.mode is AuthMode.dev
    assert cfg.xmpp.idle_timeout_seconds == 42


def test_missing_file_is_defaults(tmp_path):
    cfg = load_config(tmp_path / "does-not-exist.yaml")
    assert cfg.server.port == 8080


def test_env_overrides_yaml(tmp_path, monkeypatch):
    p = tmp_path / "c.yaml"
    p.write_text("server:\n  port: 1234\n", encoding="utf-8")
    monkeypatch.setenv("PYCONDUIT_SERVER__PORT", "5678")
    cfg = load_config(p)
    assert cfg.server.port == 5678


def test_audit_default_destination():
    cfg = load_config(None)
    assert len(cfg.audit.destinations) == 1
    assert cfg.audit.destinations[0].type == "stdout"
