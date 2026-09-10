import grpc
import pytest

from pylibinfra.grpckit.status import is_server_fault

# Ровно тот набор, что `internal/grpcstatus.IsServerFault` в golibinfra: только эти
# коды пишутся на error, остальные не-OK исходы — ожидаемые, они идут на warn.
SERVER_FAULTS = frozenset(
    {
        grpc.StatusCode.INTERNAL,
        grpc.StatusCode.UNKNOWN,
        grpc.StatusCode.DATA_LOSS,
        grpc.StatusCode.UNAVAILABLE,
        grpc.StatusCode.UNIMPLEMENTED,
    }
)


@pytest.mark.parametrize("code", list(grpc.StatusCode))
def test_taxonomy_matches_golibinfra(code: grpc.StatusCode) -> None:
    assert is_server_fault(code) is (code in SERVER_FAULTS)


def test_ok_is_not_a_fault() -> None:
    assert not is_server_fault(grpc.StatusCode.OK)
