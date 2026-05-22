from pathlib import Path


def test_deploy_passes_auth_env_vars():
    service_path = Path(__file__).parent.parent / "deploy" / "axis-lore.service"
    content = service_path.read_text()
    for var in ["LORE_AUTH_MODE", "LORE_AUTH_SECRET", "LORE_API_KEYS_DB"]:
        assert f"-e {var}=${{{var}}}" in content, (
            f"Missing -e {var}=${{{var}}} in deploy/axis-lore.service"
        )
