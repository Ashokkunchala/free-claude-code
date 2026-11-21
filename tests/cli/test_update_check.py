"""The updater checks the fork's main commit before authorizing installation."""

from importlib.metadata import PackageNotFoundError

import httpx
import pytest

from free_claude_code.updater import check as update_check

INSTALLED_SHA = "1" * 40
REMOTE_SHA = "2" * 40


def commit(sha=REMOTE_SHA):
    return {"sha": sha}


@pytest.fixture
def check(monkeypatch):
    original_client = httpx.Client

    def run(installed="1.0.0", payload=None, status=200, error=None, body=None):
        monkeypatch.setattr(update_check, "version", lambda name: installed)

        def respond(request):
            assert str(request.url) == update_check.COMMITS_URL
            if error is not None:
                raise error
            if body is not None:
                return httpx.Response(status, content=body)
            return httpx.Response(status, json=payload)

        monkeypatch.setattr(
            update_check.httpx,
            "Client",
            lambda **kwargs: original_client(
                transport=httpx.MockTransport(respond), **kwargs
            ),
        )
        return update_check.main()

    return run


@pytest.mark.parametrize(
    ("installed", "payload", "expected"),
    [
        ("1.0.0", commit(), 10),
        ("1.0.0.dev1", commit(), 10),
        (f"1.0.0.dev1+g{INSTALLED_SHA}", commit(INSTALLED_SHA), 0),
        (f"1.0.0.dev1+g{INSTALLED_SHA}", commit(), 10),
        (f"1.0.0.dev1+g{INSTALLED_SHA}.dirty", commit(INSTALLED_SHA), 0),
        ("1.0.0+build.7", commit(), 10),
    ],
)
def test_commit_comparison(check, capsys, installed, payload, expected):
    assert check(installed, payload) == expected
    output = capsys.readouterr()
    assert installed in output.out
    assert not output.err


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"sha": 123},
        {"sha": None},
        {"sha": "not-a-commit"},
        {"sha": "1" * 39},
        {"sha": "g" + "1" * 39},
    ],
)
def test_invalid_commit_does_not_authorize_installation(check, capsys, payload):
    assert check(payload=payload) == 1
    assert capsys.readouterr().err


@pytest.mark.parametrize("installed", ["invalid", "0+unknown"])
def test_unknown_installed_version(check, installed):
    assert check(installed, commit()) == 1


def test_missing_distribution(monkeypatch, capsys):
    def missing(name):
        raise PackageNotFoundError(name)

    monkeypatch.setattr(update_check, "version", missing)
    assert update_check.main() == 1
    assert capsys.readouterr().err


def test_http_error(check, capsys):
    assert check(payload=commit(), status=503) == 1
    assert capsys.readouterr().err


def test_timeout(check, capsys):
    assert check(error=httpx.ReadTimeout("timed out")) == 1
    assert capsys.readouterr().err


def test_malformed_json(check, capsys):
    assert check(body="not JSON") == 1
    assert capsys.readouterr().err
