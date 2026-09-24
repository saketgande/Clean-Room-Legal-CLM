"""FastAPI-native dependency providers for the intake module.

Part of the DI migration (see backend/DI_MIGRATION.md). Covers intake/
Pass 1 (service.py), Pass 2 (teams.py, routing.py, screening.py, ingest.py,
gmail_sync.py), and Pass 4 (approval_bridge.py, drafting.py). Pass 3 (the
AI-agent chain: agents.py, gates.py, triage_agent.py, flow_agent.py,
litigation_agent.py, email_triage_agent.py) was assessed and deliberately
left as plain function modules — see the note in backend/DI_MIGRATION.md.
"""

from fastapi import Depends
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.intake.approval_bridge import ApprovalBridgeService
from app.intake.drafting import DraftingService
from app.intake.gmail_sync import GmailSyncService
from app.intake.ingest import IngestService
from app.intake.routing import RoutingService
from app.intake.screening import ScreeningService
from app.intake.service import IntakeService
from app.intake.teams import TeamService
from app.integrations.dependencies import get_reducto_client
from app.integrations.reducto import ReductoClient


def get_intake_service(db: Session = Depends(get_db)) -> IntakeService:
    return IntakeService(db)


def get_team_service(db: Session = Depends(get_db)) -> TeamService:
    return TeamService(db)


def get_routing_service(db: Session = Depends(get_db)) -> RoutingService:
    return RoutingService(db)


def get_screening_service(db: Session = Depends(get_db)) -> ScreeningService:
    return ScreeningService(db)


def get_ingest_service(db: Session = Depends(get_db)) -> IngestService:
    return IngestService(db)


def get_gmail_sync_service(
    db: Session = Depends(get_db),
    reducto: ReductoClient = Depends(get_reducto_client),
) -> GmailSyncService:
    return GmailSyncService(db, reducto=reducto)


def get_approval_bridge_service(db: Session = Depends(get_db)) -> ApprovalBridgeService:
    return ApprovalBridgeService(db)


def get_drafting_service(db: Session = Depends(get_db)) -> DraftingService:
    return DraftingService(db)
