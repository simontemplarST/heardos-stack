import pytest
from heardos_installer.vault import Vault, WrongPassphrase, env_for
from heardos_installer.vault.migrate import import_credentials_env

def test_roundtrip_and_wrong_pass(tmp_path):
    v = Vault(tmp_path / "v.json"); v.create("correct horse"); v.update({"A": "1", "B": "two"})
    raw = (tmp_path / "v.json").read_text(); assert "two" not in raw
    w = Vault(tmp_path / "v.json"); w.unlock("correct horse"); assert w.get("B") == "two"
    with pytest.raises(WrongPassphrase): Vault(tmp_path / "v.json").unlock("nope")
    w.change_passphrase("new pass"); z = Vault(tmp_path / "v.json"); z.unlock("new pass"); assert z.names() == ["A", "B"]

def test_env_least_privilege_and_import(tmp_path):
    v = Vault(tmp_path / "v.json"); v.create("p"); v.update({"CF_API_TOKEN": "x" * 20, "OTHER": "y"})
    e = env_for({"DOMAIN": "example.com", "PUBLIC_HOSTS": ["auth", "photos"]}, v, ["CF_API_TOKEN"])
    assert e["PUBLIC_HOSTS"] == "auth photos" and "OTHER" not in e and e["CF_API_TOKEN"] == "x" * 20
    f = tmp_path / "c.env"; f.write_text("# c\nFOO=bar\nexport Q='quoted value'\nEMPTY=\n")
    assert import_credentials_env(v, f) == ["FOO", "Q"] and v.get("Q") == "quoted value"
