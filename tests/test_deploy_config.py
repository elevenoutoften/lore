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


def test_python_sdk_has_test_extra():
    """Regression: pip install -e .[test] must install pytest."""
    import tomllib

    sdk_pyproject = Path(__file__).resolve().parent.parent / "sdk" / "python" / "pyproject.toml"
    config = tomllib.loads(sdk_pyproject.read_text())
    opt_deps = config["project"].get("optional-dependencies", {})
    assert "test" in opt_deps, "sdk/python/pyproject.toml missing [project.optional-dependencies] test extra"
    test_deps = opt_deps["test"]
    assert any("pytest" in dep for dep in test_deps), "test extra must include pytest"


def test_typescript_sdk_readme_imports_match_package_name():
    """Regression: README imports must match the actual package name."""
    import json
    import re

    repo_root = Path(__file__).resolve().parent.parent
    pkg_json = json.loads((repo_root / "sdk" / "typescript" / "package.json").read_text())
    pkg_name = pkg_json["name"]

    readme = (repo_root / "sdk" / "typescript" / "README.md").read_text()
    imports = re.findall(r'from\s+"([^"]+)"', readme)
    sdk_imports = [i for i in imports if not i.startswith(".") and "node:" not in i]
    for imp in sdk_imports:
        assert imp == pkg_name, f"README imports '{imp}' but package.json names '{pkg_name}'"
