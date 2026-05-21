from __future__ import annotations

from fastapi.testclient import TestClient

from lore_app.config import LoreConfig, WorkspaceConfig
from lore_app.main import create_app


def write_page(root, page_id: str, title: str) -> None:
    path = root / f"{page_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntitle: {title}\nkind: page\nvisibility: internal\n---\n\n# {title}\n",
        encoding="utf-8",
    )


def test_workspace_prefix_uses_separate_content_root(tmp_path):
    default_root = tmp_path / "default-pages"
    team_root = tmp_path / "team-pages"
    write_page(default_root, "shared/default", "Default Page")
    write_page(team_root, "shared/team", "Team Page")

    config = LoreConfig()
    config.content_dir = default_root
    config.search_db = tmp_path / "default-search.db"
    config.vector_db = tmp_path / "default-vector.db"
    config.workspaces = {
        "team": WorkspaceConfig(
            content_dir=team_root,
            search_db=tmp_path / "team-search.db",
            vector_db=tmp_path / "team-vector.db",
        )
    }
    app = create_app(config)

    with TestClient(app) as client:
        default_pages = client.get("/api/pages")
        team_pages = client.get("/team/api/pages")
        default_missing = client.get("/api/pages/shared/team")
        team_missing = client.get("/team/api/pages/shared/default")

    assert default_pages.status_code == 200
    assert [page["id"] for page in default_pages.json()] == ["shared/default"]
    assert team_pages.status_code == 200
    assert [page["id"] for page in team_pages.json()] == ["shared/team"]
    assert default_missing.status_code == 404
    assert team_missing.status_code == 404


def test_workspace_defaults_to_main_db_paths(tmp_path):
    default_root = tmp_path / "default-pages"
    team_root = tmp_path / "team-pages"
    write_page(default_root, "default", "Default")
    write_page(team_root, "team", "Team")

    config = LoreConfig()
    config.content_dir = default_root
    config.search_db = tmp_path / "search.db"
    config.vector_db = tmp_path / "vector.db"
    config.workspaces = {"team": WorkspaceConfig(content_dir=team_root)}

    app = create_app(config)
    with TestClient(app) as client:
        response = client.get("/team/api/pages")

    assert response.status_code == 200
    assert response.json()[0]["id"] == "team"
