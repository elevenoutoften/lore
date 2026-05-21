from __future__ import annotations

from fastapi import APIRouter, Depends

from ..deps import get_repo
from ..frontmatter_spec import get_frontmatter_spec
from ..repository import LoreRepository
from ..schemas import CatalogResponse, FrontmatterSpecResponse

router = APIRouter()


@router.get("/api/catalog", response_model=CatalogResponse)
def api_catalog(repo: LoreRepository = Depends(get_repo)):
    return repo.catalog()


@router.get("/api/frontmatter/spec", response_model=FrontmatterSpecResponse)
def api_frontmatter_spec():
    return get_frontmatter_spec()
