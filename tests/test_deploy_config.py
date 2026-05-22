from pathlib import Path


def test_deploy_passes_auth_env_vars():
    service_path = Path(__file__).parent.parent / "deploy" / "axis-lore.service"
    content = service_path.read_text()
    for var in ["LORE_AUTH_MODE", "LORE_AUTH_SECRET", "LORE_API_KEYS_DB"]:
        assert f"-e {var}=${{{var}}}" in content, (
            f"Missing -e {var}=${{{var}}} in deploy/axis-lore.service"
        )


def test_release_gate_port_and_version_consistency():
    """Regression: CI Docker port, checklist port, and version name must match Dockerfile/pyproject."""
    import re
    import tomllib

    repo_root = Path(__file__).resolve().parent.parent

    dockerfile = (repo_root / "Dockerfile").read_text()
    exposed_ports = re.findall(r"EXPOSE\s+(\d+)", dockerfile)
    assert len(exposed_ports) == 1, f"Expected one EXPOSE in Dockerfile, found {exposed_ports}"
    docker_port = exposed_ports[0]

    ci_yml = (repo_root / ".github" / "workflows" / "ci.yml").read_text()
    ci_port_mappings = re.findall(r"-p\s+\d+:(\d+)", ci_yml)
    for container_port in ci_port_mappings:
        assert container_port == docker_port, (
            f"CI maps to container port {container_port} but Dockerfile EXPOSEs {docker_port}"
        )

    checklist = (repo_root / "docs" / "beta-release-checklist.md").read_text()
    checklist_docker_runs = re.findall(r"docker run.*-p\s+\d+:(\d+)", checklist)
    for container_port in checklist_docker_runs:
        assert container_port == docker_port, (
            f"Checklist maps to container port {container_port} but Dockerfile EXPOSEs {docker_port}"
        )

    pyproject = tomllib.loads((repo_root / "pyproject.toml").read_text())
    package_name = pyproject["project"]["name"]
    assert f"version('{package_name}')" in checklist or f'version("{package_name}")' in checklist, (
        f"Checklist version command does not use package name '{package_name}'"
    )
