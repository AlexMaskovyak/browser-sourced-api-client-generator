"""Private integration records must not expose values through ordinary repr/logging."""

from pathlib import Path

import pytest

from website_client.autonomous import RunRequest
from website_client.browser import BrowserError
from website_client.client_runtime import RuntimeConfig
from website_client.native_files import FileDescriptor
from website_client.workflow_admission import OperationBinding
from website_client.workflow_runtime import ExecutionSummary, WorkflowPlan


CANARY = 'CREDENTIAL_CANARY_PRIVATE'


def test_private_integration_records_hide_native_values_and_locations(tmp_path):
    native = {'example': CANARY}
    operation = OperationBinding(CANARY, native, '/' + CANARY, native, 'POST', native, 'https://fixture.invalid')
    records = [
        operation,
        FileDescriptor(Path(CANARY), CANARY, CANARY),
        RunRequest(CANARY, tmp_path / 'output', inputs=(CANARY,), endpoint=CANARY),
        WorkflowPlan(native, {'read': operation}, native, {CANARY: CANARY}),
        ExecutionSummary(CANARY, outputs=native),
        RuntimeConfig('openapi', 'https://fixture.invalid/' + CANARY, CANARY),
    ]
    assert all(CANARY not in repr(record) for record in records)


def test_generation_options_reuse_exploration_bounds_before_any_login(tmp_path):
    with pytest.raises(BrowserError, match='Exploration requires'):
        RunRequest('https://fixture.invalid', tmp_path / 'output', max_pages=101)


def test_file_descriptor_rejects_unsafe_metadata_before_opening_any_file():
    with pytest.raises(ValueError, match='metadata'):
        FileDescriptor.parse({'path': 'does-not-exist', 'filename': 'bad\r\nname'})


@pytest.mark.parametrize('value', [
    {'kind': []},
    {'kind': 'unknown'},
    {'kind': 'openapi', 'endpoint': 'https://user:secret@fixture.invalid'},
    {'kind': 'openapi', 'profileId': 'outside/profile'},
    {'kind': 'openapi', 'token': CANARY},
])
def test_runtime_configuration_rejects_invalid_defaults_without_opening_state(value):
    with pytest.raises(ValueError) as failure:
        RuntimeConfig.from_mapping(value)
    assert CANARY not in str(failure.value)
