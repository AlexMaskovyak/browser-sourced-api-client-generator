"""Private-profile persistence and exact prepared-request adapter contracts."""

import json
from unittest.mock import Mock

import httpx
import pytest

from website_client.authenticated_http import AuthenticatedDispatch, SDKTransport, profile_dispatch, workflow_session
from website_client.runtime_profile import ProfileError, ProfileStore


def test_private_atomic_profile_and_origin_lock(tmp_path, monkeypatch):
    root = tmp_path / 'profiles'
    monkeypatch.setenv('WEBSITE_CLIENT_PROFILE_DIR', str(root))
    with ProfileStore('neutral', 'https://neutral.invalid').locked() as store:
        store.save({'cookies': [], 'origins': []}, {'authorization': 'SYNTHETIC'})
        assert store.load()['headers'] == {'authorization': 'SYNTHETIC'}
        assert store.path.stat().st_mode & 0o777 == 0o600
        assert store.root.stat().st_mode & 0o777 == 0o700
        assert store.lock_path.stat().st_mode & 0o777 == 0o600
        assert not list(root.glob('.profile-*'))
    with pytest.raises(ProfileError, match='profile_origin_mismatch'):
        with ProfileStore('neutral', 'https://different.invalid').locked() as store:
            store.load()


def test_adapters_preserve_prepared_body_and_no_retry_policy():
    api, response = Mock(), Mock()
    response.status, response.headers_array = 200, []
    response.body.return_value = b'{}'
    api.fetch.return_value = response
    dispatch = AuthenticatedDispatch(api, 'https://neutral.invalid', {'authorization': 'SYNTHETIC'})
    body = bytes(range(256))
    with httpx.Client(transport=SDKTransport(dispatch), trust_env=False) as client:
        client.put('https://neutral.invalid/binary', content=body, headers={'Cookie': 'CALLER'})
    assert api.fetch.call_args.kwargs['data'] == body
    assert all(name.lower() != 'cookie' for name in api.fetch.call_args.kwargs['headers'])
    with workflow_session(dispatch) as session:
        assert session.trust_env is False
        session.put('https://neutral.invalid/binary', data=body)
    assert api.fetch.call_args.kwargs['data'] == body
    assert api.fetch.call_args.kwargs['max_redirects'] == 0
    assert api.fetch.call_args.kwargs['max_retries'] == 0
    assert response.dispose.call_count == 2


def test_context_saves_cookie_renewal_on_failure_and_disposes(tmp_path, monkeypatch):
    monkeypatch.setenv('WEBSITE_CLIENT_PROFILE_DIR', str(tmp_path / 'profiles'))
    with ProfileStore('neutral', 'https://neutral.invalid').locked() as store:
        store.save({'cookies': [], 'origins': []}, {})
    driver, api = Mock(), Mock()
    driver.request.new_context.return_value = api
    api.storage_state.return_value = {'cookies': [{'domain': 'neutral.invalid', 'name': 'sid', 'value': 'ROTATED'}], 'origins': []}
    manager = Mock(__enter__=Mock(return_value=driver), __exit__=Mock(return_value=False))
    monkeypatch.setattr('playwright.sync_api.sync_playwright', lambda: manager)
    with pytest.raises(RuntimeError, match='job failed'):
        with profile_dispatch('neutral', 'https://neutral.invalid'):
            raise RuntimeError('job failed')
    api.dispose.assert_called_once()
    assert driver.request.new_context.call_args.kwargs['ignore_https_errors'] is False
    with ProfileStore('neutral', 'https://neutral.invalid').locked() as store:
        assert store.load()['storage_state']['cookies'][0]['value'] == 'ROTATED'


@pytest.mark.parametrize('stage', ['fetch', 'body', 'dispose'])
def test_dispatch_driver_errors_never_expose_credential_logs(stage):
    import traceback
    from website_client.authenticated_http import TransportFailure
    api, response = Mock(), Mock()
    api.fetch.return_value = response
    response.status, response.headers_array = 200, []
    response.body.return_value = b'{}'
    private_error = RuntimeError('Call log: Cookie: sid=COOKIE_CANARY; Authorization: Bearer BEARER_CANARY')
    getattr(api if stage == 'fetch' else response, stage).side_effect = private_error
    dispatch = AuthenticatedDispatch(api, 'https://neutral.invalid', {'authorization': 'Bearer BEARER_CANARY'})
    with pytest.raises(TransportFailure) as failure:
        dispatch.send('POST', 'https://neutral.invalid/write', {}, b'one write')
    formatted = ''.join(traceback.format_exception(failure.value))
    for secret in ('COOKIE_CANARY', 'BEARER_CANARY'):
        assert secret not in str(failure.value)
        assert secret not in formatted
    assert api.fetch.call_count == 1
    if stage != 'fetch':
        response.dispose.assert_called_once()


def test_initial_cookie_ownership_is_not_shared_between_origin_ports(tmp_path, monkeypatch):
    from website_client.runtime_profile import OriginCaptures, save_browser_profile
    first, second = 'http://127.0.0.1:8101', 'http://127.0.0.1:8102'
    captures = OriginCaptures(first)
    captures.declare(second)
    captures.observe_request(Mock(url=first, all_headers=lambda: {'cookie': 'sid=SHARED_COOKIE'}))
    assert captures.cookies_for(first) == {('sid', 'SHARED_COOKIE')}
    assert captures.cookies_for(second) == set()
    captures.observe_request(Mock(url=second, all_headers=lambda: {'cookie': 'sid=SHARED_COOKIE'}))
    assert captures.cookies_for(first) == captures.cookies_for(second) == set()
    monkeypatch.setenv('WEBSITE_CLIENT_PROFILE_DIR', str(tmp_path / 'profiles'))
    context = Mock()
    context.storage_state.return_value = {'cookies': [{'name': 'sid', 'value': 'SHARED_COOKIE', 'domain': '127.0.0.1', 'path': '/'}], 'origins': []}
    for index, endpoint in enumerate((first, second)):
        identifier = 'origin' + str(index)
        save_browser_profile(context, identifier, endpoint, owned_cookies=captures.cookies_for(endpoint))
        with ProfileStore(identifier, endpoint).locked() as store:
            assert store.load()['storage_state']['cookies'] == []


def test_response_owned_renewal_needs_no_followup_request():
    from website_client.runtime_profile import OriginCaptures
    first, second = 'http://127.0.0.1:8101', 'http://127.0.0.1:8102'
    captures = OriginCaptures(first)
    captures.declare(second)
    captures.observe_request(Mock(url=first, all_headers=lambda: {'cookie': 'sid=OLD'}))
    captures.observe_response(first, ['sid=NEW; Path=/; HttpOnly'])
    assert ('sid', 'NEW') in captures.cookies_for(first)
    assert ('sid', 'NEW') not in captures.cookies_for(second)
    # A shared jar subsequently sending it elsewhere does not transfer issuance.
    captures.observe_request(Mock(url=second, all_headers=lambda: {'cookie': 'sid=NEW'}))
    assert ('sid', 'NEW') in captures.cookies_for(first)
    assert ('sid', 'NEW') not in captures.cookies_for(second)
    captures.observe_response(second, ['sid=NEW; Path=/; HttpOnly'])
    assert ('sid', 'NEW') not in captures.cookies_for(first)
    assert ('sid', 'NEW') not in captures.cookies_for(second)
