"""Route REST cho lịch sử cảnh báo, xác nhận và giải quyết."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, status

from src.api.models import AlarmListResponse, AlarmResponse

router = APIRouter()


def _to_response(r) -> AlarmResponse:
    return AlarmResponse(
        id=r.id,
        signal_name=r.signal_name,
        level=r.level,
        value=r.value,
        threshold=r.threshold,
        description=r.description,
        triggered_at=r.triggered_at,
        acknowledged=r.acknowledged,
        resolved_at=r.resolved_at,
    )


@router.get("", response_model=AlarmListResponse, summary="List alarm history")
async def list_alarms(
    request: Request,
    signal_name: str | None = Query(None),
    level: str | None = Query(None, pattern="^(info|warning|critical)$"),
    acknowledged: bool | None = Query(None),
    start: float | None = Query(None),
    end: float | None = Query(None),
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
):
    repo = request.app.state.repo
    records = await repo.query_alarms(
        signal_name=signal_name,
        level=level,
        acknowledged=acknowledged,
        start=start,
        end=end,
        limit=limit,
        offset=offset,
    )
    items = [_to_response(r) for r in records]
    return AlarmListResponse(items=items, total=len(items))


@router.get("/{alarm_id}", response_model=AlarmResponse, summary="Get single alarm")
async def get_alarm(alarm_id: int, request: Request):
    repo = request.app.state.repo
    alarm = await repo.get_alarm_by_id(alarm_id)
    if not alarm:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Alarm {alarm_id} not found"
        )
    return _to_response(alarm)


@router.post(
    "/{alarm_id}/acknowledge", status_code=status.HTTP_200_OK, summary="Acknowledge an alarm"
)
async def acknowledge_alarm(alarm_id: int, request: Request):
    repo = request.app.state.repo
    updated = await repo.acknowledge_alarm(alarm_id)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Already acknowledged or not found"
        )
    return {"alarm_id": alarm_id, "acknowledged": True}


@router.post("/{alarm_id}/resolve", status_code=status.HTTP_200_OK, summary="Resolve an alarm")
async def resolve_alarm(alarm_id: int, request: Request):
    repo = request.app.state.repo
    updated = await repo.resolve_alarm(alarm_id)
    if not updated:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Already resolved or not found"
        )
    return {"alarm_id": alarm_id, "resolved": True}
