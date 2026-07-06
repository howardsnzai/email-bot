import pytest

from jason.storage import LocalFileStore


@pytest.fixture
def store(tmp_path):
    return LocalFileStore(root=tmp_path / "memory")
