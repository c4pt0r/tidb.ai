from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi_pagination import Params, Page
from fastapi_pagination.ext.sqlmodel import paginate
from sqlmodel import select, func

from app.api.deps import SessionDep, CurrentSuperuserDep
from app.models import (
    DataSource,
    DataSourceType,
    Document,
    Chunk,
)
from app.tasks import import_documents_from_datasource

router = APIRouter()


class DataSourceCreate(BaseModel):
    name: str
    description: str
    data_source_type: DataSourceType
    config: dict | list
    build_kg_index: bool = False


@router.post("/admin/datasources")
def create_datasource(
    session: SessionDep, user: CurrentSuperuserDep, request: DataSourceCreate
) -> DataSource:
    data_source = DataSource(
        name=request.name,
        description=request.description,
        data_source_type=request.data_source_type,
        config=request.config,
        build_kg_index=request.build_kg_index,
        user_id=user.id,
    )
    session.add(data_source)
    session.commit()
    session.refresh(data_source)
    import_documents_from_datasource.delay(data_source.id)
    return data_source


@router.get("/admin/datasources")
def list_datasources(
    session: SessionDep,
    user: CurrentSuperuserDep,
    params: Params = Depends(),
) -> Page[DataSource]:
    return paginate(
        session,
        select(DataSource).order_by(DataSource.created_at.desc()),
        params,
    )


@router.get("/admin/datasources/{data_source_id}")
def get_datasource(
    session: SessionDep,
    user: CurrentSuperuserDep,
    data_source_id: int,
) -> DataSource:
    data_source = session.get(DataSource, data_source_id)
    if data_source is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Data source not found",
        )
    return data_source


@router.get("/admin/datasources/{data_source_id}/overview")
def get_datasource_overview(
    session: SessionDep,
    user: CurrentSuperuserDep,
    data_source_id: int,
) -> dict:
    data_source = session.get(DataSource, data_source_id)
    if data_source is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Data source not found",
        )
    documents_count = session.scalar(
        select(func.count(Document.id)).where(Document.data_source_id == data_source_id)
    )
    chunks_count = session.scalar(
        select(func.count(Chunk.id)).where(
            Chunk.document.has(Document.data_source_id == data_source_id)
        )
    )

    statement = (
        select(Document.index_status, func.count(Document.id))
        .where(Document.data_source_id == data_source_id)
        .group_by(Document.index_status)
        .order_by(Document.index_status)
    )
    results = session.exec(statement).all()
    vector_index_status = {s: c for s, c in results}

    if data_source.build_kg_index:
        statement = (
            select(Chunk.index_status, func.count(Chunk.id))
            .where(Chunk.document.has(Document.data_source_id == data_source_id))
            .group_by(Chunk.index_status)
            .order_by(Chunk.index_status)
        )
        results = session.exec(statement).all()
        kg_index_status = {s: c for s, c in results}
    else:
        kg_index_status = {}

    return {
        "documents": {
            "total": documents_count,
        },
        "chunks": {
            "total": chunks_count,
        },
        "kg_index": kg_index_status,
        "vector_index": vector_index_status,
    }
