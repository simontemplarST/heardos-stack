import os, sys, pathlib, pytest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

@pytest.fixture(autouse=True)
def heardos_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HEARDOS_HOME", str(tmp_path / "home"))
    yield tmp_path / "home"
